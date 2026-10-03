"""FFC calendar source: postback navigation + generic LLM extraction.

The navigation is tested against recorded calendar pages; the extraction
against a stub LLM, so the suite runs offline.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

from mtb_pipeline.http_client import FetchedDocument
from mtb_pipeline.llm_extract import extract_edition
from mtb_pipeline.sources.ffc_calendar import _postback_state

FIXTURES = Path(__file__).parent / "fixtures" / "ffc"


def _document(name: str, url: str) -> FetchedDocument:
    content = (FIXTURES / name).read_bytes()
    return FetchedDocument(
        url=url,
        content=content,
        content_type="text/html",
        retrieved_at=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
        status_code=200,
        content_hash="a" * 64,
    )


def test_postback_state_extracts_the_asp_fields():
    state = _postback_state((FIXTURES / "calendar.html").read_bytes())

    assert state is not None
    assert set(state) == {"__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"}
    assert state["__VIEWSTATEGENERATOR"] == "B02AF955"


def test_filtered_view_contains_only_vtt_rows():
    html = (FIXTURES / "calendar-filtered.html").read_text(encoding="utf-8", errors="ignore")
    pattern = r'<a[^>]+href="(/calendrier/competition/[^"]+)"[^>]*>(.*?)</a>'
    rows = re.findall(pattern, html, re.S)
    vtt_rows = [t for _, t in rows if "VTT" in t]

    assert rows
    assert len(vtt_rows) == len(rows)


class StubClassifier:
    """Returns a canned extraction for any prompt."""

    model_name = "stub"

    def __init__(self, event: dict | None) -> None:
        self.event = event

    def chat_raw(self, payload) -> str:  # noqa: ANN001
        import json

        return json.dumps({"event": self.event})


def test_extract_edition_builds_a_valid_edition_from_a_recorded_page():
    document = _document(
        "extreme-sur-loue-rando-dor.html",
        "https://competitions.ffc.fr/calendrier/competition/2026/C4115060101",
    )
    event = {
        "name": "Extrême sur Loue - Rando d'Or",
        "date_start": "2026-10-04",
        "date_end": None,
        "location_name": "Doubs",
        "country": "FR",
        "format": "single_day",
        "duration_days": 1,
        "organizer": None,
        "official_url": None,
        "courses": [],
        "prices": [],
    }

    edition = extract_edition(
        StubClassifier(event),  # type: ignore[arg-type]
        source_name="ffc",
        url=document.url,
        content=document.content,
        retrieved_at=document.retrieved_at,
        content_hash=document.content_hash,
        confidence=0.85,
    )

    assert edition is not None
    assert edition.name == "Extrême sur Loue - Rando d'Or"
    assert edition.location.country == "FR"
    assert edition.event_id == "ffc-extreme-sur-loue-rando-d-or-fr"
    assert edition.source_refs[0].adapter == "ffc"


def test_a_non_event_page_yields_no_edition():
    document = _document(
        "extreme-sur-loue-rando-dor.html",
        "https://competitions.ffc.fr/calendrier/calendrier.aspx",
    )

    edition = extract_edition(
        StubClassifier(None),  # type: ignore[arg-type]
        source_name="ffc",
        url=document.url,
        content=document.content,
        retrieved_at=document.retrieved_at,
        content_hash=document.content_hash,
    )

    assert edition is None
