from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .orm import Base


class SchemaOutOfDate(RuntimeError):
    """The database on disk does not match the ORM models."""


def create_db_engine(database: str | Path) -> Engine:
    if str(database) == ":memory:":
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        path = Path(database)
        path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
        )

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_db(engine: Engine, *, reset: bool = False) -> None:
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def verify_schema(engine: Engine) -> None:
    """Fail loudly when the on-disk schema drifts from the ORM models.

    ``create_all`` only creates missing tables, it never alters existing ones,
    so a model change would otherwise surface as a confusing "no such column"
    error much later. Alembic migrations replace this check once they exist.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    problems: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
        missing = {column.name for column in table.columns} - existing_columns
        if missing:
            problems.append(f"{table.name}: missing columns {sorted(missing)}")

    if problems:
        raise SchemaOutOfDate(
            "database schema is older than the models ("
            + "; ".join(problems)
            + "). Re-create it with: mtb-pipeline init-db --reset"
        )


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        yield session
