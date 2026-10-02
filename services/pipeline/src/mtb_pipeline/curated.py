"""Curated overlays merged over generated events at publish time.

Generated data and human decisions are stored separately on purpose. Collection
runs re-derive every field on every source, so anything a human decided cannot
live in the same row without eventually being overwritten.

The merge is therefore explicit and one-directional:

* ``data/generated/events.json`` is machine-written and disposable
* ``data/curated/*.json`` is human-written and authoritative

An overlay may correct an automated field, attach an editorial profile, or
record a review decision. It can never invent a new event, because a record that
automation never found has no provenance to attach an opinion to. That keeps a
typo in a curated file from putting a fictional race on the site.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import EditorialProfile, Review

# Fields an overlay is allowed to correct. Deliberately excludes identity and
# provenance fields: rewriting an id or a source reference would break the
# link back to the evidence that produced the record.
CORRECTABLE_FIELDS = frozenset(
    {
        "name",
        "date_start",
        "date_end",
        "duration_days",
        "format",
        "classification",
        "classification_confidence",
        "status",
        "official_url",
        "registration_url",
    }
)

# Location is corrected as a whole object rather than field by field, so an
# overlay never leaves a country from one place paired with a name from another.
CORRECTABLE_LOCATION_FIELDS = frozenset({"name", "region", "country", "lat", "lng"})


class OverlayError(ValueError):
    """A curated overlay is malformed or points at something that does not exist."""


@dataclass(frozen=True)
class CuratedOverlay:
    """One human-authored correction to a single generated edition."""

    edition_id: str
    fields: dict[str, Any] = field(default_factory=dict)
    location: dict[str, Any] = field(default_factory=dict)
    course_variants: list[dict[str, Any]] = field(default_factory=list)
    review: Review = field(default_factory=Review)
    editorial: EditorialProfile | None = None
    #: what this overlay is for, surfaced in the publish log
    reason: str | None = None


def load_overlays(directory: Path) -> dict[str, CuratedOverlay]:
    """Read every ``*.json`` overlay in a directory.

    Later files override earlier ones for the same edition, so an overlay can be
    split per concern. A duplicate edition across files is not an error because
    the newest file is authoritative by filename order.
    """
    overlays: dict[str, CuratedOverlay] = {}
    if not directory.exists():
        return overlays

    overlay_paths = sorted(
        path for path in directory.glob("*.json") if path.name != "known-events.json"
    )
    for path in overlay_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            overlay = _parse_overlay(entry, path)
            overlays[overlay.edition_id] = overlay
    return overlays


def _parse_overlay(entry: Any, path: Path) -> CuratedOverlay:
    if not isinstance(entry, dict):
        raise OverlayError(f"{path}: overlay entries must be JSON objects")

    edition_id = entry.get("edition_id") or entry.get("id")
    if not isinstance(edition_id, str) or not edition_id:
        raise OverlayError(f"{path}: overlay needs an 'edition_id' string")

    fields = {key: value for key, value in entry.items() if key in CORRECTABLE_FIELDS}

    unknown = (
        set(entry)
        - CORRECTABLE_FIELDS
        - {
            "edition_id",
            "id",
            "location",
            "course_variants",
            "review",
            "editorial",
            "reason",
        }
    )
    if unknown:
        raise OverlayError(
            f"{path}: overlay for {edition_id!r} may not change {sorted(unknown)}; "
            f"correctable fields are {sorted(CORRECTABLE_FIELDS)}"
        )

    location = entry.get("location") or {}
    if not isinstance(location, dict):
        raise OverlayError(f"{path}: 'location' must be an object")
    unknown_location = set(location) - CORRECTABLE_LOCATION_FIELDS
    if unknown_location:
        raise OverlayError(f"{path}: location overlay may not change {sorted(unknown_location)}")

    review = _parse_review(entry.get("review"), path, edition_id)
    editorial = _parse_editorial(entry.get("editorial"), path, edition_id)

    course_variants = entry.get("course_variants") or []
    if not isinstance(course_variants, list):
        raise OverlayError(f"{path}: 'course_variants' must be an array")

    return CuratedOverlay(
        edition_id=edition_id,
        fields=fields,
        location=location,
        course_variants=course_variants,
        review=review,
        editorial=editorial,
        reason=entry.get("reason"),
    )


def _parse_review(value: Any, path: Path, edition_id: str) -> Review:
    if value is None:
        return Review()
    if not isinstance(value, dict):
        raise OverlayError(f"{path}: 'review' must be an object")
    unknown = set(value) - {"status", "reason", "reviewed_by", "reviewed_at"}
    if unknown:
        raise OverlayError(f"{path}: review may not contain {sorted(unknown)}")
    try:
        return Review.model_validate(value)
    except ValueError as exc:
        raise OverlayError(f"{path}: invalid review for {edition_id!r}: {exc}") from exc


def _parse_editorial(value: Any, path: Path, edition_id: str) -> EditorialProfile | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise OverlayError(f"{path}: 'editorial' must be an object")
    try:
        return EditorialProfile.model_validate(value)
    except ValueError as exc:
        raise OverlayError(f"{path}: invalid editorial for {edition_id!r}: {exc}") from exc


def merge(
    editions: list[dict[str, Any]],
    overlays: dict[str, CuratedOverlay],
    *,
    generated_at: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply overlays to generated editions.

    Returns the merged editions and a log of what was applied. Overlays whose
    ``edition_id`` matches nothing are reported as skipped rather than silently
    dropped, because that usually means a stale overlay after an id change.
    """
    merged: list[dict[str, Any]] = []
    applied: list[str] = []
    seen: set[str] = set()

    for edition in editions:
        edition_id = edition.get("id")
        overlay = overlays.get(edition_id) if isinstance(edition_id, str) else None

        if overlay is None:
            merged.append(dict(edition))
            continue

        seen.add(edition_id)
        merged.append(_apply(edition, overlay, applied, generated_at))

    for edition_id in sorted(set(overlays) - seen):
        applied.append(f"skipped overlay for unknown edition {edition_id!r}")

    return merged, applied


def _apply(
    edition: dict[str, Any],
    overlay: CuratedOverlay,
    applied: list[str],
    generated_at: datetime | None,
) -> dict[str, Any]:
    result = dict(edition)

    for key, value in overlay.fields.items():
        if edition.get(key) != value:
            applied.append(f"{overlay.edition_id}: corrected {key}")
        result[key] = value

    if overlay.location:
        result["location"] = {**edition.get("location", {}), **overlay.location}

    if overlay.course_variants:
        result["course_variants"] = _merge_course_variants(
            edition.get("course_variants") or [], overlay.course_variants, overlay.edition_id
        )
        applied.append(f"{overlay.edition_id}: corrected course variants")

    # A review is a human decision, so it always wins over the automated
    # `unreviewed` default, and the timestamp defaults to the publish time when
    # the reviewer did not record one.
    if overlay.review.status != "unreviewed":
        result["review"] = overlay.review.model_dump(mode="json", exclude_none=True)
        if overlay.review.reviewed_at is None and generated_at is not None:
            result["review"]["reviewed_at"] = generated_at.isoformat()
        applied.append(f"{overlay.edition_id}: review set to {overlay.review.status}")

    if overlay.editorial is not None:
        editorial = overlay.editorial.model_dump(mode="json", exclude_none=True)
        if overlay.editorial.reviewed_at is None and generated_at is not None:
            editorial["reviewed_at"] = generated_at.isoformat()
        result["editorial_profile"] = editorial
        applied.append(f"{overlay.edition_id}: editorial profile attached")

    return result


def _merge_course_variants(
    existing: list[dict[str, Any]],
    corrections: list[dict[str, Any]],
    edition_id: str,
) -> list[dict[str, Any]]:
    """Correct variants in place, matching on id first and name second.

    An overlay cannot add or remove variants: course sets are evidence, not
    opinion, and a wrong count is better caught in review than silently applied.
    """
    by_id = {item.get("id"): index for index, item in enumerate(existing)}
    by_name = {item.get("name"): index for index, item in enumerate(existing)}

    corrected = [dict(item) for item in existing]
    for correction in corrections:
        index = by_id.get(correction.get("id"))
        if index is None:
            index = by_name.get(correction.get("name"))
        if index is None:
            raise OverlayError(
                f"{edition_id}: overlay course variant "
                f"{correction.get('id') or correction.get('name')!r} is not on this edition"
            )
        match = corrected[index]
        if "id" in correction and correction["id"] != match.get("id"):
            raise OverlayError(f"{edition_id}: course variant ids cannot be changed")
        corrected[index] = {**match, **{k: v for k, v in correction.items() if k != "id"}}

    return corrected
