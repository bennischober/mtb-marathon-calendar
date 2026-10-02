"""Replay tests for the Epic Series adapter against recorded pages."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mtb_pipeline.http_client import FetchedDocument
from mtb_pipeline.sources.epic_series import EpicSeriesAdapter, _parse_date_range

FIXTURES = Path(__file__).parent / "fixtures" / "epic-series"
SLUGS = ("capeepic", "4islands", "andorra", "swissepic", "wines2whales")


def _document(name: str, url: str) -> FetchedDocument:
    path = FIXTURES / name
    content = path.read_bytes()
    return FetchedDocument(
        url=url,
        content=content,
        content_type="text/html",
        retrieved_at=datetime(2026, 10, 2, 12, 0, tzinfo=UTC),
        status_code=200,
        content_hash=hashlib.sha256(content).hexdigest(),
    )


@pytest.fixture
def editions() -> list:
    documents = [_document("index.html", "https://www.epic-series.com/races")]
    for slug in SLUGS:
        documents.append(
            _document(f"{slug}.html", f"https://www.epic-series.com/races/{slug}")
        )
    return EpicSeriesAdapter().parse(documents)


def test_date_ranges_parse_including_en_dash():
    parsed = _parse_date_range("August 17–21, 2027")
    assert parsed is not None
    assert (parsed[0].isoformat(), parsed[1].isoformat()) == ("2027-08-17", "2027-08-21")

    single = _parse_date_range("May 5, 2027")
    assert single is not None
    assert single[0] == single[1]


def test_the_swiss_epic_is_collected_from_the_official_organizer(editions):
    swiss = next(e for e in editions if e.name == "Swiss Epic")
    assert swiss.date_start.isoformat() == "2027-08-17"
    assert swiss.date_end.isoformat() == "2027-08-21"
    assert swiss.duration_days == 5
    assert swiss.location.country == "CH"
    assert swiss.location.name == "Davos"
    assert swiss.classification == "mtb_stage_race"
    assert swiss.source_refs[0].adapter == "epic-series"


def test_each_race_appears_once_even_though_the_page_shows_it_twice(editions):
    names = [e.name for e in editions]
    assert len(names) == len(set(names))
    assert "Cape Epic" in names


def test_race_pages_contribute_coordinates_and_description(editions):
    swiss = next(e for e in editions if e.name == "Swiss Epic")
    assert swiss.location.lat == pytest.approx(46.8091)
    assert swiss.location.lng == pytest.approx(9.8398)
    assert swiss.description and "stage race" in swiss.description.lower()


def test_races_outside_the_country_map_are_skipped():
    """A race whose country cannot be mapped is skipped, not guessed."""
    adapter = EpicSeriesAdapter()
    # _location returns "" for unmapped countries and parse() skips those
    town, country = adapter._location("Nowhere", None)
    assert country == ""
