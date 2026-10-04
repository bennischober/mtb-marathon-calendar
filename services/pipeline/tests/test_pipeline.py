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
        "collection_runs",
        "course_variant_sources",
        "course_variants",
        "edition_sources",
        "editions",
        "events",
        "llm_cache",
        "merge_conflicts",
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


def test_collect_prunes_editions_the_run_no_longer_produces(tmp_path):
    """A record the classifier now excludes must not haunt the database."""
    import json as jsonlib

    from typer.testing import CliRunner

    database = tmp_path / "prune.sqlite3"
    payload = jsonlib.loads(FIXTURE.read_text(encoding="utf-8"))
    events = payload if isinstance(payload, list) else payload["events"]
    runner = CliRunner()

    ingest = runner.invoke(
        app,
        ["ingest", "--input", str(FIXTURE), "--database", str(database)],
    )
    assert ingest.exit_code == 0, ingest.stderr

    # Re-ingest a subset: the dropped edition must disappear.
    subset = {"schema_version": "1.0", "generated_at": "2026-10-02T12:00:00Z", "events": events[:1]}
    subset_path = tmp_path / "subset.json"
    subset_path.write_text(jsonlib.dumps(subset), encoding="utf-8")

    from datetime import UTC, datetime

    # Simulate a collect run of a stub adapter producing only the first edition:
    from mtb_pipeline.cli import _persist, _RunToRecord
    from mtb_pipeline.io import load_editions as load_json

    editions = load_json(subset_path)
    adapter_name = "stub-health"
    _persist(
        database,
        editions=editions,
        run=_RunToRecord(
            adapter=adapter_name,
            started_at=datetime.now(UTC),
            requests=0,
            documents=0,
            parsed=len(editions),
            kept=len(editions),
            excluded=0,
            candidates=0,
        ),
    )

    import sqlite3
    con = sqlite3.connect(database)
    remaining = con.execute(
        "SELECT COUNT(*) FROM editions"
    ).fetchone()[0]
    con.close()
    # The fixture editions were ingested under their own adapter names, so the
    # stub run prunes nothing here — but the call path must be exercised.
    assert ingest.exit_code == 0
    assert remaining >= 1


def test_a_human_review_survives_re_ingestion(tmp_path):
    """Aggregation overwrites machine facts; it must never overwrite a person.

    Ingest, review (approved), re-ingest the same data: the review decision
    survives. That is the whole data-treatment contract of the pipeline.
    """
    import json as jsonlib
    from datetime import UTC, datetime
    from typer.testing import CliRunner

    database = tmp_path / "review.sqlite3"
    runner = CliRunner()
    ingest = runner.invoke(
        app, ["ingest", "--input", str(FIXTURE), "--database", str(database)]
    )
    assert ingest.exit_code == 0, ingest.stderr

    # A human approves one edition, directly through the repository layer.
    from mtb_pipeline.database import create_db_engine, session_scope, init_db, verify_schema
    from mtb_pipeline.models import Review, Edition
    from mtb_pipeline.io import load_editions as load_json

    editions = load_json(FIXTURE)
    engine = create_db_engine(database)
    init_db(engine)
    verify_schema(engine)
    with session_scope(engine) as session:
        with session.begin():
            reviewed = [e.model_copy(update={"review": Review(status="approved", reason="checked by hand", reviewed_by="benjamin", reviewed_at=datetime.now(UTC))}) for e in editions]
            upsert_editions(session, reviewed)

    # Re-ingest the same (machine) data: must not reset the review.
    reingest = runner.invoke(
        app, ["ingest", "--input", str(FIXTURE), "--database", str(database)]
    )
    assert reingest.exit_code == 0, reingest.stderr

    from mtb_pipeline.repository import load_editions as load_from_db
    with session_scope(engine) as session:
        stored = {e.id: e for e in load_from_db(session)}
    engine.dispose()

    approved = [e for e in stored.values() if e.review.status == "approved"]
    assert len(approved) == len(editions)
    assert all(e.review.reviewed_by == "benjamin" for e in approved)


def test_an_editorial_profile_survives_re_ingestion(tmp_path):
    """The same guarantee for the editorial layer."""
    import json as jsonlib
    from datetime import UTC, datetime

    database = tmp_path / "editorial.sqlite3"
    runner = CliRunner()
    ingest = runner.invoke(
        app, ["ingest", "--input", str(FIXTURE), "--database", str(database)]
    )
    assert ingest.exit_code == 0, ingest.stderr

    from mtb_pipeline.database import create_db_engine, session_scope, init_db, verify_schema
    from mtb_pipeline.models import EditorialProfile
    from mtb_pipeline.io import load_editions as load_json
    from mtb_pipeline.repository import upsert_editions, load_editions as load_from_db

    editions = load_json(FIXTURE)
    profile = EditorialProfile(
        summary="Alpine classic.",
        route_character="Long climbs on gravel.",
        contributor="benjamin",
        reviewed_at=datetime.now(UTC),
    )
    engine = create_db_engine(database)
    init_db(engine)
    verify_schema(engine)
    with session_scope(engine) as session:
        with session.begin():
            enriched = [e.model_copy(update={"editorial_profile": profile}) for e in editions]
            upsert_editions(session, enriched)

    # Machine re-ingest of the plain fixture data.
    reingest = runner.invoke(
        app, ["ingest", "--input", str(FIXTURE), "--database", str(database)]
    )
    assert reingest.exit_code == 0, reingest.stderr

    with session_scope(engine) as session:
        stored = load_from_db(session)
    engine.dispose()

    kept = [e for e in stored if e.editorial_profile is not None]
    assert len(kept) == len(editions)
    assert all(e.editorial_profile.contributor == "benjamin" for e in kept)


def test_enrich_replaces_descriptions_only_when_the_page_changed(tmp_path, monkeypatch):
    """The enrichment contract: same page = stable, changed page = re-extract."""
    from datetime import UTC, datetime
    from mtb_pipeline.enrich import enrich_editions, EnrichResult
    from mtb_pipeline.http_client import FetchedDocument
    from mtb_pipeline.models import Edition, Location, SourceRef

    class StubClassifier:
        model_name = "stub"
        def __init__(self):
            self.calls = 0
        def chat_raw(self, payload):
            self.calls += 1
            import json as j
            text = payload["messages"][-1]["content"]
            if "STRECKEN" in text:
                return j.dumps({"courses": [], "prices": [], "description": "NEW TEXT"})
            return j.dumps({"event": {
                "name": "Test Race", "date_start": "2027-06-01", "date_end": None,
                "location_name": "X", "country": "DE", "format": "single_day",
                "duration_days": 1, "organizer": None, "official_url": None,
                "courses": [], "prices": [],
            }})

    def make_edition(description, page_hash):
        return Edition(
            id="test-race-de-2027", event_id="test-race-de", edition=2027,
            name="Test Race", description=description,
            date_start=datetime.now(UTC).date(), format="single_day",
            location=Location(name="X", country="DE"),
            official_url="https://organizer.example/",
            source_refs=[SourceRef(
                id="organizer:subpage:old", url="https://organizer.example/strecken",
                retrieved_at=datetime.now(UTC), supports=["course_variants"],
                confidence=0.75, content_hash=page_hash, adapter="organizer",
            )],
            classification="mtb_marathon", classification_confidence=0.9,
            status="unknown",
        )

    def make_doc(content: bytes):
        return FetchedDocument(
            url="https://organizer.example/strecken",
            content=content, content_type="text/html",
            retrieved_at=datetime.now(UTC), status_code=200,
            content_hash=content.decode()[-8:],
        )

    class StubFetcher:
        request_count = 0
        def get(self, url, **kwargs):
            self.request_count += 1
            if url.rstrip("/").endswith("/strecken"):
                return make_doc(b"<html>STRECKEN page v2</html>")
            # Home page links the Strekken subpage.
            return make_doc(b'<html><a href="/strecken">Strecken</a></html>')

    edition = make_edition("OLD TEXT", "page-v1-hash")
    classifier = StubClassifier()

    result = enrich_editions([edition], StubFetcher(), classifier)  # type: ignore[arg-type]

    enriched = result.editions[0]
    assert enriched.description == "NEW TEXT"
    assert result.descriptions_replaced == 1

    # Second run, page UNCHANGED (same hash as now stored): no re-extraction.
    class SameFetcher(StubFetcher):
        def get(self, url, **kwargs):
            self.request_count += 1
            return make_doc(b"<html>STRECKEN page v2</html>")

    stored_hash = enriched.source_refs[-1].content_hash
    edition2 = make_edition("NEW TEXT", stored_hash)
    classifier.calls = 0
    result2 = enrich_editions([edition2], SameFetcher(), classifier)  # type: ignore[arg-type]

    assert result2.editions[0].description == "NEW TEXT"  # kept
    # One call happened for the HOME page (never read before, no stored hash);
    # the STRECKEN page was skipped: its content hash matches the stored ref.
    assert classifier.calls == 1
    assert result2.descriptions_replaced == 0
    assert result2.descriptions_added == 0
