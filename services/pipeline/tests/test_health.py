from datetime import UTC, datetime, timedelta

import pytest

from mtb_pipeline.health import (
    FAILING,
    NEVER_RUN,
    OK,
    STALE,
    RunFacts,
    evaluate,
)
from mtb_pipeline.sources import SourceProfile
from mtb_pipeline.sources.base import PRIORITY_RANK

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)

PROFILE = SourceProfile(
    name="test-source",
    homepage="https://example.org",
    priority="official_federation",
    countries=("DE",),
    cadence_hours=24,
    data_format="json",
    parser_status="verified",
    terms_status="confirmed",
)


def run(*, ok: bool, hours_ago: float = 1.0, kept: int = 5, candidates: int = 1) -> RunFacts:
    return RunFacts(
        ok=ok,
        finished_at=NOW - timedelta(hours=hours_ago),
        kept=kept,
        candidates=candidates,
    )


def health(runs, *, profile=PROFILE, now=NOW):
    return evaluate([profile], {"test-source": runs}, now=now)[0]


def test_a_source_that_never_ran_is_reported_separately():
    item = health([])

    assert item.state == NEVER_RUN
    assert item.runs == 0
    assert item.last_success_at is None
    assert item.failure_rate is None


def test_a_recent_success_is_healthy():
    item = health([run(ok=True, hours_ago=2)])

    assert item.state == OK
    assert item.successes == 1
    assert item.consecutive_failures == 0
    assert item.kept_last == 5
    assert item.candidates_last == 1


def test_a_source_past_its_cadence_is_stale():
    item = health([run(ok=True, hours_ago=48)])

    assert item.state == STALE
    assert item.is_stale
    assert round(item.age.total_seconds() / 3600) == 48


def test_a_single_failure_does_not_mark_a_healthy_source_failing():
    """One flaky network error should not flip a working source to red."""
    item = health([run(ok=False, hours_ago=1), run(ok=True, hours_ago=5)])

    assert item.state == OK
    assert item.consecutive_failures == 1
    assert item.failure_rate == 0.5


def test_repeated_failures_mark_the_source_failing():
    item = health(
        [run(ok=False, hours_ago=1), run(ok=False, hours_ago=3), run(ok=True, hours_ago=9)]
    )

    assert item.state == FAILING
    assert item.consecutive_failures == 2
    assert item.last_ok is False
    # The last success must remain visible even while the source is failing.
    assert item.last_success_at is not None


def test_failure_rate_counts_every_attempt():
    item = health([run(ok=False), run(ok=False), run(ok=False), run(ok=True)])

    assert item.runs == 4
    assert item.failure_rate == 0.75


def test_an_older_success_does_not_reset_the_consecutive_failure_count():
    runs = [run(ok=False, hours_ago=1), run(ok=True, hours_ago=2), run(ok=False, hours_ago=3)]

    # Ordering is by time, so the newest failure is the only one that counts.
    assert health(runs).consecutive_failures == 1


def test_report_orders_failing_sources_first():
    stale = SourceProfile(name="stale-source", homepage="https://a.example", cadence_hours=1)
    healthy = SourceProfile(name="healthy-source", homepage="https://b.example", cadence_hours=24)
    facts = {
        "stale-source": [run(ok=True, hours_ago=72)],
        "healthy-source": [run(ok=True, hours_ago=1)],
    }

    report = evaluate([healthy, stale], facts, now=NOW)

    assert [item.name for item in report] == ["stale-source", "healthy-source"]


def test_sources_that_were_never_run_still_appear_in_the_report():
    report = evaluate([PROFILE], {}, now=NOW)

    assert [item.name for item in report] == ["test-source"]
    assert report[0].state == NEVER_RUN


def test_row_exposes_the_fields_the_status_report_needs():
    row = health([run(ok=True, hours_ago=3)]).row()

    assert row["source"] == "test-source"
    assert row["state"] == OK
    assert row["priority"] == "official_federation"
    assert row["terms"] == "confirmed"
    assert row["cadence_hours"] == 24
    assert row["age_hours"] == 3.0
    assert row["stale"] is False


def test_priority_rank_orders_official_sources_above_aggregators():
    assert PRIORITY_RANK["official_organizer"] < PRIORITY_RANK["official_federation"]
    assert PRIORITY_RANK["official_federation"] < PRIORITY_RANK["event_platform"]
    assert PRIORITY_RANK["event_platform"] < PRIORITY_RANK["specialist_aggregator"]
    assert PRIORITY_RANK["specialist_aggregator"] < PRIORITY_RANK["web_discovery"]


@pytest.mark.parametrize("priority", list(PRIORITY_RANK))
def test_every_declared_priority_has_a_rank(priority):
    assert (
        PRIORITY_RANK[priority]
        == PRIORITY_RANK[
            SourceProfile(name="x", homepage="https://x.example", priority=priority).priority
        ]
    )
