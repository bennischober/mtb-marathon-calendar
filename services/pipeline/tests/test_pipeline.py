import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect
from typer.testing import CliRunner

from mtb_pipeline.cli import app
from mtb_pipeline.database import (
    SchemaOutOfDate,
    create_db_engine,
    init_db,
    session_scope,
    verify_schema,
)
from mtb_pipeline.io import build_export, load_editions
from mtb_pipeline.repository import load_editions as load_editions_from_db
from mtb_pipeline.repository import upsert_editions

FIXTURE = Path(__file__).parent / "fixtures" / "events.json"


def test_fixture_is_valid():
    editions = load_editions(FIXTURE)

    assert len(editions) == 2
    assert editions[0].format == "stage_race"
    assert editions[0].duration_days == 5


def test_export_sorts_events_and_keeps_metadata(tmp_path):
    editions = load_editions(FIXTURE)
    output = tmp_path / "events.json"

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "export",
            "--input",
            str(FIXTURE),
            "--output",
            str(output),
            "--generated-at",
            "2026-03-01T12:00:00+00:00",
        ],
    )

    assert result.exit_code == 0
    assert output.exists()
    exported = build_export(editions)
    assert exported["events"][0]["date_start"] == "2027-06-14"


def test_validate_command_accepts_fixture():
    runner = CliRunner()
    result = runner.invoke(app, ["validate", "--input", str(FIXTURE)])

    assert result.exit_code == 0
    assert "Validated 2 event editions." in result.stdout


def test_build_export_without_generated_at_uses_utc_now():
    exported = build_export(load_editions(FIXTURE))

    generated_at = datetime.fromisoformat(exported["generated_at"])
    assert generated_at.tzinfo is not None
    assert abs((datetime.now(UTC) - generated_at).total_seconds()) < 60


def test_build_export_omits_empty_extraction_warnings():
    exported = build_export(load_editions(FIXTURE))

    assert all("extraction_warnings" not in event for event in exported["events"])


def test_sqlite_round_trip_is_identical(tmp_path):
    editions = load_editions(FIXTURE)
    engine = create_db_engine(tmp_path / "events.sqlite3")
    init_db(engine)

    with session_scope(engine) as session:
        with session.begin():
            upsert_editions(session, editions)
    with session_scope(engine) as session:
        loaded = load_editions_from_db(session)
    engine.dispose()

    generated_at = datetime(2026, 3, 1, 12, tzinfo=UTC)
    assert loaded == editions
    assert build_export(loaded, generated_at) == build_export(editions, generated_at)


def test_ingest_is_idempotent(tmp_path):
    editions = load_editions(FIXTURE)
    engine = create_db_engine(tmp_path / "events.sqlite3")
    init_db(engine)

    for _ in range(2):
        with session_scope(engine) as session:
            with session.begin():
                upsert_editions(session, editions)
    with session_scope(engine) as session:
        loaded = load_editions_from_db(session)
    engine.dispose()

    assert loaded == editions


def test_database_export_matches_fixture_export(tmp_path):
    database = tmp_path / "events.sqlite3"
    from_database = tmp_path / "from-database.json"
    from_fixture = tmp_path / "from-fixture.json"
    generated_at = "2026-03-01T12:00:00+00:00"
    runner = CliRunner()

    ingest_result = runner.invoke(
        app,
        ["ingest", "--input", str(FIXTURE), "--database", str(database)],
    )
    assert ingest_result.exit_code == 0, ingest_result.stdout

    export_result = runner.invoke(
        app,
        [
            "export",
            "--database",
            str(database),
            "--output",
            str(from_database),
            "--generated-at",
            generated_at,
        ],
    )
    fixture_result = runner.invoke(
        app,
        [
            "export",
            "--input",
            str(FIXTURE),
            "--output",
            str(from_fixture),
            "--generated-at",
            generated_at,
        ],
    )

    assert export_result.exit_code == 0, export_result.stdout
    assert fixture_result.exit_code == 0, fixture_result.stdout
    assert from_database.read_text(encoding="utf-8") == from_fixture.read_text(encoding="utf-8")


def test_export_rejects_input_and_database_together(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "export",
            "--input",
            str(FIXTURE),
            "--database",
            str(tmp_path / "events.sqlite3"),
            "--output",
            str(tmp_path / "events.json"),
        ],
    )

    assert result.exit_code == 1
    assert "not both" in result.stderr


def test_export_fails_when_database_missing(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "export",
            "--database",
            str(tmp_path / "missing.sqlite3"),
            "--output",
            str(tmp_path / "events.json"),
        ],
    )

    assert result.exit_code == 1
    assert "run ingest or init-db first" in result.stderr


def test_init_db_creates_tables(tmp_path):
    database = tmp_path / "events.sqlite3"
    runner = CliRunner()
    result = runner.invoke(app, ["init-db", "--database", str(database)])

    assert result.exit_code == 0
    engine = create_db_engine(database)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()

    assert tables == {
        "course_variant_sources",
        "course_variants",
        "edition_sources",
        "editions",
        "events",
        "sources",
    }


def test_verify_schema_detects_missing_columns(tmp_path):
    database = tmp_path / "events.sqlite3"
    engine = create_db_engine(database)
    init_db(engine)

    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE sources DROP COLUMN adapter")

    with pytest.raises(SchemaOutOfDate, match="missing columns"):
        verify_schema(engine)
    engine.dispose()


def test_init_db_reset_repairs_outdated_schema(tmp_path):
    database = tmp_path / "events.sqlite3"
    runner = CliRunner()
    assert runner.invoke(app, ["init-db", "--database", str(database)]).exit_code == 0

    engine = create_db_engine(database)
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE sources DROP COLUMN adapter")
    engine.dispose()

    stale = runner.invoke(
        app,
        ["export", "--database", str(database), "--output", str(tmp_path / "out.json")],
    )
    assert stale.exit_code == 1
    assert "init-db --reset" in stale.stderr

    repaired = runner.invoke(app, ["init-db", "--database", str(database), "--reset"])
    assert repaired.exit_code == 0

    healthy = runner.invoke(
        app,
        ["export", "--database", str(database), "--output", str(tmp_path / "out.json")],
    )
    assert healthy.exit_code == 0
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))["events"] == []


def test_generated_export_can_be_reloaded_and_reingested(tmp_path):
    """`export` output must round-trip, otherwise the site artifact is a dead end."""
    from mtb_pipeline.io import build_export, load_editions, write_export

    export_path = tmp_path / "events.json"
    payload = build_export(load_editions(FIXTURE), generated_at=datetime(2026, 10, 2, tzinfo=UTC))

    write_export(
        load_editions(FIXTURE), export_path, generated_at=datetime(2026, 10, 2, tzinfo=UTC)
    )

    assert json.loads(export_path.read_text()) == payload
    assert len(load_editions(export_path)) == len(load_editions(FIXTURE))


def test_ingest_accepts_a_generated_export(tmp_path):
    database = tmp_path / "roundtrip.sqlite3"
    export_path = tmp_path / "events.json"

    export_result = CliRunner().invoke(
        app, ["export", "--input", str(FIXTURE), "--output", str(export_path)]
    )
    init_result = CliRunner().invoke(app, ["init-db", "--database", str(database)])
    ingest_result = CliRunner().invoke(
        app, ["ingest", "--database", str(database), "--input", str(export_path)]
    )

    assert export_result.exit_code == 0
    assert init_result.exit_code == 0
    assert ingest_result.exit_code == 0, ingest_result.stderr
    engine = create_db_engine(database)
    with session_scope(engine) as session:
        reloaded = load_editions_from_db(session)
    engine.dispose()

    assert reloaded == load_editions(FIXTURE)


def test_load_editions_rejects_an_unrecognised_shape(tmp_path):
    from mtb_pipeline.io import load_editions

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")

    with pytest.raises(ValueError, match="JSON array of editions or an export object"):
        load_editions(bad)
