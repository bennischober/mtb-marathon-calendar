"""Durable storage for cross-source merge conflicts.

The merge already resolves every disagreement, so nothing here repairs data.
It answers a narrower question: after a surprising merge, *which* sources
disagreed, on *what*, and which value survived. Before this existed that
information only existed as text on stdout, which a scheduled run discards.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .orm import MergeConflictRecord


def _jsonable(value: Any) -> Any:
    """Coerce a resolved value into something JSON can hold.

    Conflicting values come from the export payload, which is already JSON, but
    the merge also compares values that only ever existed as Python objects.
    Anything unexpected degrades to its text form rather than failing the export.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


def record_merge_conflicts(
    session: Session,
    conflicts: list[dict[str, Any]],
    *,
    edition_ids: list[str],
    recorded_at: datetime,
) -> int:
    """Store the conflicts from one export, replacing any previous set.

    ``edition_ids`` is the set of editions the export covered. Only their
    conflicts are replaced, so a partial export cannot wipe the record for
    editions it did not look at. A field that stopped disagreeing disappears
    from the table instead of lingering as a stale accusation.
    """
    if edition_ids:
        session.execute(
            delete(MergeConflictRecord).where(MergeConflictRecord.edition_id.in_(edition_ids))
        )

    stamp = recorded_at.isoformat()
    for conflict in conflicts:
        session.add(
            MergeConflictRecord(
                edition_id=conflict["edition_id"],
                recorded_at=stamp,
                field=conflict["field"],
                kept=_jsonable(conflict["kept"]),
                kept_source=conflict["kept_source"],
                discarded=_jsonable(conflict["discarded"]) or {},
                reason=conflict["reason"],
            )
        )
    return len(conflicts)


def load_merge_conflicts(
    session: Session,
    *,
    edition_id: str | None = None,
    limit: int | None = None,
) -> list[MergeConflictRecord]:
    """Return stored conflicts, newest edition grouping first."""
    statement = select(MergeConflictRecord).order_by(
        MergeConflictRecord.edition_id, MergeConflictRecord.field
    )
    if edition_id is not None:
        statement = statement.where(MergeConflictRecord.edition_id == edition_id)
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.scalars(statement))
