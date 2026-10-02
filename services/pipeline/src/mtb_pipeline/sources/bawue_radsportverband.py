"""Adapter for the BAD/WTB Baden-Württemberg federation race calendar.

Why a PDF: the German national federation (Bund Deutscher Radfahrer) has no
public machine calendar at all. ``bdr.de`` is a dead domain that redirects to an
unrelated printing company, and the federation's own site resolves to
``rad-net.de``, whose robots.txt explicitly disallows AI and search crawlers,
so automating it is out of the question. Verified 2026-10-02 and recorded in
docs/DATA_SOURCES.md.

The regional association (Badischer Radsportverband / Württembergischer
Radsportverband) is the next level down and does publish its season calendar as
a single PDF under a permissive robots.txt. It is the one verified-clean,
machine-readable federation source, which makes it worth parsing even though it
only covers Baden-Württemberg.

What the calendar does *not* contain, and what this adapter therefore does not
invent:

* no distances, so every marathon carries a ``no_distance_data`` warning
* no per-event web page, so ``official_url`` points at the calendar PDF itself
* no venue coordinates, and only a coarse ``Region`` label for the location

Every one of those gaps is reported as an extraction warning so a human can
enrich the record later through ``data/curated/`` instead of the gap silently
becoming a fabricated fact.

The PDF is re-published each season under a new ``Stand_<date>`` filename, so
``SEASON_PDF_URL`` must be updated annually. There is no sitemap or HTML link
to the file, which rules out automatic discovery.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from pypdf import PdfReader

from ..classification import EventSignals, classify
from ..http_client import FetchedDocument, Fetcher
from ..models import Edition, Location, Review, SourceRef
from ..normalize import clean_text
from ..normalize import edition_id as make_edition_id
from ..normalize import event_id as make_event_id
from .base import CollectOptions, SourceAdapter, SourceProfile

SITE_BASE = "https://www.badischer-radsportverband.de"
#: Season PDF, re-uploaded whenever the association updates the calendar.
SEASON_PDF_URL = (
    f"{SITE_BASE}/fileadmin/user_upload/"
    "BaWue_Rennkalender_STR-B-CX-MTB-GRA_2026-Stand_03.07.2026.pdf"
)
SEASON = 2026

SUPPORTED_FIELDS = [
    "name",
    "date_start",
    "date_end",
    "location",
    "status",
    "discipline",
]

SUPPORTED_COUNTRIES = ("DE",)

# Rows are one extracted text line each, e.g.
# "18.04.2026 Kinetixx Frühjahrsmarathon Münsingen + Gravel WTB LV MTB XCM ..."
_DATE = r"\d{1,2}\.\d{1,2}\.\d{4}"
_ROW_RE = re.compile(rf"^(?P<start>{_DATE})(?:\s+(?P<end>{_DATE}|ABGESAGT))?\s+(?P<rest>\S.*)$")

# Verband codes that end the free-text event name.
_VERBAND_CODES = {
    "BAD",
    "WTB",
    "BRV",
    "WRSV",
    "BAY",
    "GC",
    "LV",
    "UCI",
    "LV-MTB",
}

# Discipline codes used by the association. ``MTB`` on its own is only a family
# marker in the "Disziplin" column, so a more specific code elsewhere on the row
# always wins: a race listed as ``MTB XCO`` is a cross-country race and stays out
# of scope even though its title says "Mountainbike-Marathon".
#
# Order matters, because compound codes are matched by prefix: "GRAVEL" has to
# be tried before "CX" would claim a "CX-GRAVEL" cell, and "BAHN" covers both
# "BAHN" and "BAHN-CUP".
_DISCIPLINE_CODE_MAP: dict[str, str] = {
    "XCM": "xcm",
    "XCT": "xct",
    "XCO": "xco",
    "XCC": "xcc",
    "XCE": "xce",
    "4X": "fourcross",
    "BAHN": "bahn",
    "GRAVEL": "gravel",
    "CX": "cyclocross",
    "STR": "rennrad",
    "RR": "rennrad",
    "KRIT": "kriterium",
    "EZF": "einzelzeitfahren",
    "DM": "deutsche_meisterschaft",
}
# Longest first, so "BAHN-CUP" is not truncated to something more specific.
_SORTED_DISCIPLINE_CODES = sorted(_DISCIPLINE_CODE_MAP, key=len, reverse=True)
_MTB_DISCIPLINE_LABELS = {"xcm", "xct", "xco", "xcc", "xce", "fourcross"}
_FAMILY_MARKER = "MTB"

# Coarse region labels used in place of a venue. They are real geographic
# divisions of the association, not towns, so they land in `region`.
# Keys are uppercased to match the extracted text.
_REGION_CODES = {
    "OST": "Ost",
    "MITTE": "Mitte",
    "SÜD": "Süd",
    "NORD": "Nord",
    "BAWÜ": "Baden-Württemberg",
    "OBERSCHW.": "Oberschwaben",
    "SCHW-ZOL": "Schwaben-Zollern",
    "SCHÖN-WÜR.": "Schönwürttemberg",
    "STUTTGART": "Stuttgart",
    "KRAICHGAU": "Kraichgau",
}

# Canceled races are not marked in a status column: the word simply replaces the
# optional end date.
_CANCELLED_MARKER = "ABGESAGT"

# A marathon must be labelled as one, not merely named like one: "Härtsfeld
# Mountainbike-Marathon" is an XCO race and stays out of scope.
_MARATHON_NAME_TOKENS = ("marathon", "bike-marathon", "mtb-marathon")
_TOUR_NAME_TOKENS = ("tour", "etappe", "epic")


class BadenWuerttembergCalendarAdapter(SourceAdapter):
    profile = SourceProfile(
        name="bawue-radsportverband",
        homepage=SITE_BASE,
        endpoint=SEASON_PDF_URL,
        priority="official_federation",
        countries=SUPPORTED_COUNTRIES,
        cadence_hours=168,
        data_format="pdf",
        parser_status="verified",
        robots_status=(
            "robots.txt allows all paths except /typo3/ and /typo3_src/ "
            "(User-agent: *); verified 2026-10-02"
        ),
        terms_status="confirmed",
        notes=(
            "Official regional association calendar (BAD/WTB, Baden-Württemberg). "
            "Publicly published season PDF with a discipline column, which is "
            "rare for German federation calendars. Limitations: no distances, no "
            "per-event pages, and coarse region labels instead of venues. "
            "Official federation priority, so it outranks the aggregator, but it "
            "only ever contributes Baden-Württemberg."
        ),
    )

    def fetch(self, fetcher: Fetcher, options: CollectOptions) -> list[FetchedDocument]:
        if options.max_documents is not None and options.max_documents < 1:
            return []
        return [
            fetcher.get(
                SEASON_PDF_URL,
                accept="application/pdf",
                snapshot_name=f"bawue-rennkalender-{SEASON}",
            )
        ]

    def parse(self, documents: Sequence[FetchedDocument]) -> list[Edition]:
        editions: list[Edition] = []
        seen: set[str] = set()
        for document in documents:
            for record in parse_calendar_pdf(document.content):
                edition = self._to_edition(record, document)
                if edition is None or edition.id in seen:
                    continue
                seen.add(edition.id)
                editions.append(edition)
        return editions

    def _to_edition(self, record: CalendarRow, document: FetchedDocument) -> Edition | None:
        event_date, date_warning = _resolve_date(record.date_text)
        if event_date is None:
            return None

        end_date = _parse_date(record.end_date_text)
        if end_date is not None and end_date < event_date:
            end_date = None
        duration_days = (end_date - event_date).days + 1 if end_date else 1

        discipline = _discipline_from_codes(record.codes) or (
            record.discipline_hint if _has_mtb_family_marker(record.codes) else None
        )
        classification = classify(
            EventSignals(
                discipline=discipline,
                max_distance_km=None,
                duration_days=duration_days,
                is_cancelled=record.is_cancelled,
                is_stage_like=any(token in record.discipline_hint for token in _TOUR_NAME_TOKENS),
                title=record.name,
            )
        )

        warnings = list(classification.warnings)
        if date_warning:
            warnings.append(date_warning)
        warnings.extend(_known_gap_warnings())

        stable_event_id = make_event_id(self.name, record.name, SUPPORTED_COUNTRIES[0])
        record_edition_id = make_edition_id(stable_event_id, event_date.year)
        source_ref = SourceRef(
            id=f"{self.name}:calendar:{SEASON}",
            url=SEASON_PDF_URL,
            retrieved_at=document.retrieved_at,
            supports=SUPPORTED_FIELDS,
            confidence=classification.confidence,
            content_hash=document.content_hash,
            native_id=f"{SEASON}:{record.line_number}",
            adapter=self.name,
        )

        return Edition(
            id=record_edition_id,
            event_id=stable_event_id,
            edition=event_date.year,
            name=record.name,
            date_start=event_date,
            date_end=end_date,
            format=_event_format(duration_days, classification.classification),
            duration_days=duration_days if duration_days > 1 else None,
            location=Location(
                name=record.region or "Baden-Württemberg",
                country=SUPPORTED_COUNTRIES[0],
                region=record.region,
            ),
            official_url=SEASON_PDF_URL,
            source_refs=[source_ref],
            classification=classification.classification,
            classification_confidence=classification.confidence,
            extraction_warnings=sorted(set(warnings)),
            review=Review(),
            status="cancelled" if record.is_cancelled else "announced",
            last_verified=document.retrieved_at.date(),
        )


@dataclass(frozen=True)
class CalendarRow:
    """One extracted calendar line, already split into the fields we can trust."""

    name: str
    date_text: str
    end_date_text: str | None
    is_cancelled: bool
    codes: tuple[str, ...]
    discipline_hint: str
    region: str | None
    line_number: int


def extract_pdf_lines(content: bytes) -> list[str]:
    """Decode the season PDF into raw text lines."""
    reader = PdfReader(io.BytesIO(content))
    lines: list[str] = []
    for page in reader.pages:
        lines.extend((page.extract_text() or "").splitlines())
    return lines


def parse_calendar_text(text: str) -> list[CalendarRow]:
    """Extract calendar rows from already-extracted PDF text.

    Kept separate from :func:`extract_pdf_lines` so the column logic can be
    tested as plain text, without a binary fixture, and so a change in pypdf's
    text extraction cannot be mistaken for a change in parsing.
    """
    rows: list[CalendarRow] = []
    line_number = 0

    for raw_line in text.splitlines():
        line_number += 1
        line = raw_line.strip()
        match = _ROW_RE.match(line)
        if not match:
            continue
        row = _split_row(line, match, line_number)
        if row is not None:
            rows.append(row)

    return rows


def parse_calendar_pdf(content: bytes) -> list[CalendarRow]:
    """Extract calendar rows from the season PDF."""
    return parse_calendar_text("\n".join(extract_pdf_lines(content)))


def _split_row(line: str, match: re.Match[str], line_number: int) -> CalendarRow | None:
    rest = clean_text(match.group("rest"))

    # The event name ends at the first Verband code, which is the only column
    # whose vocabulary is fixed and always present.
    tokens = rest.split(" ")
    name_end = None
    for index, token in enumerate(tokens):
        if token.upper() in _VERBAND_CODES:
            name_end = index
            break
    if name_end is None or name_end == 0:
        # Header and legend lines also match the date shape; they carry no
        # Verband code, so there is no name to extract.
        return None

    name = clean_text(" ".join(tokens[:name_end]))
    if not name:
        return None

    tail = tokens[name_end:]
    codes = tuple(token.upper() for token in tail)
    end_text = match.group("end")
    is_cancelled = end_text == _CANCELLED_MARKER

    return CalendarRow(
        name=name,
        date_text=match.group("start"),
        end_date_text=None if is_cancelled else end_text,
        is_cancelled=is_cancelled,
        codes=codes,
        discipline_hint=_discipline_hint(name),
        region=_region_from_codes(codes),
        line_number=line_number,
    )


def _discipline_hint(name: str) -> str:
    """Fall back to the title when the discipline column is empty.

    Regional calendars routinely leave the discipline blank for well-known
    series, so the title is the only remaining signal. A marathon word is only
    used when the row is already known to be MTB, which the caller guarantees.
    """
    lowered = name.lower()
    if any(token in lowered for token in _MARATHON_NAME_TOKENS):
        return "mtb_marathon"
    return "mtb_other"


def _discipline_from_codes(codes: tuple[str, ...]) -> str | None:
    """Map the association's discipline codes onto shared classification labels.

    A specific code always beats the bare ``MTB`` family marker, because the
    association puts the precise discipline in the next column. Only when no
    specific code is present does the caller fall back to the event title.
    """
    for code in codes:
        for known in _SORTED_DISCIPLINE_CODES:
            if code == known or code.startswith(f"{known}-"):
                return _DISCIPLINE_CODE_MAP[known]
    return None


def _has_mtb_family_marker(codes: tuple[str, ...]) -> bool:
    return _FAMILY_MARKER in codes


def _region_from_codes(codes: tuple[str, ...]) -> str | None:
    for code in codes:
        if code in _REGION_CODES:
            return _REGION_CODES[code]
    return None


def _event_format(duration_days: int, classification: str) -> str:
    if duration_days > 1:
        return "stage_race" if classification == "mtb_stage_race" else "multi_day"
    return "single_day"


def _resolve_date(value: str) -> tuple[date | None, str | None]:
    """Parse a calendar date, correcting obvious season typos.

    The published calendar contains typos such as a 2026 race printed as
    ``21.05.2065``. Silently trusting them would file the race decades away, and
    silently dropping it would lose a real race, so the season year is used and
    the correction is reported.
    """
    parsed = _parse_date(value)
    if parsed is None:
        return None, None
    if abs(parsed.year - SEASON) > 1:
        corrected = parsed.replace(year=SEASON)
        return corrected, f"date_year_corrected_from_{parsed.year}_to_{SEASON}"
    return parsed, None


def _parse_date(value: str | None) -> date | None:
    if not value or value == _CANCELLED_MARKER:
        return None
    try:
        return date(*(int(part) for part in value.split(".")[::-1]))  # type: ignore[misc]
    except (TypeError, ValueError):
        return None


def _known_gap_warnings() -> list[str]:
    """Warnings describing what this source structurally cannot provide."""
    return [
        "no_distance_data",
        "official_url_is_federation_calendar_pdf",
        "location_is_region_label_only",
    ]


from dataclasses import dataclass  # noqa: E402  (kept below the parsing helpers)
