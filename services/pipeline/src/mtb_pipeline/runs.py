"""Persistence for collection run history and the derived source health report.

Kept separate from :mod:`mtb_pipeline.repository`, which only deals with the
event data itself, so operational bookkeeping can evolve without touching the
record mapping that the calendar depends on.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .health import RunFacts, SourceHealth, evaluate
from .orm import CollectionRunRecord
from .sources import SourceProfile


class RunOutcome:
    """Counts recorded for one collection attempt."""

    def __init__(
        self,
        *,
        ok: bool = True,
        error: str | None = None,
        requests: int = 0,
        documents: int = 0,
        parsed: int = 0,
        kept: int = 0,
        excluded: int = 0,
        candidates: int = 0,
    ) -> None:
        self.ok = ok
        self.error = error
        self.requests = requests
        self.documents = documents
        self.parsed = parsed
        self.kept = kept
        self.excluded = excluded
        self.candidates = candidates


def record_run(
    session: Session,
    adapter: str,
    *,
    started_at: datetime,
    finished_at: datetime | None = None,
    outcome: RunOutcome | None = None,
) -> CollectionRunRecord:
    """Append one collection run. Failures are recorded, not swallowed."""
    outcome = outcome or RunOutcome()
    finished_at = finished_at or datetime.now(UTC)
    record = CollectionRunRecord(
        adapter=adapter,
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        ok=outcome.ok,
        error=outcome.error,
        requests=outcome.requests,
        documents=outcome.documents,
        parsed=outcome.parsed,
        kept=outcome.kept,
        excluded=outcome.excluded,
        candidates=outcome.candidates,
    )
    session.add(record)
    return record


def _load_facts(session: Session) -> dict[str, list[RunFacts]]:
    rows = session.scalars(
        select(CollectionRunRecord).order_by(CollectionRunRecord.finished_at)
    ).all()

    grouped: dict[str, list[RunFacts]] = defaultdict(list)
    for row in rows:
        grouped[row.adapter].append(
            RunFacts(
                ok=row.ok,
                finished_at=datetime.fromisoformat(row.finished_at),
                kept=row.kept,
                candidates=row.candidates,
            )
        )
    return dict(grouped)


def source_health(
    session: Session,
    profiles: list[SourceProfile],
    *,
    now: datetime | None = None,
) -> list[SourceHealth]:
    """Report health for every registered source, including ones never run."""
    return evaluate(profiles, _load_facts(session), now=now)
