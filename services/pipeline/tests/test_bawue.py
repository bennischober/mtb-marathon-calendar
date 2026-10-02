"""Tests for the BAD/WTB federation calendar adapter.

The fixture is the real published season PDF, recorded on 2026-10-02 from
https://www.badischer-radsportverband.de/. Testing against the actual file is
deliberate: the column layout is fragile, and a hand-written stand-in would keep
passing long after pypdf or the PDF stopped yielding usable text.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from mtb_pipeline.http_client import FetchedDocument
from mtb_pipeline.models import Edition, SourceRef
from mtb_pipeline.sources.bawue_radsportverband import (
    SEASON,
    SEASON_PDF_URL,
    BadenWuerttembergCalendarAdapter,
    parse_calendar_pdf,
    parse_calendar_text,
)

FIXTURE = Path(__file__).parent / "fixtures" / "bawue-radsportverband" / "rennkalender-2026.pdf"
RETRIEVED_AT = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def adapter() -> BadenWuerttembergCalendarAdapter:
    return BadenWuerttembergCalendarAdapter()


def document() -> FetchedDocument:
    return FetchedDocument(
        url=SEASON_PDF_URL,
        content=FIXTURE.read_bytes(),
        content_type="application/pdf",
        retrieved_at=RETRIEVED_AT,
        status_code=200,
        content_hash="b" * 64,
    )


def parse_fixture() -> list[Edition]:
    return adapter().parse([document()])


def find(fragment: str) -> Edition:
    matches = [edition for edition in parse_fixture() if fragment in edition.name]
    assert matches, f"no edition matched {fragment!r}"
    return matches[0]


# --- row parsing -----------------------------------------------------------


def test_the_real_pdf_yields_a_plausible_number_of_races():
    rows = parse_calendar_pdf(FIXTURE.read_bytes())
    assert len(rows) > 80


def test_legend_and_header_lines_are_not_mistaken_for_races():
    """The legend also contains date-shaped and header text."""
    names = [row.name for row in parse_calendar_pdf(FIXTURE.read_bytes())]
    assert "Datum von" not in names
    assert "Name der Veranstaltung" not in names
    assert "MTB Männer Elite" not in names
    assert not any(name.startswith("BaWü_Rennkalender") for name in names)


def test_the_event_name_stops_at_the_verband_column():
    row = next(r for r in parse_calendar_pdf(FIXTURE.read_bytes()) if "Odenwald" in r.name)
    assert row.name == "OdenwaldBikeMarathon Short Track"


def test_text_parsing_is_usable_without_the_pdf_layer():
    rows = parse_calendar_text(
        "18.04.2026 Kinetixx Frühjahrsmarathon Münsingen WTB LV MTB XCM x Schw-Zol TSG"
    )
    assert [row.name for row in rows] == ["Kinetixx Frühjahrsmarathon Münsingen"]
    assert rows[0].region == "Schwaben-Zollern"


def test_a_line_without_a_verband_code_is_rejected():
    assert parse_calendar_text("01.01.2026 some heading text without codes") == []


def test_a_cancelled_race_is_recognised_without_a_status_column():
    rows = parse_calendar_text("19.04.2026 ABGESAGT Konstanzer City Kriterium BAD LV STR KRIT")
    assert rows[0].is_cancelled is True
    assert rows[0].end_date_text is None


def test_a_multi_day_row_keeps_both_dates():
    rows = parse_calendar_text("11.04.2026 12.04.2026 28. Hausacher MTB Weekend BAD GC MTB XCO")
    assert rows[0].date_text == "11.04.2026"
    assert rows[0].end_date_text == "12.04.2026"


def test_an_impossible_year_is_corrected_and_reported():
    """The published calendar prints a 2026 race as 21.05.2065."""
    edition = find("Bahnen-Tournee Öschelbronn")
    assert edition.date_start == date(SEASON, 5, 21)
    assert f"date_year_corrected_from_2065_to_{SEASON}" in edition.extraction_warnings


# --- discipline handling ---------------------------------------------------


def test_a_specific_discipline_code_beats_the_generic_mtb_marker():
    """``MTB XCO`` is a cross-country race despite "Marathon" in the title."""
    assert find("Härtsfeld Mountainbike-Marathon").classification == "exclude"


def test_fourcross_is_excluded():
    assert find("European Fourcross (4X) Series 2026 Esslingen").classification == "exclude"


def test_road_and_track_races_are_excluded():
    excluded = [e for e in parse_fixture() if e.classification == "exclude"]
    names = " ".join(e.name for e in excluded)
    assert "Niederwangener" in names
    assert "Bahnen-Tournee" in names


def test_xcm_races_are_included_as_marathons():
    assert find("Black Forest Bike Marathon").classification == "mtb_marathon"


def test_a_bare_mtb_row_falls_back_to_the_event_title():
    assert find("OdenwaldBikeMarathon Short Track").classification == "mtb_marathon"


def test_only_mtb_classifications_can_survive_the_filter():
    """This calendar covers road, track and gravel as well, so check the filter.

    ``mtb_candidate`` is expected to survive: rows whose discipline column is
    blank cannot be judged here and are meant to reach a human as a review
    queue, flagged with ``review=unreviewed``.
    """
    survivors = {e.classification for e in parse_fixture() if e.classification != "exclude"}
    assert survivors <= {"mtb_marathon", "mtb_stage_race", "mtb_candidate"}
    assert "mtb_marathon" in survivors


def test_track_road_and_gravel_races_are_excluded():
    excluded = {e.name for e in parse_fixture() if e.classification == "exclude"}
    for fragment in ("Bahnen-Tournee", "Gravel-Bike-Race", "Niederwangener", "Linkenheim"):
        assert any(fragment in name for name in excluded), fragment


def test_ambiguous_rows_reach_review_unreviewed_rather_than_being_dropped():
    """A blank discipline column must not silently lose a real race."""
    edition = find("Alb Gold Trophy")
    assert edition.classification == "mtb_candidate"
    assert edition.review.status == "unreviewed"


def test_a_cancelled_race_is_flagged_in_the_status():
    assert find("Konstanzer City Kriterium").status == "cancelled"


# --- edition shape ---------------------------------------------------------


def test_structural_gaps_are_reported_rather_than_invented():
    warnings = find("Black Forest Bike Marathon").extraction_warnings
    assert "no_distance_data" in warnings
    assert "official_url_is_federation_calendar_pdf" in warnings
    assert "location_is_region_label_only" in warnings


def test_the_country_is_germany():
    assert find("Black Forest Bike Marathon").location.country == "DE"


def test_the_organiser_is_not_mistaken_for_the_region():
    edition = find("Black Forest Bike Marathon")
    assert edition.location.region == "Süd"
    assert "Velo-Bike" not in edition.location.name


def test_the_region_is_not_repeated_as_the_place_name():
    # A reader must never see a location rendered as "Süd, Süd, DE", which is
    # what happened while the region doubled as the venue.
    for name in ("Black Forest Bike Marathon", "14. Sulzbacher Bergzeitfahren für Jedermann"):
        location = find(name).location
        assert location.name == "Baden-Württemberg"
        assert location.name != location.region


def test_provenance_points_at_the_source_pdf():
    edition = find("Black Forest Bike Marathon")
    assert str(edition.source_refs[0].url) == SEASON_PDF_URL
    assert edition.source_refs[0].adapter == "bawue-radsportverband"
    assert edition.source_refs[0].content_hash == "b" * 64


def test_ids_are_stable_across_runs():
    first = {e.name: e.id for e in parse_fixture()}
    second = {e.name: e.id for e in parse_fixture()}
    assert first == second


def test_the_year_in_the_title_does_not_split_the_event_identity():
    """``normalize_name`` drops the season, so both years share one event_id."""
    editions = parse_fixture()
    schwarzwald = [e for e in editions if "Schwarzwald Bike Marathon" in e.name]
    assert schwarzwald
    assert len({e.event_id for e in schwarzwald}) == len(schwarzwald)


# --- profile ---------------------------------------------------------------


def test_the_profile_declares_an_official_federation_source():
    profile = adapter().profile
    assert profile.priority == "official_federation"
    assert profile.data_format == "pdf"
    assert profile.countries == ("DE",)
    assert profile.terms_status == "confirmed"


def test_the_profile_records_what_the_source_cannot_provide():
    assert "no distances" in adapter().profile.notes


def test_parsing_no_documents_returns_nothing():
    assert adapter().parse([]) == []


def test_a_corrupt_document_fails_loudly():
    broken = replace(document(), content=b"not a pdf at all")
    with pytest.raises(Exception):  # noqa: B017 - pypdf raises its own error type
        adapter().parse([broken])


# --- cross-source behaviour ------------------------------------------------


def test_the_federation_outranks_the_aggregator_when_both_see_one_race():
    """Proves the registry priority reaches the deduper for real adapter names.

    ``merge_editions`` keys priorities by adapter name while ``PRIORITY_RANK`` is
    keyed by category, so without an explicit mapping every adapter would silently
    share the default rank and priority would never be applied.
    """
    from mtb_pipeline.io import _source_priorities

    priorities = _source_priorities()
    assert priorities["bawue-radsportverband"] < priorities["radsport-events"]

    federation = find("Black Forest Bike Marathon")
    aggregator = federation.model_copy(
        update={
            "id": "radsport-events-black-forest-bike-marathon-de-2026",
            "location": federation.location.model_copy(update={"name": "Freiburg"}),
            "source_refs": [
                SourceRef(
                    id="radsport-events:event:1",
                    url="https://radsport-events.de/events/1",
                    retrieved_at=RETRIEVED_AT,
                    supports=["name", "location"],
                    confidence=0.7,
                    adapter="radsport-events",
                )
            ],
        }
    )

    from mtb_pipeline.dedupe import merge_editions

    result = merge_editions(
        [federation.model_dump(mode="json"), aggregator.model_dump(mode="json")],
        source_priority=priorities,
    )

    assert result.merges == 1
    assert len(result.editions) == 1
    # The federation record wins the location, and the aggregator is not lost.
    assert result.editions[0]["location"]["name"] == federation.location.name
    assert {ref["adapter"] for ref in result.editions[0]["source_refs"]} == {
        "bawue-radsportverband",
        "radsport-events",
    }
