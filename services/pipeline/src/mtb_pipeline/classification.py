"""Factual pre-classification and LLM hand-off.

Classification is a two-stage process:

1. **Deterministic scope filter** (this module): honor *source-stated*
   labels. When a source says ``category=ROAD`` or a federation PDF codes a
   discipline as a time trial, that is a fact, not an opinion, so acting on
   it stays code. Factual observations (no distance data, cancelled flags)
   become warnings. Nothing here judges titles, descriptions or German
   morphology — that is the LLM's job.

2. **LLM classification** (``mtb_pipeline.llm``): every record that survives
   the scope filter is a ``mtb_candidate`` until the LLM — reading title,
   description and distances — assigns the final class. Without an API key
   the run degrades gracefully: records stay candidates, nothing is silently
   dropped, and the site shows them as unreviewed.

Why this split: keyword/regex classification accumulated a pattern list for
every false-positive class ever seen (running-first events, training rides,
gravel tours...), and every new language or source needed more patterns.
An LLM reads the text instead. Dates, distances and IDs stay deterministic
because hallucinated facts are worse than missing races.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Classification

# Only a factual observation: Salzkammergut-style one-day ultras exceed 200 km
# and are still marathons, while some labelled marathons top out below 50 km.
# The LLM weighs this; the warning only records the fact.
LONG_DISTANCE_KM = 50.0


@dataclass(frozen=True)
class EventSignals:
    """Facts an adapter asserts about one event.

    ``discipline`` and ``category`` are the source's own labels, used only for
    the scope filter below — never for semantic guessing.
    """

    discipline: str | None = None
    category: str | None = None
    max_distance_km: float | None = None
    total_elevation_m: float | None = None
    duration_days: int = 1
    is_cancelled: bool = False
    title: str | None = None
    description: str | None = None
    extra_warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ClassificationResult:
    classification: Classification
    confidence: float
    warnings: list[str] = field(default_factory=list)


# Source categories that are factually out of scope: the source itself says
# the event is road, gravel or cyclocross. Acting on the source's own label
# is deterministic, not a judgement call.
_OUT_OF_SCOPE_CATEGORIES = {
    "road",
    "gravel",
    "rtf",
    "cyclocross",
    "superrad",
    "brevet",
    "volksradfahren",
    "jedermann",
}

# Source disciplines that are factually out of scope for the same reason.
_OUT_OF_SCOPE_DISCIPLINES = {
    "cyclocross",
    "cx",
    "xco",
    "xcc",
    "crosscountry",
    "cross_country",
    "downhill",
    "dh",
    "enduro",
    "fourcross",
    "4x",
    "hillclimb",
    "road",
    "rennrad",
    "kriterium",
    "bahn",
    "gravel",
    "gravel_race",
    "gravel_tour",
    "gravel_ride",
    "gravel_brevet",
    "ctf",
    "brevet",
    "einzelzeitfahren",
    "mannschaftszeitfahren",
    "duathlon",
    "duathlon_cross",
    "triathlon",
    "e_bike",
    "pumptrack",
}

_EXCLUDE_CONFIDENCE = 0.95


def classify(signals: EventSignals) -> ClassificationResult:
    """Apply the deterministic scope filter to source-stated labels.

    Everything that passes becomes ``mtb_candidate``: the LLM classification
    stage decides the final class, or — when no LLM is configured — the
    record stays a candidate for human review.
    """
    warnings: list[str] = list(signals.extra_warnings)

    category = _normalize(signals.category)
    if category in _OUT_OF_SCOPE_CATEGORIES:
        return ClassificationResult(
            "exclude",
            _EXCLUDE_CONFIDENCE,
            [*warnings, f"category_{category}_is_not_mtb"],
        )

    discipline = _normalize(signals.discipline)
    if discipline in _OUT_OF_SCOPE_DISCIPLINES:
        return ClassificationResult(
            "exclude",
            _EXCLUDE_CONFIDENCE,
            [*warnings, f"discipline_{discipline}_excluded"],
        )

    if signals.is_cancelled:
        warnings.append("event_is_cancelled")
    warnings.extend(_distance_warnings(signals))

    # No semantic opinion here: the LLM stage decides. Without it, the record
    # stays a visible, reviewable candidate instead of being dropped.
    return ClassificationResult("mtb_candidate", 0.5, warnings)


def _distance_warnings(signals: EventSignals) -> list[str]:
    if signals.max_distance_km is None:
        return ["no_distance_data"]
    if signals.max_distance_km < LONG_DISTANCE_KM:
        return [f"longest_course_variant_below_{LONG_DISTANCE_KM:g}km"]
    return []


def _normalize(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().lower().replace(" ", "_").replace("-", "_")
