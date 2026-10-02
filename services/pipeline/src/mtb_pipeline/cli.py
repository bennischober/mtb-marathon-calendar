from datetime import datetime
from pathlib import Path

import typer
from sqlalchemy.exc import SQLAlchemyError

from .database import (
    SchemaOutOfDate,
    create_db_engine,
    init_db,
    session_scope,
    verify_schema,
)
from .io import load_editions, write_export
from .repository import load_editions as load_editions_from_db
from .repository import upsert_editions

DEFAULT_DATABASE = Path("data/mtb.sqlite3")
DEFAULT_EXPORT = Path("data/generated/events.json")

app = typer.Typer(no_args_is_help=True, help="Collect, validate, and export MTB marathon events.")


def parse_generated_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def read_database(database: Path):
    engine = create_db_engine(database)
    init_db(engine)
    verify_schema(engine)
    try:
        with session_scope(engine) as session:
            return load_editions_from_db(session)
    finally:
        engine.dispose()


@app.command()
def validate(
    input_path: Path = typer.Option(
        ...,
        "--input",
        exists=True,
        readable=True,
        help="JSON file containing event editions.",
    ),
) -> None:
    try:
        editions = load_editions(input_path)
    except (OSError, ValueError) as exc:
        typer.echo(f"Validation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Validated {len(editions)} event editions.")


@app.command("init-db")
def init_db_command(
    database: Path = typer.Option(
        DEFAULT_DATABASE,
        "--database",
        help="SQLite database path.",
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Drop existing tables first. Required after ORM model changes.",
    ),
) -> None:
    try:
        engine = create_db_engine(database)
        init_db(engine, reset=reset)
        verify_schema(engine)
        engine.dispose()
    except (OSError, SQLAlchemyError, SchemaOutOfDate) as exc:
        typer.echo(f"Database initialization failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    action = "Re-initialized" if reset else "Initialized"
    typer.echo(f"{action} SQLite database at {database}.")


@app.command()
def ingest(
    input_path: Path = typer.Option(
        ...,
        "--input",
        exists=True,
        readable=True,
        help="JSON file containing event editions.",
    ),
    database: Path = typer.Option(
        DEFAULT_DATABASE,
        "--database",
        help="SQLite database path.",
    ),
) -> None:
    try:
        editions = load_editions(input_path)
        engine = create_db_engine(database)
        init_db(engine)
        verify_schema(engine)
        try:
            with session_scope(engine) as session:
                with session.begin():
                    upsert_editions(session, editions)
        finally:
            engine.dispose()
    except (OSError, ValueError, SQLAlchemyError, SchemaOutOfDate) as exc:
        typer.echo(f"Ingest failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Ingested {len(editions)} event editions into {database}.")


@app.command()
def export(
    input_path: Path | None = typer.Option(
        None,
        "--input",
        help="Optional JSON input for fixture-based export.",
    ),
    database: Path | None = typer.Option(
        None,
        "--database",
        help="SQLite database path; defaults to data/mtb.sqlite3.",
    ),
    output_path: Path = typer.Option(
        DEFAULT_EXPORT,
        "--output",
        help="Destination for the site-facing JSON artifact.",
    ),
    generated_at: str | None = typer.Option(
        None,
        "--generated-at",
        help="Optional ISO timestamp used for reproducible exports.",
    ),
) -> None:
    if input_path is not None and database is not None:
        typer.echo("Use either --input or --database, not both.", err=True)
        raise typer.Exit(code=1)

    try:
        if input_path is not None:
            editions = load_editions(input_path)
        else:
            database_path = database or DEFAULT_DATABASE
            if str(database_path) != ":memory:" and not database_path.exists():
                raise ValueError(
                    f"database does not exist: {database_path}; run ingest or init-db first"
                )
            editions = read_database(database_path)

        payload = write_export(
            editions,
            output_path,
            generated_at=parse_generated_at(generated_at),
        )
    except (OSError, ValueError, SQLAlchemyError, SchemaOutOfDate) as exc:
        typer.echo(f"Export failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Exported {len(payload['events'])} event editions to {output_path}.")


if __name__ == "__main__":
    app()
