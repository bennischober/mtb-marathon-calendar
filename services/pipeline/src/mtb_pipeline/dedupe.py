"""Deduplication and field-by-field conflict resolution across sources.

Two sources routinely describe the same race: one aggregator lists
"Salzkammergut MTB Marathon", the organizer lists "Salzkammergut Trophy". They
must become one record, not two, but merging must never quietly discard
evidence.

The rules are therefore asymmetric on purpose:

* **Identity** is decided conservatively. A name match alone is not enough,
  because "Bike Marathon" matches too many events. A match additionally needs a
  compatible date and the same country, so unrelated races are never folded
  together by a generic name.
* **Field values** are decided by source priority first and freshness second.
  When a lower-priority source disagrees with a higher-priority one, the
  higher-priority value wins and the disagreement is *recorded*, not dropped.
* **Every** source reference from every input survives the merge, so a human can
  always see what was seen.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .normalize import normalize_name
from .sources.base import PRIORITY_RANK

# Two dates further apart than this are treated as different editions rather
# than a typo in one of the sources.
DATE_TOLERANCE = timedelta(days=3)

# Generic words that appear in many unrelated race names. A match on one of
# these alone is never enough to merge two records.
GENERIC_TOKENS = frozenset(
    {
        "bike",
        "bikes",
        "mtb",
        "mountainbike",
        "marathon",
        "race",
        "rennen",
        "tour",
        "cup",
        "grand",
        "annual",
        "annuale",
    }
)


@dataclass(frozen=True)
class MergeConflict:
    """A field where sources disagreed, kept alongside the winning value.

    ``edition_id`` is the surviving edition, so a conflict can be stored against
    the record it actually affects rather than only being printed.
    """

    edition_id: str
    field_name: str
    kept: Any
    kept_source: str
    discarded: dict[str, Any]
    reason: str

    def __getitem__(self, key: str) -> Any:
        # Dict-style access keeps the conflict readable in the export log and
        # lets storage code index it without caring about the concrete type.
        mapping = {
            "edition_id": self.edition_id,
            "field": self.field_name,
            "kept": self.kept,
            "kept_source": self.kept_source,
            "discarded": self.discarded,
            "reason": self.reason,
        }
        try:
            return mapping[key]
        except KeyError:
            raise KeyError(key) from None


@dataclass
class MergeResult:
    editions: list[dict[str, Any]]
    merges: int
    conflicts: list[MergeConflict] = field(default_factory=list)
    #: human-readable notes, surfaced in the export log
    log: list[str] = field(default_factory=list)


def merge_editions(
    editions: list[dict[str, Any]],
    *,
    source_priority: dict[str, int] | None = None,
) -> MergeResult:
    """Fold duplicate editions together, resolving conflicts field by field.

    ``editions`` are edition dicts as produced by the ORM or by an adapter, and
    each must carry ``source_refs`` so provenance can be ranked.
    """
    priority = source_priority or PRIORITY_RANK

    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []

    for edition in editions:
        key = _bucket_key(edition)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(edition)

    merged: list[dict[str, Any]] = []
    conflicts: list[MergeConflict] = []
    log: list[str] = []
    merges = 0

    for key in order:
        group = buckets[key]
        if len(group) == 1:
            merged.append(_copy_edition(group[0]))
            continue

        survivors, group_conflicts, group_log = _merge_group(group, priority)
        merges += len(group) - len(survivors)
        conflicts.extend(group_conflicts)
        log.extend(group_log)
        merged.extend(survivors)

    # _merge_group orders survivors by source rank, which is the right order
    # inside a merge but scrambles the chronological export order across
    # buckets. Restore the export contract's order: chronological, then best
    # evidence first, then id — matching build_export's sort exactly.
    def _order(item: dict[str, Any]) -> tuple[str, float, str]:
        confidences = [float(ref.get("confidence") or 0.0) for ref in item.get("source_refs") or []]
        return (
            str(item.get("date_start") or ""),
            -max(confidences) if confidences else 0.0,
            str(item.get("id") or ""),
        )

    merged.sort(key=_order)
    return MergeResult(editions=merged, merges=merges, conflicts=conflicts, log=log)


def _bucket_key(edition: dict[str, Any]) -> tuple[str, str]:
    """Coarse grouping key.

    Names are deliberately excluded: two sources rarely spell a race the same
    way, so name matching happens inside the group instead of deciding it.
    """
    location = edition.get("location") or {}
    return (
        str(location.get("country") or ""),
        str(edition.get("edition") or ""),
    )


def _distinctive_tokens(name: str) -> set[str]:
    return {token for token in name.split("-") if token and token not in GENERIC_TOKENS}


def _names_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """True when two names plausibly describe the same race.

    Matching is token-based and asymmetric-tolerant: "Salzkammergut Trophy" and
    "Salzkammergut MTB Marathon" share the one distinctive token and add only
    generic words, so they match. Both names must contain a distinctive token,
    which is what stops "Bike Marathon" from matching anything.
    """
    left_name = normalize_name(str(left.get("name") or ""))
    right_name = normalize_name(str(right.get("name") or ""))

    left_tokens = _distinctive_tokens(left_name)
    right_tokens = _distinctive_tokens(right_name)
    if not left_tokens or not right_tokens:
        return False

    if left_name == right_name:
        return True

    shared = left_tokens & right_tokens
    return bool(shared) and (left_tokens <= right_tokens or right_tokens <= left_tokens)


def _dates_agree(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_date = _as_date(left.get("date_start"))
    right_date = _as_date(right.get("date_start"))
    if left_date is None or right_date is None:
        # Without dates we cannot rule out two different editions, so say no.
        return False
    return abs(left_date - right_date) <= DATE_TOLERANCE


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _copy_edition(edition: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy an edition so merging never mutates the caller's data.

    The caller passes records that may alias dicts still held elsewhere (e.g.
    the payload list), so nested ``location``, ``source_refs`` and
    ``course_variants`` must be copied too.
    """
    return copy.deepcopy(edition)


def _merge_group(
    group: list[dict[str, Any]],
    priority: dict[str, int],
) -> tuple[list[dict[str, Any]], list[MergeConflict], list[str]]:
    # Best source first: stronger priority, then the fresher retrieval.
    ordered = sorted(
        group,
        key=lambda item: (_group_rank(item, priority), _neg_recency(item)),
    )
    winner = _copy_edition(ordered[0])
    conflicts: list[MergeConflict] = []
    log: list[str] = []
    survivors: list[dict[str, Any]] = [winner]
    unrelated = 0

    kept_sources = _source_names(winner)
    for other in ordered[1:]:
        if not _names_match(other, winner):
            # A genuinely different event that happens to share a bucket. It is
            # carried through untouched: dropping it would lose an event. Logging
            # every one of these would bury the actual merges, so the count is
            # reported once per group instead.
            survivors.append(_copy_edition(other))
            unrelated += 1
            continue

        if not _dates_agree(other, winner):
            # Same series and year but different dates: a reschedule we cannot
            # resolve, so keep both and let a human look.
            survivors.append(other)
            log.append(
                f"{winner.get('id')}: kept {other.get('id')} separate, dates differ "
                f"({other.get('date_start')} vs {winner.get('date_start')})"
            )
            conflicts.append(
                MergeConflict(
                    edition_id=winner.get("id", ""),
                    field_name="date_start",
                    kept=winner.get("date_start"),
                    kept_source=_primary_source(winner),
                    discarded={_source_label(other): other.get("date_start")},
                    reason="dates differ beyond tolerance; kept both records",
                )
            )
            continue

        _apply(winner, other, kept_sources, priority, conflicts)
        log.append(
            f"{winner.get('id')}: merged {other.get('id')} "
            f"({', '.join(sorted(_source_names(other) - kept_sources)) or 'same source'})"
        )
        kept_sources |= _source_names(other)

    if unrelated:
        log.append(
            f"{len(survivors) - 1} record(s) in this country/year bucket stayed "
            f"separate because their names do not match"
        )

    return survivors, conflicts, log


def _group_rank(edition: dict[str, Any], priority: dict[str, int]) -> int:
    """Best (lowest) priority rank across the edition's sources."""
    ranks = [
        priority.get(str(ref.get("adapter")), _DEFAULT_RANK)
        for ref in edition.get("source_refs") or []
    ]
    return min(ranks) if ranks else _DEFAULT_RANK


_DEFAULT_RANK = PRIORITY_RANK["web_discovery"]


def _neg_recency(edition: dict[str, Any]) -> float:
    """Sort key placing the most recently retrieved edition first.

    Negating the epoch seconds is required: ISO timestamps sort correctly as
    strings, but negating the *string* would not invert their order.
    """
    times = [str(ref.get("retrieved_at") or "") for ref in edition.get("source_refs") or []]
    stamps = [_parse_timestamp(value) for value in times]
    known = [stamp for stamp in stamps if stamp is not None]
    return -max(known) if known else 0.0


def _parse_timestamp(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


def _source_names(edition: dict[str, Any]) -> set[str]:
    return {str(ref.get("adapter") or "?") for ref in edition.get("source_refs") or []}


def _primary_source(edition: dict[str, Any]) -> str:
    adapters = sorted(a for a in _source_names(edition) if a != "?")
    return adapters[0] if adapters else "unknown"


def _source_label(edition: dict[str, Any]) -> str:
    return _primary_source(edition)


def _apply(
    winner: dict[str, Any],
    other: dict[str, Any],
    kept_sources: set[str],
    priority: dict[str, int],
    conflicts: list[MergeConflict],
) -> None:
    """Fill gaps in the winner and record every disagreement.

    A field is only overwritten when the other record's sources outrank the
    winner's, which keeps a high-priority value from being replaced by noise.
    """
    reconciled = (
        "date_end",
        "duration_days",
        "registration_url",
        "official_url",
        "format",
        "status",
    )
    for key in reconciled:
        current = winner.get(key)
        incoming = other.get(key)
        if incoming is None or incoming == current:
            continue
        if current is None:
            winner[key] = incoming
            continue

        other_outranks = _group_rank(other, priority) < _group_rank(winner, priority)
        conflicts.append(
            MergeConflict(
                edition_id=winner.get("id", ""),
                field_name=key,
                kept=incoming if other_outranks else current,
                kept_source=_source_label(other) if other_outranks else _primary_source(winner),
                discarded=(
                    {_primary_source(winner): current}
                    if other_outranks
                    else {_source_label(other): incoming}
                ),
                reason=(
                    "higher-priority source disagreed"
                    if other_outranks
                    else "lower-priority source disagreed"
                ),
            )
        )
        if other_outranks:
            winner[key] = incoming

    _merge_location(winner, other, conflicts)
    _merge_course_variants(winner, other)
    _merge_provenance(winner, other)


def _merge_location(
    winner: dict[str, Any], other: dict[str, Any], conflicts: list[MergeConflict]
) -> None:
    target = winner.setdefault("location", {})
    incoming = other.get("location") or {}
    for key, value in incoming.items():
        if value is None:
            continue
        current = target.get(key)
        if current is None:
            target[key] = value
        elif current != value:
            conflicts.append(
                MergeConflict(
                    edition_id=winner.get("id", ""),
                    field_name=f"location.{key}",
                    kept=current,
                    kept_source=_primary_source(winner),
                    discarded={_source_label(other): value},
                    reason="locations differ; kept the higher-priority record",
                )
            )


def _merge_course_variants(winner: dict[str, Any], other: dict[str, Any]) -> None:
    """Union variant sets, keeping the winner's value for a shared id.

    Distances are evidence: an extra course from another source is worth keeping,
    but a changed distance for a course both sources list is a disagreement the
    conflict log already covers.
    """
    variants = list(winner.get("course_variants") or [])
    by_id = {variant.get("id"): index for index, variant in enumerate(variants)}

    for incoming in other.get("course_variants") or []:
        if incoming.get("id") in by_id:
            continue
        by_id[incoming.get("id")] = len(variants)
        variants.append(dict(incoming))

    winner["course_variants"] = variants


def _merge_provenance(winner: dict[str, Any], other: dict[str, Any]) -> None:
    """Keep every source reference from every merged record."""
    refs = list(winner.get("source_refs") or [])
    seen = {ref.get("id") for ref in refs}

    for ref in other.get("source_refs") or []:
        if ref.get("id") not in seen:
            seen.add(ref.get("id"))
            refs.append(dict(ref))

    winner["source_refs"] = refs
