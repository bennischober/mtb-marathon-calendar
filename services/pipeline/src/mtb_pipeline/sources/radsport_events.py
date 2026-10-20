"""Adapter for radsport-events.de.

The public site is a client-side rendered React app with no server-rendered
HTML, so HTML scraping is not an option. The same application reads a public
JSON API that returns fully structured race records, which is the better
source anyway: it exposes a discipline label, a category, and per-distance
course data.

Note that this aggregator is mostly road, gravel and cyclocross, so the
majority of its MTB records are noise for this calendar. Filtering is done by
the shared classifier, not here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from urllib.parse import urlparse

from ..classification import EventSignals, classify
from ..http_client import FetchedDocument, Fetcher
from ..models import (
    CourseVariant,
    Edition,
    Location,
    Review,
    SourceRef,
)
from ..normalize import (
    clean_text,
    course_variant_id,
    distance_label,
)
from ..normalize import (
    edition_id as make_edition_id,
)
from ..normalize import (
    event_id as make_event_id,
)
from .base import CollectOptions, SourceAdapter, SourceProfile

API_BASE = "https://api.radsport-events.de/api/v1"
SITE_BASE = "https://radsport-events.de"
EVENTS_ENDPOINT = f"{API_BASE}/events"
LISTING_PATH = "/events/{id}"

# The API silently caps page size at 100 and returns fewer rows than asked for
# when a larger size is requested, so asking for more only wastes bandwidth.
PAGE_SIZE = 100
MAX_PAGES = 50

SUPPORTED_FIELDS = [
    "name",
    "description",
    "start_time",
    "date_start",
    "date_end",
    "location",
    "course_variants",
    "status",
    "discipline",
]

# Scope countries from docs/PLAN.md. Records outside these are still collected
# but dropped by the caller, so the adapter itself stays country-agnostic.
SUPPORTED_COUNTRIES = ("DE", "AT", "CH", "IT", "FR")

_STATUS_MAP = {
    "OPEN": "registration_open",
    "ONSITE_ONLY": "registration_open",
    "CLOSED": "unknown",
    "UNKNOWN": "unknown",
}


class RadsportEventsAdapter(SourceAdapter):
    profile = SourceProfile(
        name="radsport-events",
        homepage=SITE_BASE,
        endpoint=EVENTS_ENDPOINT,
        priority="specialist_aggregator",
        countries=SUPPORTED_COUNTRIES,
        cadence_hours=24,
        data_format="json",
        parser_status="verified",
        robots_status="site allows public paths; API host returns 403, treated as allow",
        terms_status="unconfirmed",
        notes=(
            "Road and gravel aggregator first, so most MTB records are noise and "
            "are dropped by the classifier. Valuable for one-day ultras over "
            "200 km and multi-day stage races that distance filters would miss."
        ),
    )

    def fetch(self, fetcher: Fetcher, options: CollectOptions) -> list[FetchedDocument]:
        """Page through the MTB category until the API runs out of records.

        ``options.limit`` caps the number of *records* fetched, so a small limit
        also shrinks the requested page size instead of downloading a full page
        and throwing most of it away.
        """
        documents: list[FetchedDocument] = []
        fetched_records = 0

        for page in range(MAX_PAGES):
            if options.max_documents is not None and len(documents) >= options.max_documents:
                break
            if options.limit is not None and fetched_records >= options.limit:
                break

            size = PAGE_SIZE
            if options.limit is not None:
                size = max(1, min(PAGE_SIZE, options.limit - fetched_records))

            document = fetcher.get(
                EVENTS_ENDPOINT,
                params=build_page_params(page, size),
                accept="application/json",
                snapshot_name=f"events-page-{page}",
            )
            documents.append(document)

            payload = document.json()
            if not isinstance(payload, dict):
                break

            content = payload.get("content") or []
            fetched_records += len(content)

            total_pages = int(payload.get("totalPages") or 0)
            if not content or page + 1 >= total_pages:
                break

        return documents

    def parse(self, documents: Sequence[FetchedDocument]) -> list[Edition]:
        editions: list[Edition] = []
        for document in documents:
            payload = document.json()
            if not isinstance(payload, dict):
                continue
            for record in payload.get("content") or []:
                if not isinstance(record, dict):
                    continue
                edition = self._to_edition(record, document)
                if edition is not None:
                    editions.append(edition)
        return editions

    def _to_edition(self, record: dict, document: FetchedDocument) -> Edition | None:
        native_id = record.get("id")
        title = clean_text(str(record.get("title") or ""))
        event_date = _parse_date(record.get("eventDate"))
        if not native_id or not title or event_date is None:
            return None

        country = (record.get("country") or "").upper() or None
        if country is None:
            # The ISO country code keys dedupe buckets and feeds Location, so
            # inventing "DE" would corrupt identity matching. Skip instead.
            return None
        distances = [
            distance
            for distance in (record.get("distances") or [])
            if isinstance(distance, dict) and _positive(distance.get("distanceKm"))
        ]
        max_distance = max((float(distance["distanceKm"]) for distance in distances), default=None)
        end_date = _parse_date(record.get("endDate"))
        duration_days = (
            (end_date - event_date).days + 1 if end_date and end_date >= event_date else 1
        )

        discipline = record.get("eventType")
        classification = classify(
            EventSignals(
                discipline=discipline,
                category=record.get("category"),
                max_distance_km=max_distance,
                duration_days=duration_days,
                is_cancelled=bool(record.get("cancelled")),
                title=title,
                description=record.get("description"),
            )
        )

        stable_event_id = make_event_id(self.name, title, country)
        record_edition_id = make_edition_id(stable_event_id, event_date.year)
        source_ref = SourceRef(
            id=f"{self.name}:event:{native_id}",
            url=f"{SITE_BASE}{LISTING_PATH.format(id=native_id)}",
            retrieved_at=document.retrieved_at,
            supports=SUPPORTED_FIELDS,
            confidence=classification.confidence,
            content_hash=document.content_hash,
            native_id=str(native_id),
            adapter=self.name,
        )

        return Edition(
            id=record_edition_id,
            event_id=stable_event_id,
            edition=event_date.year,
            name=title,
            description=_clean_description(record.get("description")),
            start_time=_start_time(record.get("startTime"), record.get("startTimeEnd")),
            date_start=event_date,
            date_end=end_date,
            format=self._format(record, duration_days),
            duration_days=duration_days if duration_days > 1 else None,
            location=Location(
                name=clean_text(
                    str(record.get("city") or record.get("locationName") or country or "Unknown")
                ),
                country=country,
                region=record.get("bundesland"),
                lat=_coordinate(record.get("latitude")),
                lng=_coordinate(record.get("longitude")),
            ),
            course_variants=[
                self._course_variant(record_edition_id, distance, source_ref.id)
                for distance in distances
            ],
            official_url=_absolute_url(record.get("organizerUrl")) or str(source_ref.url),
            source_refs=[source_ref],
            classification=classification.classification,
            classification_confidence=classification.confidence,
            extraction_warnings=sorted(set(classification.warnings)),
            review=Review(),
            status=self._status(record),
            last_verified=document.retrieved_at.date(),
        )

    def _format(self, record: dict, duration_days: int) -> str:
        # Format is factual here: day count from dates, point-to-point only
        # when the source text states it. Whether a multi-day event is ridden
        # as stages is a judgement the LLM classification stage makes.
        if duration_days > 1:
            return "multi_day"
        description = (record.get("description") or "").lower()
        if "point to point" in description or "p2p" in description:
            return "point_to_point"
        return "single_day"

    def _course_variant(
        self, record_edition_id: str, distance: dict, source_ref_id: str
    ) -> CourseVariant:
        label = clean_text(str(distance.get("label") or ""))
        distance_km = float(distance["distanceKm"])
        name = label or distance_label(distance_km)
        elevation = distance.get("elevationMeters")
        notes = []
        if distance.get("hasGpx"):
            notes.append("GPX track available")
        if distance.get("routeUrl"):
            notes.append("route published by organizer")
        return CourseVariant(
            id=course_variant_id(record_edition_id, name, distance_km),
            name=name,
            distance_km=distance_km,
            elevation_m=float(elevation) if _positive(elevation) else None,
            start_location=clean_text(str(distance.get("startLocation") or "")) or None,
            finish_location=clean_text(str(distance.get("finishLocation") or "")) or None,
            technical_notes="; ".join(notes) or None,
            availability="announced" if distance.get("hasGpx") else "unknown",
            status="unknown",
            source_refs=[source_ref_id],
        )

    def _status(self, record: dict) -> str:
        if record.get("cancelled"):
            return "cancelled"
        if record.get("tentative"):
            return "announced"
        if record.get("fullyBooked"):
            return "sold_out"
        return _STATUS_MAP.get(
            str(record.get("registrationStatus") or "UNKNOWN").upper(), "unknown"
        )


def build_page_params(page: int, size: int = PAGE_SIZE) -> dict[str, object]:
    return {"page": page, "size": size, "category": "MTB", "status": "PUBLISHED"}


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _positive(value: object) -> bool:
    try:
        return float(value) > 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _start_time(start: object, end: object) -> str | None:
    """Advertised start time, kept as the source writes it."""
    start_text = clean_text(str(start or ""))
    end_text = clean_text(str(end or ""))
    if start_text and end_text:
        return f"{start_text} - {end_text}"
    return start_text or None


def _clean_description(value: object) -> str | None:
    """Keep the source description for the LLM second opinion."""
    text = clean_text(str(value or "")).strip()
    return text or None


def _coordinate(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if -180 <= number <= 180 else None


def _absolute_url(value: object) -> str | None:
    """Some organizer URLs arrive without a scheme, e.g. "example.de/race"."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if not parsed.netloc:
        return None
    return candidate
