"""Durable merge-conflict storage and the `conflicts` command.

The merge resolves every disagreement on its own, so these tests care about the
narrower guarantee: what disagreed is recorded where it can still be inspected
after the export log has scrolled away.
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mtb_pipeline.cli import app
from mtb_pipeline.conflicts import load_merge_conflicts, record_merge_conflicts
from mtb_pipeline.database import create_db_engine, init_db, session_scope
from mtb_pipeline.io import load_editions
from mtb_pipeline.repository import upsert_editions

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
FIXTURE = Path("services/pipeline/tests/fixtures/events.json")
MARATHON = "example-mtb-marathon-2027"
EPIC = "example-mtb-epic-2027"

CONFLICTS = [
    {
        "edition_id": MARATHON,
        "field": "official_url",
        "kept": "https://federation.example/calendar.pdf",
        "kept_source": "bawue-radsportverband",
        "discarded": {"radsport-events": "https://platform.example/e/1"},
        "reason": "lower-priority source disagreed",
    },
    {
        "edition_id": MARATHON,
        "field": "location.region",
        "kept": "Süd",
        "kept_source": "bawue-radsportverband",
        "discarded": {"radsport-events": "Schönwürttemberg"},
        "reason": "locations differ; kept the higher-priority record",
    },
]


@pytest.fixture
def database(tmp_path):
    """A database holding the fixture editions.

    Conflicts reference an edition, so the rows have to exist first; otherwise
    the foreign key would reject every insert and the tests would pass for the
    wrong reason.
    """
    path = tmp_path / "conflicts.sqlite3"
    engine = create_db_engine(path)
    init_db(engine)
    try:
        with session_scope(engine) as session:
            with session.begin():
                upsert_editions(session, load_editions(FIXTURE))
    finally:
        engine.dispose()
    return path


def store(database, conflicts, *, edition_ids, recorded_at=NOW):
    engine = create_db_engine(database)
    init_db(engine)
    try:
        with session_scope(engine) as session:
            with session.begin():
                return record_merge_conflicts(
                    session, conflicts, edition_ids=edition_ids, recorded_at=recorded_at
                )
    finally:
        engine.dispose()


def load(database, **kwargs):
    engine = create_db_engine(database)
    try:
        with session_scope(engine) as session:
            return load_merge_conflicts(session, **kwargs)
    finally:
        engine.dispose()


def test_a_conflict_is_stored_with_the_value_that_won(database):
    stored = store(database, CONFLICTS, edition_ids=[MARATHON])

    rows = load(database)
    assert stored == 2
    assert {row.field for row in rows} == {"official_url", "location.region"}
    official = next(row for row in rows if row.field == "official_url")
    assert official.edition_id == MARATHON
    assert official.kept == "https://federation.example/calendar.pdf"
    assert official.kept_source == "bawue-radsportverband"
    assert official.discarded == {"radsport-events": "https://platform.example/e/1"}
    assert official.reason == "lower-priority source disagreed"
    assert official.recorded_at == NOW.isoformat()


def test_conflicts_are_replaced_rather_than_accumulated(database):
    store(database, CONFLICTS, edition_ids=[MARATHON])

    # The region disagreement is resolved upstream. The next export must not
    # keep accusing the sources about a field they now agree on.
    store(database, CONFLICTS[:1], edition_ids=[MARATHON])

    assert [row.field for row in load(database)] == ["official_url"]


def test_a_partial_export_leaves_other_editions_untouched(database):
    store(database, CONFLICTS, edition_ids=[MARATHON])
    epic_conflict = [{**CONFLICTS[0], "edition_id": EPIC}]

    # Re-exporting the marathon must not erase what is recorded for the epic.
    store(database, [], edition_ids=[MARATHON])
    store(database, epic_conflict, edition_ids=[EPIC])

    assert [row.edition_id for row in load(database)] == [EPIC]


def test_values_that_json_cannot_hold_are_stored_as_text(database):
    dates_as_values = [
        {
            **CONFLICTS[0],
            "kept": date(2027, 7, 10),
            "discarded": {"radsport-events": date(2027, 7, 11)},
        }
    ]

    store(database, dates_as_values, edition_ids=[MARATHON])

    row = load(database)[0]
    assert row.kept == "2027-07-10"
    assert row.discarded == {"radsport-events": "2027-07-11"}


def test_loading_can_be_narrowed_to_one_edition(database):
    store(database, CONFLICTS, edition_ids=[MARATHON])

    # ORM rows compare by identity, so compare the fields that matter. Rows come
    # back grouped by edition and ordered by field name.
    narrowed = [(row.edition_id, row.field) for row in load(database, edition_id=MARATHON)]
    assert narrowed == [(MARATHON, "location.region"), (MARATHON, "official_url")]
    assert load(database, edition_id="does-not-exist") == []
    assert len(load(database, limit=1)) == 1


def test_conflicts_survive_reopening_the_database(database):
    store(database, CONFLICTS, edition_ids=[MARATHON])

    # The store/load helpers each open their own engine, so this asserts the
    # rows really are on disk rather than living in the writing session.
    assert len(load(database)) == 2

    engine = create_db_engine(database)
    try:
        with session_scope(engine) as session:
            assert len(load_merge_conflicts(session)) == 2
    finally:
        engine.dispose()


# --- CLI --------------------------------------------------------------------


def test_the_command_reports_when_there_is_nothing_to_show(database):
    result = CliRunner().invoke(app, ["conflicts", "--database", str(database)])

    assert result.exit_code == 0
    assert "No merge conflicts recorded" in result.stdout


def test_the_command_lists_stored_conflicts(database):
    store(database, CONFLICTS, edition_ids=[MARATHON])

    result = CliRunner().invoke(app, ["conflicts", "--database", str(database)])

    assert result.exit_code == 0
    assert "official_url" in result.stdout
    assert "bawue-radsportverband" in result.stdout
    assert "radsport-events=" in result.stdout


def test_the_command_names_the_race_not_only_the_id(database):
    store(database, CONFLICTS, edition_ids=[MARATHON])

    result = CliRunner().invoke(app, ["conflicts", "--database", str(database)])

    assert "Example Marathon" in result.stdout


def test_the_command_can_emit_json(database):
    store(database, CONFLICTS, edition_ids=[MARATHON])

    result = CliRunner().invoke(app, ["conflicts", "--database", str(database), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert {row["field"] for row in payload} == {"official_url", "location.region"}
    assert all(row["edition_id"] == MARATHON for row in payload)
    assert all(row["name"] == "Example Marathon" for row in payload)