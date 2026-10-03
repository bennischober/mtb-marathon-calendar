import json
from datetime import UTC, date, datetime
from pathlib import Path

from .curated import load_overlays
from .curated import merge as merge_overlays
from .dedupe import MergeConflict, merge_editions
from .known_events import check_known_events, load_known_events
from .models import Edition
from .sources import all_profiles


def load_editions(path: Path) -> list[Edition]:
    """Load editions from either a bare array or a generated export envelope.

    Fixtures are stored as a plain array of editions, while `export` wraps them
    in an object with `schema_version` and `generated_at`. Accepting both keeps
    `export` output re-ingestable and re-validatable.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("events")
    if not isinstance(payload, list):
        raise ValueError("input must be a JSON array of editions or an export object")
    return [Edition.model_validate(item) for item in payload]


def build_export(editions: list[Edition], generated_at: datetime | None = None) -> dict:
    timestamp = generated_at or datetime.now(UTC)
    sorted_editions = sorted(
        editions,
        # Chronological first; within a day, the best-evidenced race leads;
        # the id keeps the order total and deterministic.
        key=lambda item: (
            item.date_start,
            -max((ref.confidence for ref in item.source_refs), default=0.0),
            item.id,
        ),
    )
    return {
        "schema_version": "1.0",
        "generated_at": timestamp.isoformat(),
        "events": [_dump_edition(edition) for edition in sorted_editions],
    }


def _dump_edition(edition: Edition) -> dict:
    payload = edition.model_dump(mode="json", exclude_none=True)
    if not payload.get("extraction_warnings"):
        payload.pop("extraction_warnings", None)
    return payload


def build_published_export(
    editions: list[Edition],
    generated_at: datetime | None = None,
    curated_dir: Path | None = None,
    merge_duplicates: bool = True,
    conflicts: list[MergeConflict] | None = None,
) -> tuple[dict, list[str]]:
    """Build the site-facing artifact.

    Three stages run in a fixed order:

    1. the machine dump of what is in the database
    2. deduplication and conflict resolution across sources
    3. curated overlays, so a human decision always has the last word

    Whichever way an edition reached the database, and however many sources
    contributed to it, corrections are applied last and never merged away.

    Pass a list as ``conflicts`` to collect the structured disagreements, each
    carrying the edition it affects. The returned log already contains them as
    text; the caller needs the structured form to store them.
    """
    payload = build_export(editions, generated_at=generated_at)
    events = payload["events"]
    log: list[str] = []

    if merge_duplicates and events:
        result = merge_editions(events, source_priority=_source_priorities())
        events = result.editions
        log.extend(f"merged: {line}" for line in result.log)
        log.extend(f"conflict: {conflict}" for conflict in result.conflicts)
        if conflicts is not None:
            conflicts.extend(result.conflicts)

    if curated_dir is not None:
        overlays = load_overlays(curated_dir)
        events, applied = merge_overlays(events, overlays, generated_at=generated_at)
        log.extend(f"curated: {line}" for line in applied)
        known = load_known_events(curated_dir)
        if known:
            report = check_known_events(known, events)
            log.extend(f"known: {line}" for line in report.log_lines())

    # Overlays and merges operate on plain dicts and are the only unvalidated
    # path into the artifact; re-validate so a bad overlay cannot ship. The
    # dicts are kept as-is: build_export already produced canonical JSON, and
    # re-dumping would silently rewrite datetime serialization.
    for event in events:
        Edition.model_validate(event)
    payload["events"] = events
    return payload, log


def write_export(editions: list[Edition], path: Path, generated_at: datetime | None = None) -> dict:
    payload = build_export(editions, generated_at=generated_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _source_priorities() -> dict[str, int]:
    """Map every registered adapter name to its registry priority rank.

    ``merge_editions`` keys its priority table by adapter name, while
    ``PRIORITY_RANK`` is keyed by the category names. Without this mapping every
    real adapter would silently fall back to the default rank and source
    priority would never actually be applied.
    """
    return {profile.name: profile.priority_rank for profile in all_profiles()}

_SERIES_PREFIXES = ("bawue-radsportverband", "radsport-events", "epic-series")


def _series_slug(event_id: str) -> str:
    """URL slug matching the site's routing: event_id minus source prefix."""
    for prefix in _SERIES_PREFIXES:
        if event_id.startswith(f"{prefix}-"):
            return event_id[len(prefix) + 1 :]
    return event_id


def build_ics(editions: list[Edition], generated_at: datetime | None = None) -> str:
    """A full-calendar iCalendar feed from the exported editions.

    One VEVENT per edition with the official URL as the canonical link, so
    calendar clients deep-link into the site. Stateless fields only: whatever
    is in the export, nothing invented.
    """
    from datetime import datetime as _dt

    stamp = (generated_at or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//mtb-marathon-calendar//export//EN",
        "CALSCALE:GREGORIAN",
    ]
    for edition in sorted(editions, key=lambda item: item.date_start):
        end = edition.date_end or edition.date_start
        # DTEND is exclusive in iCalendar; a single-day race ends next day.
        exclusive_end = end.toordinal() + 1
        end_date = date.fromordinal(exclusive_end)
        summary = f"{edition.name} ({edition.location.country})"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{edition.id}@mtb-marathon-calendar",
                f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{edition.date_start.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{end_date.strftime('%Y%m%d')}",
                f"SUMMARY:{_ics_escape(summary)}",
                f"URL:{edition.official_url}",
                # X-CALENDAR-LINK is our permanent per-edition page
                # (/events/<slug>/<year>); URL stays the official organizer
                # page so calendar clients deep-link out.
                f"X-CALENDAR-LINK:/events/{_series_slug(edition.event_id)}/{edition.edition}",
                f"LOCATION:{_ics_escape(edition.location.name)}, {edition.location.country}",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )
