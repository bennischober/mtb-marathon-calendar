import json
from datetime import UTC, datetime
from pathlib import Path

import typer
from sqlalchemy.exc import SQLAlchemyError

from .collect import collect, summarize
from .database import (
    SchemaOutOfDate,
    create_db_engine,
    init_db,
    session_scope,
    verify_schema,
)
from .http_client import DEFAULT_USER_AGENT, Fetcher, FetchError, RobotsDisallowed
from .io import load_editions, write_export
from .repository import load_editions as load_editions_from_db
from .repository import upsert_editions
from .runs import RunOutcome, record_run, source_health
from .sources import CollectOptions, all_profiles, available_sources, get_adapter
from .sources.radsport_events import SUPPORTED_COUNTRIES

DEFAULT_DATABASE = Path("data/mtb.sqlite3")
DEFAULT_EXPORT = Path("data/generated/events.json")
DEFAULT_RAW_DIR = Path("raw")

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


@app.command("collect")
def collect_command(
    source: str = typer.Option(
        ...,
        "--source",
        help=f"Source adapter to run. Available: {', '.join(available_sources())}",
    ),
    database: Path | None = typer.Option(
        None,
        "--database",
        help="SQLite database path; defaults to data/mtb.sqlite3.",
    ),
    raw_dir: Path = typer.Option(
        DEFAULT_RAW_DIR,
        "--raw-dir",
        help="Directory for raw source snapshots.",
    ),
    country: list[str] = typer.Option(
        list(SUPPORTED_COUNTRIES),
        "--country",
        help="Restrict to these ISO country codes. Repeatable.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Fetch and parse without writing to the database.",
    ),
    user_agent: str = typer.Option(
        DEFAULT_USER_AGENT,
        "--user-agent",
        help="User agent sent to the source. Please set a real contact address.",
    ),
    delay_seconds: float = typer.Option(
        1.0,
        "--delay",
        min=0.0,
        help="Minimum delay between HTTP requests, in seconds.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Stop after roughly this many source records.",
    ),
) -> None:
    countries = tuple(code.upper() for code in country)
    unknown = set(countries) - set(SUPPORTED_COUNTRIES)
    if unknown:
        typer.echo(
            f"Unsupported countries: {', '.join(sorted(unknown))}. "
            f"Scope is {', '.join(SUPPORTED_COUNTRIES)}.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        adapter = get_adapter(source)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    started_at = datetime.now(UTC)

    def record_success(result) -> None:
        if dry_run:
            return
        _persist(  # noqa: ANN001
            database or DEFAULT_DATABASE,
            editions=result.editions,
            run=_RunToRecord(
                adapter=adapter.name,
                started_at=started_at,
                requests=result.requests,
                documents=result.documents,
                parsed=result.parsed,
                kept=result.kept,
                excluded=result.excluded,
                candidates=result.candidates,
            ),
        )

    def record_failure(error: Exception) -> None:
        if dry_run:
            return
        # A failed run is still worth recording, otherwise a source that broke
        # last week looks identical to one that was never attempted.
        try:
            _persist(
                database or DEFAULT_DATABASE,
                editions=[],
                run=_RunToRecord(
                    adapter=adapter.name,
                    started_at=started_at,
                    error=str(error),
                ),
                only_run=True,
            )
        except (OSError, SQLAlchemyError, SchemaOutOfDate):
            # Never let bookkeeping hide the original collection error.
            pass

    try:
        with Fetcher(
            user_agent=user_agent,
            delay_seconds=delay_seconds,
            raw_dir=raw_dir,
        ) as fetcher:
            result = collect(
                adapter,
                countries=countries,
                options=CollectOptions(limit=limit),
                fetcher=fetcher,
                on_finished=record_success,
                on_error=record_failure,
            )
    except RobotsDisallowed as exc:
        # `on_error` already recorded the failed run.
        typer.echo(f"Collection stopped: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except (FetchError, OSError, ValueError, SQLAlchemyError, SchemaOutOfDate) as exc:
        typer.echo(f"Collection failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(summarize(result))

    if dry_run:
        typer.echo("Dry run: nothing written to the database.")
        return

    database_path = database or DEFAULT_DATABASE
    typer.echo(f"Stored {len(result.editions)} event editions in {database_path}.")


class _RunToRecord:
    """Bundle of run counters, written together with the editions."""

    def __init__(
        self,
        *,
        adapter: str,
        started_at: datetime,
        error: str | None = None,
        requests: int = 0,
        documents: int = 0,
        parsed: int = 0,
        kept: int = 0,
        excluded: int = 0,
        candidates: int = 0,
    ) -> None:
        self.adapter = adapter
        self.started_at = started_at
        self.outcome = RunOutcome(
            ok=error is None,
            error=error,
            requests=requests,
            documents=documents,
            parsed=parsed,
            kept=kept,
            excluded=excluded,
            candidates=candidates,
        )


def _persist(
    database_path: Path,
    *,
    editions: list,
    run: _RunToRecord,
    only_run: bool = False,
) -> None:
    """Write editions and the run record in a single transaction."""
    engine = create_db_engine(database_path)
    init_db(engine)
    verify_schema(engine)
    try:
        with session_scope(engine) as session:
            with session.begin():
                if not only_run:
                    upsert_editions(session, editions)
                record_run(session, run.adapter, started_at=run.started_at, outcome=run.outcome)
    finally:
        engine.dispose()


@app.command()
def status(
    database: Path = typer.Option(
        DEFAULT_DATABASE,
        "--database",
        help="SQLite database path.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON instead of a table.",
    ),
    now: str | None = typer.Option(
        None,
        "--now",
        help="ISO timestamp used as the evaluation clock; defaults to the current time.",
    ),
) -> None:
    """Report registry metadata and observed health for every source."""
    try:
        engine = create_db_engine(database)
        init_db(engine)
        verify_schema(engine)
        try:
            with session_scope(engine) as session:
                report = source_health(session, all_profiles(), now=parse_generated_at(now))
        finally:
            engine.dispose()
    except (OSError, SQLAlchemyError, SchemaOutOfDate) as exc:
        typer.echo(f"Status failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(json.dumps([item.row() for item in report], indent=2, default=str))
        return

    typer.echo(
        f"{'SOURCE':22} {'STATE':10} {'PRIORITY':22} {'RUNS':>5} {'FAIL%':>6} "
        f"{'AGE_H':>7} {'KEPT':>5} {'CADENCE':>8}"
    )
    for item in report:
        row = item.row()
        failure = "n/a" if row["failure_rate"] is None else f"{row['failure_rate'] * 100:.0f}"
        age = "n/a" if row["age_hours"] is None else f"{row['age_hours']:.1f}"
        kept = "n/a" if row["kept_last"] is None else str(row["kept_last"])
        typer.echo(
            f"{row['source']:22} {row['state']:10} {row['priority']:22} "
            f"{row['runs']:>5} {failure:>6} {age:>7} {kept:>5} "
            f"{row['cadence_hours']:>7}h"
        )
        if row["state"] in {"failing", "stale"} or row["terms"] != "confirmed":
            typer.echo(f"{'':22} terms={row['terms']} robots={row['parser']}")


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
