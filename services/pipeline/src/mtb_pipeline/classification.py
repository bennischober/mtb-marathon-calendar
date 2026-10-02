"""Source-agnostic rules that decide whether a record belongs in the calendar.

The calendar covers open MTB marathon, ultra, point-to-point and stage/epic
races. Everything else is excluded or demoted to a candidate for human review.
Rules are deliberately kept separate from adapters so each source only has to
map its own labels onto :class:`EventSignals`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Classification

# Only a heuristic: Salzkammergut-style one-day ultras exceed 200 km and are
# still marathons, while some labelled marathons top out below 50 km.
LONG_DISTANCE_KM = 50.0



@dataclass(frozen=True)
class EventSignals:
    """Normalized facts an adapter can assert about one event."""

    discipline: str | None = None
    category: str | None = None
    max_distance_km: float | None = None
    total_elevation_m: float | None = None
    duration_days: int = 1
    is_cancelled: bool = False
    is_stage_like: bool = False
    title: str | None = None
    description: str | None = None
    extra_warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ClassificationResult:
    classification: Classification
    confidence: float
    warnings: list[str] = field(default_factory=list)


# Disciplines that are unambiguously in scope.
_MARATHON_DISCIPLINES = {"mtb_marathon", "mtb-marathon", "xcm", "marathon_mtb"}
_STAGE_DISCIPLINES = {
    "mtb_stage_race",
    "stage_race",
    "etappenrennen",
    "etappentour",
    "mehrttagestour",
    "stage_tour",
    "mtb_epic",
    "epic",
}
_POINT_TO_POINT_DISCIPLINES = {"point_to_point", "p2p"}

# Disciplines that need corroborating evidence before inclusion.
_ULTRA_DISCIPLINES = {"ultra", "ultradistance", "ultra_distance"}
_NEEDS_EVIDENCE_DISCIPLINES = {"sonstige_mtb", "mtb_other", "other_mtb", "radmarathon"}

# Disciplines that are out of scope regardless of distance.
_EXCLUDED_DISCIPLINES = {
    # cross-country and gravity disciplines
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
    # road / time trial / gravel
    "road",
    "rennrad",
    "radmarathon_road",
    "kriterium",
    "bahn",
    "gravel",
    "gravel_race",
    "gravel_tour",
    "gravel_ride",
    "gravel_brevet",
    "ctf",
    "brevet",
    "rund um",
    "jedermann",
    "vintage_tour",
    "hobbyrennen",
    "einzelzeitfahren",
    "mannschaftszeitfahren",
    "rennen_2_3_6h",
    "rennen_12h",
    "rennen_24h",
    "volksradfahren",
    "permanent",
    # other sports and non-race formats
    "duathlon",
    "duathlon_cross",
    "triathlon",
    "bikepacking",
    "gravel_camp",
    "superrennrad",
    "e-bike",
    "pumptrack",
}

# Non-MTB categories are rejected before the discipline is even considered.
_OUT_OF_SCOPE_CATEGORIES = {"road", "gravel", "rtf", "cyclocross", "superrad"}

# Titles that name a course, a club ride or a training instead of a race.
# Aggregators file these under the MTB category with a discipline label such as
# "Sonstige MTB", so the label alone cannot keep them out. An explicit marathon
# or stage label still wins, because that is stronger evidence than the title.
_TRAINING_TITLE_PATTERNS = (
    "fahrtechnikkurs",
    "technikkurs",
    "techniktraining",
    "workshop",
    "seminar",
    "schulung",
    "unterricht",
    "lehrfahrt",
    "ausbildung",
    "werkstatt",
    "training",
    "testfahrt",
    "probefahrt",
    "gefahrentraining",
    "ehrenfahrt",
    "wanderfahrt",
    "ausfahrt",
)

_EXCLUDE_CONFIDENCE = 0.95
_TRAINING_TITLE_CONFIDENCE = 0.85

# Titles that put a running event first and MTB second ("Xxx-Lauf" whose site
# lists running courses with an MTB side course). The aggregator files these
# under MTB_MARATHON because an MTB course exists, but the race belongs to a
# running series, so it is out of scope for a MTB marathon calendar.
_RUNNING_FIRST_TITLE_PATTERNS = (
    "lauf",
    "läufe",
    "laeufe",
    "run",
    "marathonlauf",
    "volkslauf",
    "trailrun",
)

# A MTB mention in the description is fine — running-first events offer an MTB
# side course. What betrays a running-first event is running vocabulary in the
# title plus running discipline words in the description.
_RUNNING_DESCRIPTION_HINTS = (
    "laufangebot",
    "läufer",
    "laeufer",
    "volkslauf",
    "laufcup",
    "leichtathletik",
    "running",
    "trailrun",
)

_RUNNING_FIRST_CONFIDENCE = 0.8


def classify(signals: EventSignals) -> ClassificationResult:
    """Return the calendar classification for one event."""
    warnings: list[str] = list(signals.extra_warnings)
    discipline = _normalize(signals.discipline)
    category = _normalize(signals.category)

    if category in _OUT_OF_SCOPE_CATEGORIES:
        return ClassificationResult(
            "exclude",
            _EXCLUDE_CONFIDENCE,
            [*warnings, f"category_{category}_is_not_mtb"],
        )

    if discipline in _EXCLUDED_DISCIPLINES:
        return ClassificationResult(
            "exclude",
            _EXCLUDE_CONFIDENCE,
            [*warnings, f"discipline_{discipline}_excluded"],
        )

    # A marathon discipline label normally wins, but a running-first title is
    # stronger evidence: "Possenlauf" is a running race with an MTB side
    # course, however the aggregator labels it. Check the description so the
    # many "Bike-Marathon" names containing " Marathon" are not misread — the
    # pattern must match a running-specific compound, not the bare word.
    running_hint = _looks_like_running_first(signals.title, signals.description)
    if running_hint is not None:
        return ClassificationResult(
            "exclude",
            _RUNNING_FIRST_CONFIDENCE,
            [*warnings, f"title_suggests_running_race_not_mtb:{running_hint}"],
        )

    if signals.is_cancelled:
        warnings.append("event_is_cancelled")

    long_distance = (
        signals.max_distance_km is not None and signals.max_distance_km >= LONG_DISTANCE_KM
    )
    multi_day = signals.duration_days > 1

    if discipline in _STAGE_DISCIPLINES or (signals.is_stage_like and multi_day):
        if not multi_day:
            warnings.append("stage_label_without_multiple_days")
        warnings.extend(_distance_warnings(signals))
        return ClassificationResult("mtb_stage_race", 0.9 if multi_day else 0.7, warnings)

    if discipline in _POINT_TO_POINT_DISCIPLINES:
        warnings.extend(_distance_warnings(signals))
        return ClassificationResult("mtb_point_to_point", 0.8, warnings)

    if discipline in _MARATHON_DISCIPLINES:
        warnings.extend(_distance_warnings(signals))
        return ClassificationResult("mtb_marathon", 0.9, warnings)

    training_hint = _looks_like_training(signals.title)
    if training_hint is not None:
        return ClassificationResult(
            "exclude",
            _TRAINING_TITLE_CONFIDENCE,
            [*warnings, f"title_suggests_training_not_a_race:{training_hint}"],
        )

    if discipline in _ULTRA_DISCIPLINES:
        if long_distance or multi_day:
            classification = "mtb_stage_race" if multi_day else "mtb_marathon"
            return ClassificationResult(classification, 0.75, warnings)
        warnings.append("ultra_without_long_distance_or_multi_day_evidence")
        return ClassificationResult("mtb_candidate", 0.4, warnings)

    if discipline in _NEEDS_EVIDENCE_DISCIPLINES:
        # The label alone is ambiguous, so a promotion must stay auditable.
        warnings.append(f"discipline_{discipline}_is_ambiguous")
        if multi_day:
            return ClassificationResult("mtb_stage_race", 0.65, warnings)
        if long_distance:
            return ClassificationResult("mtb_marathon", 0.7, warnings)
        warnings.append("ambiguous_discipline_needs_review")
        return ClassificationResult("mtb_candidate", 0.45, warnings)

    if signals.max_distance_km is None:
        warnings.append("no_distance_data")
        return ClassificationResult("mtb_candidate", 0.3, warnings)

    if long_distance or multi_day:
        warnings.append("unlabelled_long_distance_event")
        return ClassificationResult("mtb_candidate", 0.5, warnings)

    return ClassificationResult("exclude", 0.6, [*warnings, "no_long_distance_evidence"])


def _looks_like_training(title: str | None) -> str | None:
    """Return the matched training term, or ``None`` for anything race-like."""
    if not title:
        return None
    haystack = title.lower()
    for pattern in _TRAINING_TITLE_PATTERNS:
        if pattern in haystack:
            return pattern
    return None


def _looks_like_running_first(title: str | None, description: str | None) -> str | None:
    """Detect running-first events mislabelled as MTB by the source.

    A title ending in a running compound ("Possenlauf", "Volkslauf") combined
    with running vocabulary in the description is decisive. Either signal
    alone is not: "Bike-Marathon" contains no running compound, and a
    description that merely mentions an MTB side course is expected.
    """
    if not title:
        return None
    haystack = title.lower()
    matched = next(
        (pattern for pattern in _RUNNING_FIRST_TITLE_PATTERNS if pattern in haystack),
        None,
    )
    if matched is None:
        return None
    # Bike/MTB compound titles ("MTB-Marathon", "Bike-Marathon") are MTB races
    # even though they contain "marathon"; the running compounds above never
    # appear inside those, but guard explicitly anyway.
    if "mtb" in haystack or "bike" in haystack:
        return None
    if description:
        lowered = description.lower()
        for hint in _RUNNING_DESCRIPTION_HINTS:
            if hint in lowered:
                return f"{matched}+{hint}"
    return matched if "lauf" in haystack else None


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
