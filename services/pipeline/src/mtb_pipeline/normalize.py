"""Deterministic identity and text normalization for stable cross-year matching."""

from __future__ import annotations

import re
import unicodedata

_UMLAUT_MAP = str.maketrans(
    {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
        "Ä": "Ae",
        "Ö": "Oe",
        "Ü": "Ue",
    }
)

_YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_ORDINAL_PATTERN = re.compile(r"(?<!\d)\d{1,2}\.\s*(?:edition|auflage)(?!\d)", re.IGNORECASE)
_NON_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_TRAILING_YEAR_SUFFIX = re.compile(r"[\s\-–—_/]*\b(?:19|20)\d{2}\s*(?:edition)?\s*$", re.IGNORECASE)


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def clean_text(text: str) -> str:
    """Collapse whitespace and drop decorative separators used in titles."""
    cleaned = text.replace("\u00a0", " ")
    cleaned = re.sub(r"\s*[\|–—·•]+\s*", " - ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_name(text: str) -> str:
    """Normalize a race name for comparison and for deriving a stable event id."""
    normalized = clean_text(text).translate(_UMLAUT_MAP)
    normalized = strip_accents(normalized)
    normalized = _ORDINAL_PATTERN.sub(" ", normalized)
    normalized = _YEAR_PATTERN.sub(" ", normalized)
    normalized = normalized.lower()
    normalized = _TRAILING_YEAR_SUFFIX.sub("", normalized)
    normalized = _NON_SLUG_PATTERN.sub("-", normalized)
    return normalized.strip("-")


def slugify(text: str) -> str:
    slug = _NON_SLUG_PATTERN.sub("-", strip_accents(text).translate(_UMLAUT_MAP).lower())
    return slug.strip("-")


def event_id(source: str, name: str, country: str | None = None) -> str:
    """Build a stable event id that survives year-to-year title changes.

    The year and ordinal edition markers are removed before slugifying so that
    "Swiss Epic 2027" and "Swiss Epic 2028" resolve to the same event.
    """
    slug = normalize_name(name)
    if not slug:
        slug = "unknown"
    country_slug = slugify(country) if country else ""
    parts = [slugify(source), slug]
    if country_slug:
        parts.append(country_slug)
    return "-".join(part for part in parts if part)


def edition_id(event_id_value: str, edition: int) -> str:
    return f"{event_id_value}-{edition}"


def course_variant_id(edition_id_value: str, name: str, distance_km: float | None) -> str:
    """Course variant ids must be stable across re-collection runs."""
    slug = slugify(name) or (f"{distance_km:g}km" if distance_km else "variant")
    return f"{edition_id_value}-{slug}"


def distance_label(distance_km: float) -> str:
    if distance_km >= 100:
        return f"{distance_km:.0f} km"
    if distance_km >= 10:
        return f"{distance_km:.1f}".rstrip("0").rstrip(".") + " km"
    return f"{distance_km:.1f} km"


