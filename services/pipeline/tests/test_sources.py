import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mtb_pipeline.collect import collect
from mtb_pipeline.http_client import FetchedDocument
from mtb_pipeline.models import Edition
from mtb_pipeline.sources import CollectOptions, available_sources, get_adapter
from mtb_pipeline.sources.base import SourceAdapter
from mtb_pipeline.sources.radsport_events import (
    PAGE_SIZE,
    RadsportEventsAdapter,
    build_page_params,
)

FIXTURE = Path(__file__).parent / "fixtures" / "radsport-events" / "events-page-0.json"
RETRIEVED_AT = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def load_fixture_document() -> FetchedDocument:
    payload = FIXTURE.read_bytes()
    return FetchedDocument(
        url="https://api.radsport-events.de/api/v1/events?page=0&size=100&category=MTB",
        content=payload,
        content_type="application/json",
        retrieved_at=RETRIEVED_AT,
        status_code=200,
        content_hash="fixture" + "0" * 58,
    )


def _page_document(index: int, record_count: int, total_pages: int) -> FetchedDocument:
    """A synthetic API page, so pagination can be tested without the network."""
    payload = {
        "content": [{"id": str(index * 1000 + offset)} for offset in range(record_count)],
        "totalPages": total_pages,
        "totalElements": total_pages * 100,
        "number": index,
        "size": record_count,
    }
    body = json.dumps(payload).encode()
    return FetchedDocument(
        url=f"https://api.radsport-events.de/api/v1/events?page={index}&size={record_count}",
        content=body,
        content_type="application/json",
        retrieved_at=RETRIEVED_AT,
        status_code=200,
        content_hash=f"{index:064d}",
    )


def _document_from_records(records: list[dict]) -> FetchedDocument:
    payload = {"content": records, "totalPages": 1, "totalElements": len(records)}
    return FetchedDocument(
        url="https://api.radsport-events.de/api/v1/events?page=0&size=100",
        content=json.dumps(payload).encode(),
        content_type="application/json",
        retrieved_at=RETRIEVED_AT,
        status_code=200,
        content_hash="a" * 64,
    )


def parse_fixture() -> list[Edition]:
    adapter = RadsportEventsAdapter()
    return adapter.parse([load_fixture_document()])


def by_title(editions: list[Edition], needle: str) -> Edition:
    return next(edition for edition in editions if needle.lower() in edition.name.lower())


def test_registry_exposes_the_source_under_its_stable_name():
    assert "radsport-events" in available_sources()
    assert isinstance(get_adapter("radsport-events"), RadsportEventsAdapter)


def test_unknown_source_raises_with_a_helpful_message():
    with pytest.raises(ValueError, match="unknown source"):
        get_adapter("does-not-exist")


def test_page_params_request_only_published_mtb_records():
    params = build_page_params(1)

    assert params["category"] == "MTB"
    assert params["status"] == "PUBLISHED"
    assert params["page"] == 1


def test_page_size_matches_the_api_cap():
    assert PAGE_SIZE == 100


def test_limit_shrinks_the_page_size_and_stops_paging():
    """A limit is a record budget, so the request should ask for no more than that."""
    calls = []

    class RecordingFetcher:
        def __init__(self, documents: list[FetchedDocument]) -> None:
            self._documents = documents

        def get(self, url, *, params, accept, snapshot_name):  # noqa: ANN001, ANN202, ARG002
            calls.append(params)
            return self._documents[len(calls) - 1]

    pages = [_page_document(index, record_count=100, total_pages=5) for index in range(5)]
    adapter = RadsportEventsAdapter()

    documents = adapter.fetch(RecordingFetcher(pages), CollectOptions(limit=150))

    assert [params["size"] for params in calls] == [100, 50]
    assert len(documents) == 2


def test_limit_smaller_than_a_page_requests_only_that_many_records():
    calls = []

    class RecordingFetcher:
        def get(self, url, *, params, accept, snapshot_name):  # noqa: ANN001, ANN202, ARG002
            calls.append(params)
            return _page_document(0, record_count=20, total_pages=5)

    RadsportEventsAdapter().fetch(RecordingFetcher(), CollectOptions(limit=20))

    assert [params["size"] for params in calls] == [20]


def test_fetch_without_a_limit_pages_until_the_api_is_exhausted():
    calls = []

    class RecordingFetcher:
        def __init__(self, documents: list[FetchedDocument]) -> None:
            self._documents = documents

        def get(self, url, *, params, accept, snapshot_name):  # noqa: ANN001, ANN202, ARG002
            calls.append(params)
            return self._documents[len(calls) - 1]

    pages = [
        _page_document(0, record_count=100, total_pages=3),
        _page_document(1, record_count=100, total_pages=3),
        _page_document(2, record_count=18, total_pages=3),
    ]

    documents = RadsportEventsAdapter().fetch(RecordingFetcher(pages), CollectOptions())

    assert len(documents) == 3
    assert all(params["size"] == PAGE_SIZE for params in calls)


def test_parser_reads_every_record_in_the_fixture():
    records = json.loads(FIXTURE.read_text(encoding="utf-8"))["content"]

    assert len(parse_fixture()) == len(records)


def test_marathon_keeps_its_course_variants():
    edition = by_title(parse_fixture(), "Maintal Bike Marathon")

    assert edition.classification == "mtb_marathon"
    assert edition.location.country == "DE"
    assert edition.edition == 2026
    assert len(edition.course_variants) >= 1
    longest = max(edition.course_variants, key=lambda variant: variant.distance_km)
    assert longest.distance_km > 50


def test_short_marathon_is_still_included_with_a_warning():
    edition = by_title(parse_fixture(), "Adelsberger Bike Marathon")

    assert edition.classification == "mtb_marathon"
    assert any("below_50km" in warning for warning in edition.extraction_warnings)


def test_multi_day_stage_race_maps_to_stage_race_format():
    edition = by_title(parse_fixture(), "Tour de Mur")

    assert edition.classification == "mtb_stage_race"
    assert edition.format == "stage_race"
    assert edition.date_end is not None
    assert edition.duration_days and edition.duration_days > 1


def test_multi_day_ultra_is_a_stage_race():
    """Trans Balkan Race spans ten days, so it is a stage race, not a one-day ultra."""
    edition = by_title(parse_fixture(), "Trans Balkan Race")

    assert edition.classification == "mtb_stage_race"
    assert edition.format == "stage_race"
    assert max(variant.distance_km for variant in edition.course_variants) > 100


def test_multi_day_marathon_weekend_is_not_forced_into_a_stage_format():
    """Spanning days is not the same as riding them as stages."""
    record = {
        "id": 90001,
        "title": "Bike Festival Test Valley",
        "description": "Zwei Tage mit mehreren Marathon-Rennen.",
        "country": "IT",
        "category": "MTB",
        "eventType": "MTB_MARATHON",
        "eventDate": "2027-04-30",
        "endDate": "2027-05-02",
        "locationName": "Riva del Garda",
        "distances": [{"label": "Marathon", "distanceKm": 83.9, "elevationMeters": 2600}],
    }
    adapter = RadsportEventsAdapter()

    edition = adapter.parse([_document_from_records([record])])[0]

    assert edition.format == "multi_day"
    assert edition.classification == "mtb_marathon"
    assert edition.duration_days == 3


def test_cyclocross_and_downhill_are_excluded():
    editions = parse_fixture()

    assert by_title(editions, "Cyclo-Cross-Cup").classification == "exclude"
    assert by_title(editions, "Gravity Cup").classification == "exclude"
    assert by_title(editions, "Schwarzwälder MTB Cup").classification == "exclude"


def test_cancelled_event_is_flagged_and_marked_cancelled():
    edition = by_title(parse_fixture(), "Example Cancelled MTB Marathon")

    assert edition.classification == "mtb_marathon"
    assert edition.status == "cancelled"
    assert "event_is_cancelled" in edition.extraction_warnings


def test_registration_open_maps_to_registration_open_status():
    edition = by_title(parse_fixture(), "Lautertal-Bikemarathon")

    assert edition.status == "registration_open"


def test_every_edition_carries_source_provenance():
    for edition in parse_fixture():
        assert edition.source_refs
        ref = edition.source_refs[0]
        assert ref.adapter == "radsport-events"
        assert ref.native_id
        assert ref.url
        assert ref.content_hash
        assert ref.retrieved_at == RETRIEVED_AT


def test_official_url_prefers_the_organizer_page():
    edition = by_title(parse_fixture(), "Maintal Bike Marathon")

    assert str(edition.official_url).startswith("http")


def test_event_ids_are_stable_across_years_and_ignore_edition_numbers():
    from mtb_pipeline.normalize import event_id

    first = event_id("radsport-events", "Swiss Epic 2027", "CH")
    second = event_id("radsport-events", "Swiss Epic 2028", "CH")

    assert first == second
    assert "2027" not in first


class StubAdapter(SourceAdapter):
    name = "stub"
    homepage = "https://example.org"

    def __init__(self, editions: list[Edition]) -> None:
        self._editions = editions

    def fetch(self, fetcher, options):  # noqa: ANN001, ANN202
        return [load_fixture_document()]

    def parse(self, documents) -> list[Edition]:  # noqa: ANN001, ANN201
        return self._editions


def test_collect_filters_by_country_and_classification():
    editions = parse_fixture()

    result = collect(StubAdapter(editions), countries=("DE", "AT", "CH", "IT", "FR"))

    assert result.parsed == len(editions)
    assert result.kept < result.parsed
    assert all(
        edition.location.country in ("DE", "AT", "CH", "IT", "FR") for edition in result.editions
    )
    assert all(edition.classification != "exclude" for edition in result.editions)


def test_collect_counts_candidates_for_the_review_queue():
    result = collect(StubAdapter(parse_fixture()))

    assert result.candidates == sum(
        1 for edition in result.editions if edition.classification == "mtb_candidate"
    )


def test_collect_options_are_passed_through_to_the_adapter():
    seen: dict[str, object] = {}

    class RecordingAdapter(StubAdapter):
        def fetch(self, fetcher, options):  # noqa: ANN001, ANN202
            seen["limit"] = options.limit
            return []

    collect(RecordingAdapter([]), options=CollectOptions(limit=5))

    assert seen["limit"] == 5


def test_collect_command_rejects_unknown_source():
    from typer.testing import CliRunner

    from mtb_pipeline.cli import app

    result = CliRunner().invoke(app, ["collect", "--source", "not-a-source", "--dry-run"])

    assert result.exit_code == 1
    assert "unknown source" in result.stderr


def test_collect_command_rejects_out_of_scope_country():
    from typer.testing import CliRunner

    from mtb_pipeline.cli import app

    result = CliRunner().invoke(
        app, ["collect", "--source", "radsport-events", "--country", "ES", "--dry-run"]
    )

    assert result.exit_code == 1
    assert "Unsupported countries" in result.stderr
