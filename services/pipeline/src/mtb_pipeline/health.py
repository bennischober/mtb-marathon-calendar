"""Source health derived from recorded collection runs.

An automated calendar is only useful if it is honest about what it does not
know. This module answers three questions from the ``collection_runs`` history:
did the source ever succeed, is it stale relative to its declared cadence, and
how often has it been failing.

Nothing here mutates state; the status report is a read-only view.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .sources import SourceProfile

HealthState = str

OK = "ok"
STALE = "stale"
FAILING = "failing"
NEVER_RUN = "never_run"


@dataclass(frozen=True)
class SourceHealth:
    """Observed health of one source, paired with its declared expectations."""

    profile: SourceProfile
    runs: int
    successes: int
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    last_ok: bool | None
    consecutive_failures: int
    kept_last: int | None
    candidates_last: int | None
    #: evaluation clock, injected so staleness is deterministic in tests
    now: datetime

    @property
    def name(self) -> str:
        return self.profile.name

    @property
    def failure_rate(self) -> float | None:
        if not self.runs:
            return None
        return round((self.runs - self.successes) / self.runs, 3)

    @property
    def age(self) -> timedelta | None:
        if self.last_success_at is None:
            return None
        return self.now - self.last_success_at

    @property
    def state(self) -> HealthState:
        """Overall verdict, worst condition first.

        A source is reported as failing only after repeated failures, so one
        flaky network error does not flip a healthy source to red.
        """
        if self.last_attempt_at is None:
            return NEVER_RUN
        if self.consecutive_failures >= 2:
            return FAILING
        if self.last_success_at is not None and self.is_stale:
            return STALE
        return OK

    @property
    def is_stale(self) -> bool:
        if self.last_success_at is None:
            return True
        return self.now - self.last_success_at > self.expected_interval

    @property
    def expected_interval(self) -> timedelta:
        return timedelta(hours=self.profile.cadence_hours)

    def row(self) -> dict[str, object]:
        """Flat summary, used by the CLI report and its tests."""
        return {
            "source": self.name,
            "state": self.state,
            "priority": self.profile.priority,
            "format": self.profile.data_format,
            "parser": self.profile.parser_status,
            "robots": self.profile.robots_status,
            "terms": self.profile.terms_status,
            "endpoint": self.profile.endpoint,
            "countries": list(self.profile.countries),
            "cadence_hours": self.profile.cadence_hours,
            "runs": self.runs,
            "failure_rate": self.failure_rate,
            "consecutive_failures": self.consecutive_failures,
            "last_success_at": _iso(self.last_success_at),
            "age_hours": round(self.age.total_seconds() / 3600, 1) if self.age else None,
            "stale": self.is_stale,
            "kept_last": self.kept_last,
            "candidates_last": self.candidates_last,
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def evaluate(
    profiles: Sequence[SourceProfile],
    runs_by_source: dict[str, list[RunFacts]],
    *,
    now: datetime | None = None,
) -> list[SourceHealth]:
    """Build one :class:`SourceHealth` per profile, worst first."""
    now = now or datetime.now(UTC)
    health = [_health(profile, runs_by_source.get(profile.name, []), now) for profile in profiles]
    return sorted(health, key=lambda item: (_STATE_ORDER[item.state], item.name))


_STATE_ORDER = {FAILING: 0, NEVER_RUN: 1, STALE: 2, OK: 3}


@dataclass(frozen=True)
class RunFacts:
    """The subset of a collection run that health reporting needs."""

    ok: bool
    finished_at: datetime
    kept: int
    candidates: int


def _health(profile: SourceProfile, runs: Sequence[RunFacts], now: datetime) -> SourceHealth:
    # Newest first, so consecutive-failure counting stops at the first success.
    ordered = sorted(runs, key=lambda run: run.finished_at, reverse=True)

    successes = [run for run in ordered if run.ok]
    last_success = successes[0].finished_at if successes else None

    consecutive_failures = 0
    for run in ordered:
        if run.ok:
            break
        consecutive_failures += 1

    last_kept = next((run.kept for run in ordered), None)
    last_candidates = next((run.candidates for run in ordered), None)

    return SourceHealth(
        profile=profile,
        runs=len(ordered),
        successes=len(successes),
        last_success_at=last_success,
        last_attempt_at=ordered[0].finished_at if ordered else None,
        last_ok=ordered[0].ok if ordered else None,
        consecutive_failures=consecutive_failures,
        kept_last=last_kept,
        candidates_last=last_candidates,
        now=now,
    )
