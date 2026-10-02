"""Run-history persistence and the `status` report.

These tests drive the real CLI with a stub source so the wiring between
collection, the `collection_runs` table and the health report is covered
without touching the network.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mtb_pipeline import sources
from mtb_pipeline.cli import app
from mtb_pipeline.database import create_db_engine, init_db, session_scope
from mtb_pipeline.health import FAILING, NEVER_RUN, OK, STALE
from mtb_pipeline.io import load_editions
from mtb_pipeline.runs import RunOutcome, record_run, source_health
from mtb_pipeline.sources.base import SourceAdapter, SourceProfile

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
FIXTURE = "tests/fixtures/events.json"


class StubAdapter(SourceAdapter):
    profile = SourceProfile(
        name="stub-health",
        homepage="https://stub.example",
        priority="event_platform",
        countries=("DE",),
        cadence_hours=24,
        data_format="json",
        parser_status="verified",
        terms_status="confirmed",
    )


@pytest.fixture
def registered_stub(monkeypatch):
    """Register the stub adapter so `collect --source` can find it."""
    monkeypatch.setitem(sources._ADAPTERS, StubAdapter.profile.name, StubAdapter)
    return StubAdapter


def add_run(database, *, ok=True, hours_ago=1.0, kept=3, error=None):
    engine = create_db_engine(database)
    init_db(engine)
    try:
        with session_scope(engine) as session:
            with session.begin():
                record_run(
                    session,
                    "stub-health",
                    started_at=NOW - timedelta(hours=hours_ago),
                    finished_at=NOW - timedelta(hours=hours_ago),
                    outcome=RunOutcome(ok=ok, kept=kept, error=error),
                )
    finally:
        engine.dispose()


def test_a_run_is_recorded_with_its_counters(tmp_path):
    database = tmp_path / "runs.sqlite3"

    add_run(database, ok=True, kept=7)

    engine = create_db_engine(database)
    try:
        with session_scope(engine) as session:
            report = source_health(session, [StubAdapter.profile], now=NOW)
    finally:
        engine.dispose()

    assert report[0].runs == 1
    assert report[0].kept_last == 7
    assert report[0].state == OK


def test_failed_runs_are_recorded_not_swallowed(tmp_path):
    database = tmp_path / "runs.sqlite3"

    add_run(database, ok=False, kept=0, error="403 Forbidden")
    add_run(database, ok=False, kept=0, error="timeout")

    engine = create_db_engine(database)
    try:
        with session_scope(engine) as session:
            report = source_health(session, [StubAdapter.profile], now=NOW)
    finally:
        engine.dispose()

    assert report[0].state == FAILING
    assert report[0].consecutive_failures == 2
    assert report[0].failure_rate == 1.0


def test_run_history_is_append_only(tmp_path):
    """A later failure must not erase the record of an earlier success."""
    database = tmp_path / "runs.sqlite3"

    add_run(database, ok=True, hours_ago=5, kept=9)
    add_run(database, ok=False, hours_ago=1)

    engine = create_db_engine(database)
    try:
        with session_scope(engine) as session:
            report = source_health(session, [StubAdapter.profile], now=NOW)
    finally:
        engine.dispose()

    assert report[0].runs == 2
    assert report[0].successes == 1
    assert report[0].last_success_at == NOW - timedelta(hours=5)


def test_status_reports_a_never_run_source(tmp_path):
    database = tmp_path / "status.sqlite3"
    runner = CliRunner()

    result = runner.invoke(app, ["status", "--database", str(database)])

    assert result.exit_code == 0
    assert "radsport-events" in result.stdout
    assert NEVER_RUN in result.stdout


def test_status_json_includes_registry_metadata(tmp_path, registered_stub):
    database = tmp_path / "status.sqlite3"
    add_run(database, ok=True, kept=4)

    result = CliRunner().invoke(
        app, ["status", "--database", str(database), "--json", "--now", NOW.isoformat()]
    )

    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    by_name = {row["source"]: row for row in rows}

    assert by_name["radsport-events"]["priority"] == "specialist_aggregator"
    assert by_name["radsport-events"]["cadence_hours"] == 24
    assert by_name["stub-health"]["kept_last"] == 4


def test_status_marks_a_stale_source(tmp_path, registered_stub):
    database = tmp_path / "status.sqlite3"
    add_run(database, ok=True, hours_ago=72)

    result = CliRunner().invoke(
        app, ["status", "--database", str(database), "--json", "--now", NOW.isoformat()]
    )

    rows = {row["source"]: row for row in json.loads(result.stdout)}

    assert rows["stub-health"]["state"] == STALE
    assert rows["stub-health"]["stale"] is True


def test_status_surfaces_unconfirmed_terms(tmp_path):
    """Terms are not settled for radsport-events, so the report must say so."""
    database = tmp_path / "status.sqlite3"

    result = CliRunner().invoke(app, ["status", "--database", str(database)])

    assert "terms=unconfirmed" in result.stdout


def test_status_lists_every_registered_source(tmp_path):
    database = tmp_path / "status.sqlite3"

    result = CliRunner().invoke(app, ["status", "--database", str(database), "--json"])

    rows = json.loads(result.stdout)

    assert {row["source"] for row in rows} == set(sources.available_sources())


def test_collect_records_a_failed_run_when_the_source_breaks(registered_stub, tmp_path):
    """A source that breaks must not look identical to one never attempted."""
    database = tmp_path / "failed.sqlite3"

    class BrokenAdapter(registered_stub):
        def fetch(self, fetcher, options):  # noqa: ANN001, ANN202, ARG002
            raise OSError("connection reset by peer")

        def parse(self, documents):  # noqa: ANN001, ANN201, ARG002
            return []

    sources._ADAPTERS[BrokenAdapter.profile.name] = BrokenAdapter

    result = CliRunner().invoke(
        app,
        ["collect", "--source", "stub-health", "--database", str(database)],
    )

    assert result.exit_code == 1
    assert "connection reset" in result.stderr

    engine = create_db_engine(database)
    try:
        with session_scope(engine) as session:
            report = source_health(session, [BrokenAdapter.profile], now=datetime.now(UTC))
    finally:
        engine.dispose()

    assert report[0].runs == 1
    assert report[0].successes == 0
    assert report[0].last_ok is False


def test_collect_records_a_successful_run_with_its_counts(registered_stub, tmp_path):
    database = tmp_path / "ok.sqlite3"

    class WorkingAdapter(registered_stub):
        def fetch(self, fetcher, options):  # noqa: ANN001, ANN202, ARG002
            return []

        def parse(self, documents):  # noqa: ANN001, ANN201, ARG002
            return load_editions(Path(FIXTURE))

    sources._ADAPTERS[WorkingAdapter.profile.name] = WorkingAdapter

    result = CliRunner().invoke(
        app,
        ["collect", "--source", "stub-health", "--database", str(database), "--limit", "5"],
    )

    assert result.exit_code == 0, result.stderr

    engine = create_db_engine(database)
    try:
        with session_scope(engine) as session:
            report = source_health(session, [WorkingAdapter.profile], now=datetime.now(UTC))
    finally:
        engine.dispose()

    assert report[0].runs == 1
    assert report[0].kept_last == 2
