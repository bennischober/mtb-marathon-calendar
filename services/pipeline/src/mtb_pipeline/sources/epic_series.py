"""Adapter for the Epic Series (epic-series.com), the IRONMAN-run stage races.

The Epic Series is the official organizer of the Absa Cape Epic, Swiss Epic,
Andorra Epic, 4Islands Epic and FNB Wines2Whales — the flagship multi-day MTB
stage races that aggregators like radsport-events do not list at all. The
races index page carries every race's date range and venue as plain text, and
each race page embeds schema.org JSON-LD with coordinates and description.

This is an ``official_organizer`` source: it outranks every aggregator.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date

from ..classification import EventSignals, classify
from ..http_client import FetchedDocument, Fetcher
from ..models import CourseVariant, Edition, Location, PriceEntry, Review, SourceRef
from ..normalize import (
    clean_text,
)
from ..normalize import (
    edition_id as make_edition_id,
)
from ..normalize import (
    event_id as make_event_id,
)
from .base import CollectOptions, SourceAdapter, SourceProfile

RACES_URL = "https://www.epic-series.com/races"

SUPPORTED_FIELDS = [
    "name",
    "description",
    "date_start",
    "date_end",
    "location",
    "course_variants",
    "status",
    "discipline",
]

# "March 21–28, 2027" — en dash between day numbers; single-day ranges exist too.
_DATE_RANGE = re.compile(
    r"^(?P<month>[A-Z][a-z]+) (?P<day1>\d{1,2})(?:[–-](?P<day2>\d{1,2}))?, (?P<year>20\d\d)$"
)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

# Race slugs on the site, used to build detail-page URLs.
_KNOWN_RACES = ("capeepic", "4islands", "andorra", "swissepic", "wines2whales")


def _parse_date_range(text: str) -> tuple[date, date] | None:
    match = _DATE_RANGE.match(text.strip())
    if match is None:
        return None
    month = _MONTHS.get(match.group("month").lower())
    if month is None:
        return None
    year = int(match.group("year"))
    day1 = int(match.group("day1"))
    day2 = int(match.group("day2") or match.group("day1"))
    try:
        start = date(year, month, day1)
        end = date(year, month, day2)
    except ValueError:
        return None
    if end < start:
        return None
    return start, end


# Summary strip on the race home page. The page renders three icon fields —
# Duration, Distance, Climbing — each as a label div followed by a value div
# whose first span holds the text ("5 Days", "300KM", "10000M").
# Entry-info pages show tier tables: "TIER 1 TIER 2 TIER 3 TIER 4" followed by
# window labels ("1/10 to 29/11") and price cells ("CHF 4,650"). The exact
# grid layout varies per race; we collect every currency amount with its
# nearest preceding context and the windows as validity notes.
_CURRENCY = re.compile(
    r"(CHF|EUR|€|USD|\$|GBP|£)\s*([\d.,]+)",
)

_PRICE_WINDOWS = re.compile(
    r"(\d{1,2}/\d{1,2})\s+to\s+(\d{1,2}/\d{1,2})",
)


def _entry_prices(document: FetchedDocument) -> list[dict]:
    """Pull price entries off an entry-info page, best effort.

    The tier tables are visually structured but semantically flat text, so
    this records each amount with its currency and, when identifiable, the
    tier window it belongs to. Whatever cannot be aligned is still recorded
    as an amount the source published.
    """
    try:
        html = document.content.decode("utf-8", errors="ignore")
    except AttributeError:
        return []
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    windows = _PRICE_WINDOWS.findall(text)
    prices: list[dict] = []
    for match in _CURRENCY.finditer(text):
        currency, amount = match.group(1), match.group(2)
        if currency in ("€", "$", "£"):
            currency = {"€": "EUR", "$": "USD", "£": "GBP"}[currency]
        # Skip obviously-not-entry prices (thousands-separated hotel packages
        # are still real prices; there is no reliable filter, so record all).
        window = None
        position = match.start()
        for start_text, end_text in windows:
            start_pos = text.find(f"{start_text} to {end_text}")
            if 0 <= start_pos < position:
                window = f"{start_text} to {end_text}"
        prices.append(
            {
                "amount": f"{currency} {amount}",
                "note": window,
            }
        )
    return prices


_FIELD_VALUE = re.compile(
    r'icon-field-label">(Duration|Distance|Climbing)</div>\s*'
    r"<div[^>]*>\s*<span[^>]*>([^<]+)</span>",
    re.I,
)


def _summary_stats(document: FetchedDocument) -> tuple[int, float, float] | None:
    """(days, total distance km, total climbing m) from the race home page."""
    try:
        html = document.content.decode("utf-8", errors="ignore")
    except AttributeError:
        return None
    fields = {
        match.group(1).lower(): match.group(2).strip()
        for match in _FIELD_VALUE.finditer(html)
    }
    days_match = re.search(r"(\d+)", fields.get("duration", ""))
    distance_match = re.search(r"([\d.,]+)", fields.get("distance", ""))
    climbing_match = re.search(r"([\d.,]+)", fields.get("climbing", ""))
    if not (days_match and distance_match and climbing_match):
        return None
    return (
        int(days_match.group(1)),
        float(distance_match.group(1).replace(",", "")),
        float(climbing_match.group(1).replace(",", "")),
    )


def _extract_json_ld(document: FetchedDocument) -> dict | None:
    try:
        html = document.content.decode("utf-8", errors="ignore")
    except AttributeError:
        return None
    match = re.search(
        r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S
    )
    if match is None:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    graph = data.get("@graph") if isinstance(data, dict) else None
    if not graph:
        return None
    for node in graph:
        if node.get("@type") == "SportsEvent":
            return node
    return None


def _country_code(address_country: str | None, fallback: str) -> str:
    """Map JSON-LD country names (or region digits) onto ISO codes."""
    mapping = {
        "switzerland": "CH",
        "andorra": "AD",
        "croatia": "HR",
        "south africa": "ZA",
        "germany": "DE",
        "austria": "AT",
        "italy": "IT",
        "france": "FR",
    }
    return mapping.get((address_country or "").strip().lower(), "")


class EpicSeriesAdapter(SourceAdapter):
    profile = SourceProfile(
        name="epic-series",
        homepage="https://www.epic-series.com/",
        endpoint=RACES_URL,
        priority="official_organizer",
        countries=("CH", "AD", "HR", "ZA"),
        cadence_hours=24 * 7,
        data_format="html",
        parser_status="verified",
        robots_status="allowed",
        terms_status="unconfirmed",
        notes=(
            "IRONMAN-run stage races (Cape Epic, Swiss Epic, Andorra Epic, "
            "4Islands, Wines2Whales). Index page text + JSON-LD. Dates are "
            "announced well ahead; distances follow later."
        ),
    )

    def fetch(self, fetcher: Fetcher, options: CollectOptions) -> list[FetchedDocument]:
        documents = [fetcher.get(RACES_URL)]
        for slug in _KNOWN_RACES:
            documents.append(fetcher.get(f"https://www.epic-series.com/races/{slug}"))
            documents.append(
                fetcher.get(f"https://www.epic-series.com/races/{slug}/entry-info")
            )
        return documents

    def parse(self, documents: Sequence[FetchedDocument]) -> list[Edition]:
        if not documents:
            return []
        index = documents[0]
        details = {
            document.url.rsplit("/", 1)[-1]: document
            for document in documents[1:]
        }
        entry_pages = {
            document.url.rsplit("/", 2)[-2]: document
            for document in documents[1:]
            if document.url.endswith("/entry-info")
        }

        editions: list[Edition] = []
        seen: set[tuple[str, date]] = set()
        for entry in self._index_entries(index):
            slug = entry["slug"]
            dates = _parse_date_range(entry["date_text"])
            if dates is None:
                continue
            start, end = dates
            detail = details.get(slug)
            ld = _extract_json_ld(detail) if detail else None

            name = clean_text(entry["name"])
            town, country_code = self._location(entry["location"], ld)
            if not name or not country_code:
                # A race whose country we cannot map is out of calendar scope;
                # skipping beats fabricating an invalid record.
                continue

            description = None
            lat = lng = None
            if ld:
                description = clean_text(str(ld.get("description") or "")) or None
                geo = ld.get("location", {}).get("geo") or {}
                try:
                    lat = float(geo.get("latitude"))
                    lng = float(geo.get("longitude"))
                except (TypeError, ValueError):
                    lat = lng = None

            # The page shows each race twice (hero card + menu card); keep
            # the first occurrence per slug and start date.
            identity = (slug, start)
            if identity in seen:
                continue
            seen.add(identity)
            event_id = make_event_id(self.name, name, country_code)
            edition_id = make_edition_id(event_id, start.year)
            source_ref = SourceRef(
                id=f"{self.name}:{slug}",
                url=f"https://www.epic-series.com/races/{slug}",
                retrieved_at=index.retrieved_at,
                supports=SUPPORTED_FIELDS,
                confidence=0.95,
                content_hash=detail.content_hash if detail else index.content_hash,
                native_id=slug,
                adapter=self.name,
            )
            stats = _summary_stats(detail) if detail else None
            span_days = (end - start).days + 1
            # The page's "Duration" counts riding days, which can differ from
            # the calendar span (rest days, transfer days). The span is the
            # factual calendar truth, so duration follows the dates and the
            # stats only feed the course totals.
            duration_days = span_days

            signals = EventSignals(
                discipline="ETAPPENRENNEN",
                category="MTB",
                duration_days=duration_days,
                title=name,
                description=description,
            )
            classification = classify(signals)
            prices = [
                PriceEntry.model_validate(price)
                for price in _entry_prices(entry_pages[slug])
            ] if slug in entry_pages else []
            variants: list[CourseVariant] = []
            if stats:
                variants.append(
                    CourseVariant(
                        id=f"{edition_id}-total",
                        name="Total course",
                        distance_km=stats[1],
                        elevation_m=int(stats[2]),
                        technical_notes=f"{stats[0]} riding days over {span_days} calendar days",
                        source_refs=[source_ref.id],
                    )
                )
            editions.append(
                Edition(
                    id=edition_id,
                    event_id=event_id,
                    edition=start.year,
                    name=name,
                    description=description,
                    date_start=start,
                    date_end=end,
                    format="stage_race",
                    duration_days=duration_days,
                    location=Location(
                        name=town or name,
                        country=country_code,
                        lat=lat,
                        lng=lng,
                    ),
                    course_variants=variants,
                    prices=prices,
                    official_url=f"https://www.epic-series.com/races/{slug}",
                    source_refs=[source_ref],
                    classification=classification.classification,
                    classification_confidence=classification.confidence,
                    extraction_warnings=sorted(set(classification.warnings)),
                    review=Review(),
                    status="announced",
                    last_verified=index.retrieved_at.date(),
                )
            )
        return editions

    def _index_entries(self, document: FetchedDocument) -> list[dict[str, str]]:
        """Pull (slug, date, name, location) triples off the races index page.

        The page renders each race card as: date line, name line,
        "Town, Country" line, then the link to the race page — so the slug is
        the first race href *after* the date, not before it.
        """
        html = document.content.decode("utf-8", errors="ignore")

        href_pattern = r'href="(?:https://www\.epic-series\.com)?/races/([a-z0-9-]+)["\']'
        date_pattern = r"([A-Z][a-z]+ \d{1,2}[–-]\d{1,2}, 20\d\d)(?=<)"
        tokens = [
            (match.start(), "slug", match.group(1))
            for match in re.finditer(href_pattern, html)
        ] + [
            (match.start(), "date", match.group(1))
            for match in re.finditer(date_pattern, html)
        ]
        tokens.sort()

        entries: list[dict[str, str]] = []
        for position, (offset, kind, value) in enumerate(tokens):
            if kind != "date":
                continue
            slug = next(
                (
                    token[2]
                    for token in tokens[position + 1 :]
                    if token[1] == "slug"
                ),
                "",
            )
            # The card's text lines follow the date in the tag-stripped flow.
            text = re.sub(r"<[^>]+>", "\n", html[offset : offset + 2000])
            text = re.sub(r"\n{2,}", "\n", text)
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            name = lines[1] if len(lines) > 1 else ""
            if name == "See Race Details":
                continue
            # Hero cards split the name ("Absa" / "Cape Epic"); menu cards
            # keep it together ("Absa Cape Epic"). Join a short first line
            # with the following line so both renderings converge.
            if len(name) <= 6 and len(lines) > 2 and lines[2] not in ("See Race Details", ""):
                name = f"{name} {lines[2]}".strip()
                lines[2:3] = [] if len(lines) > 3 else lines[2:3]
            entries.append(
                {
                    "slug": slug,
                    "date_text": value,
                    "name": name,
                    "location": lines[2] if len(lines) > 2 else "",
                }
            )
        return entries

    def _location(self, text: str, ld: dict | None) -> tuple[str, str | None]:
        """Split 'Davos, Switzerland' into town and ISO country code."""
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if len(parts) >= 2:
            country = _country_code(parts[-1], "")
            if country:
                return " ".join(parts[:-1]), country
        if ld:
            address = ld.get("location", {}).get("address", {})
            country = _country_code(address.get("addressCountry"), "")
            if country:
                return address.get("addressRegion") or "", country
        return text, ""
