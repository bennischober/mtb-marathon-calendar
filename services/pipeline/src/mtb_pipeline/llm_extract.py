"""Generic LLM extraction: turn any event page into a validated Edition.

The per-site parsing layer (regexes, discipline-code tables, column maps)
is dead. Verified 2026-10-03 across French, German and Italian organizer
pages: one generic prompt extracts the same facts a hand-written parser
did, in any language. A "source adapter" therefore shrinks to: fetch the
right pages, hand the text to :func:`extract_edition`, validate.

What the LLM does NOT replace (and why this module stays thin):

* fetching/politeness — robots, delays, raw snapshots stay in the fetcher
* discovery of which pages to fetch — mechanical navigation, per source
* validation and provenance — Pydantic models and SourceRef stay exact

Known LLM failure modes, encoded in the prompt:

* dates written in any language (Italian "maggio", German "Juni") must be
  converted, not skipped
* the model needs today's date to resolve which edition "29 maggio" means
* null is only for facts the text truly lacks — never for facts it failed
  to read
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .llm import LLMClassifier
from .models import Edition, Location, PriceEntry, Review, SourceRef
from .normalize import edition_id, event_id

EXTRACT_SYSTEM_PROMPT = """You extract sport event facts from web page text for a
mountain bike marathon calendar.

Rules:
- Dates may be written in any language (Italian "maggio", German "Juni", French
  "juin"). Convert them to ISO YYYY-MM-DD.
- Today is {today}. Resolve relative or day-month-only dates to the year the
  event actually happens: a date that already passed this year means the next
  edition is meant, when the page presents it as upcoming.
- Distances and elevations keep their numbers (km and m); ranges or multiple
  courses become separate course entries.
- Prices keep their currency as written ("EUR 390", "CHF 4,100").
- Use null only when the text truly lacks the fact. Never invent an event,
  a date, or a URL.
- If the page does not describe a specific sport event (it is a blog index, a
  shop, a general info page), return {{"event": null}}.

Output JSON only: {{"event": {{"name": ..., "date_start": ..., "date_end": ...,
"start_time": ... (as written, e.g. "07:30" or "09:00 - 12:00"),
"location_name": ..., "country": "...", "region": ...,
"format": "single_day"|"multi_day"|"stage_race"|"point_to_point",
"duration_days": ..., "organizer": ..., "official_url": ...,
"courses": [{{"name": ..., "distance_km": ..., "elevation_m": ...}}],
"prices": [{{"label": ..., "amount": ..., "note": ...}}]}}}}"""


class ExtractedCourse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    distance_km: float | None = None
    elevation_m: float | None = None


class ExtractedPrice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str | None = None
    amount: str | None = None
    note: str | None = None


class ExtractedEvent(BaseModel):
    """The LLM's raw extraction, before identity/provenance are attached."""

    model_config = ConfigDict(extra="ignore")

    name: str
    date_start: date
    date_end: date | None = None
    start_time: str | None = None
    location_name: str | None = None
    country: str | None = None
    region: str | None = None
    format: str | None = None
    duration_days: int | None = None
    organizer: str | None = None
    official_url: str | None = None
    courses: list[ExtractedCourse] = Field(default_factory=list)
    prices: list[ExtractedPrice] = Field(default_factory=list)


class ExtractResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: ExtractedEvent | None = None


class ExtractError(ValueError):
    """The page yielded no usable event."""


def page_text(content: bytes, limit: int = 12_000) -> str:
    """Strip a page to plain text for the prompt.

    Scripts and styles first, then tags, then entities. Long pages are cut at
    ``limit`` characters; the informative head of an event page survives that
    cut in practice, and prompt size stays bounded.
    """
    import re

    html = content.decode("utf-8", errors="ignore")
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&[a-z#0-9]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


EXTRACT_PARTIAL_PROMPT = """You extract PARTIAL facts about one sport event from a
subpage (a course list, a price page, a news post). The page may not name the event or
carry dates — extract only what is present.
- Courses are physically distinct ROUTES. Registration categories that share one route
  (age groups "U19"/"Hobby", e-bike classes, "Duo" variants) are NOT separate courses:
  list the route once. Only list several courses when their distances genuinely differ.
- Distances/elevations keep their numbers (km and m). Prices keep their currency
  ("EUR 390"). Descriptions stay in the page's language, max 3 sentences.
Output JSON only: {"courses": [{"name": ..., "distance_km": ..., "elevation_m": ...}],
"prices": [{"label": ..., "amount": ..., "note": ...}], "description": "..."}
Use empty lists when nothing of that kind is present. Never invent numbers."""


class PartialResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    courses: list[ExtractedCourse] = Field(default_factory=list)
    prices: list[ExtractedPrice] = Field(default_factory=list)
    description: str | None = None


def extract_partial(classifier: LLMClassifier, text: str) -> PartialResult | None:
    """Extract partial facts (courses/prices/description) from a subpage."""
    payload = {
        "model": classifier.model_name,
        "messages": [
            {"role": "system", "content": EXTRACT_PARTIAL_PROMPT},
            {"role": "user", "content": text},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    try:
        answer = classifier.chat_raw(payload)
    except Exception:
        return None
    try:
        return PartialResult.model_validate(json.loads(answer))
    except (json.JSONDecodeError, ValidationError):
        return None


def extract_raw(
    classifier: LLMClassifier,
    text: str,
    *,
    today: date | None = None,
) -> ExtractedEvent | None:
    """One LLM call: page text → raw event facts (or None)."""
    today = today or datetime.now(UTC).date()
    payload = {
        "model": classifier.model_name,
        "messages": [
            {"role": "system", "content": EXTRACT_SYSTEM_PROMPT.format(today=today.isoformat())},
            {"role": "user", "content": text},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    answer = classifier.chat_raw(payload)
    try:
        result = ExtractResult.model_validate(json.loads(answer))
    except (json.JSONDecodeError, ValidationError):
        return None
    return result.event


def extract_edition(
    classifier: LLMClassifier,
    *,
    source_name: str,
    url: str,
    content: bytes,
    retrieved_at: datetime,
    content_hash: str,
    confidence: float = 0.85,
    today: date | None = None,
) -> Edition | None:
    """Full pipeline for one page: text → facts → validated Edition.

    Returns ``None`` when the page is not a specific event page (the honest
    outcome for blog indexes and shop pages). Raises only on LLM transport
    errors, which the caller records like any other fetch failure.
    """
    extracted = extract_raw(classifier, page_text(content), today=today)
    if extracted is None:
        return None

    # Identity: source-namespaced, year-stripped event id.
    country = _iso_country(extracted.country)
    event = event_id(source_name, extracted.name, country)
    edition_year = extracted.date_start.year

    supports = [
        field
        for field, value in (
            ("name", extracted.name),
            ("date_start", extracted.date_start),
            ("date_end", extracted.date_end),
            ("start_time", extracted.start_time),
            ("location", extracted.location_name),
            ("course_variants", extracted.courses),
            ("prices", extracted.prices),
            ("organizer", extracted.organizer),
            ("status", None),
        )
        if value
    ]
    source_ref = SourceRef(
        id=f"{source_name}:extract:{content_hash[:16]}",
        url=url,
        retrieved_at=retrieved_at,
        supports=supports,
        confidence=confidence,
        content_hash=content_hash,
        adapter=source_name,
    )

    format_value = _normalize_format(extracted.format, extracted.duration_days)
    duration = extracted.duration_days or (
        (extracted.date_end - extracted.date_start).days + 1 if extracted.date_end else 1
    )
    courses = [
        _course_variant(event, edition_year, course, source_ref.id)
        for course in extracted.courses
        if course.distance_km
    ]
    prices = [
        PriceEntry(
            label=price.label,
            amount=price.amount,
            note=price.note,
        )
        for price in extracted.prices
        if price.amount
    ]

    return Edition(
        id=edition_id(event, edition_year),
        event_id=event,
        edition=edition_year,
        name=extracted.name,
        description=None,
        date_start=extracted.date_start,
        date_end=extracted.date_end,
        start_time=extracted.start_time,
        format=format_value,
        duration_days=duration if duration > 1 else None,
        location=Location(
            name=extracted.location_name or extracted.name,
            country=country or "DE",
            region=extracted.region,
        ),
        course_variants=courses,
        prices=prices,
        official_url=_safe_url(extracted.official_url) or url,
        source_refs=[source_ref],
        classification="mtb_candidate",
        classification_confidence=0.6,
        extraction_warnings=["llm_extracted"],
        review=Review(),
        status="announced" if extracted.date_start >= (today or date.today()) else "unknown",
        last_verified=retrieved_at.date(),
    )


_COUNTRY_NAMES = {
    "germany": "DE",
    "deutschland": "DE",
    "austria": "AT",
    "oesterreich": "AT",
    "österreich": "AT",
    "switzerland": "CH",
    "schweiz": "CH",
    "suisse": "CH",
    "italy": "IT",
    "italia": "IT",
    "france": "FR",
    "french": "FR",
    "andorra": "AD",
}


def _iso_country(value: str | None) -> str | None:
    """Map a country name or code onto ISO 3166-1 alpha-2."""
    if not value:
        return None
    cleaned = value.strip().lower()
    if len(cleaned) == 2 and cleaned.isalpha():
        return cleaned.upper()
    return _COUNTRY_NAMES.get(cleaned)


def _safe_url(value: str | None) -> str | None:
    """Keep only plausible http(s) URLs; the LLM sometimes writes junk."""
    if not value:
        return None
    candidate = value.strip()
    if not candidate.startswith(("http://", "https://")):
        candidate = f"https://{candidate}"
    try:
        from pydantic import AnyHttpUrl

        return str(AnyHttpUrl(candidate))
    except Exception:
        return None


def _normalize_format(raw: str | None, duration_days: int | None) -> str:
    value = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    known = {"single_day", "multi_day", "stage_race", "point_to_point"}
    if value in known:
        return value
    if (duration_days or 1) > 1:
        return "multi_day"
    return "single_day"


def _course_variant(
    event: str,
    edition_year: int,
    course: ExtractedCourse,
    source_ref_id: str,
):
    from .models import CourseVariant
    from .normalize import course_variant_id

    return CourseVariant(
        id=course_variant_id(
            edition_id(event, edition_year),
            course.name or f"{course.distance_km:g}km",
            course.distance_km,
        ),
        name=course.name or f"{course.distance_km:g} km",
        distance_km=course.distance_km,
        elevation_m=course.elevation_m,
        source_refs=[source_ref_id],
    )
