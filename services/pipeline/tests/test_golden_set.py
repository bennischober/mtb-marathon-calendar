"""Golden-set evaluation of the LLM classification stage.

Each case is a tricky real event with the verdict a correct classifier must
reach. The LLM responses are recorded stubs, so the suite runs offline and
pins the *contract*: the pipeline applies verdicts correctly, caches them,
and degrades to candidates when the provider fails.

When the prompt changes, re-record the stub answers by running the batch
against a real provider and pasting the verdicts into ``GOLDEN_ANSWERS``.
"""

import json
import tempfile
from pathlib import Path

import httpx

from mtb_pipeline.collect import collect
from mtb_pipeline.llm import LLMClassifier, LLMConfig, record_prompt
from mtb_pipeline.models import Edition, Location, SourceRef
from mtb_pipeline.sources.radsport_events import RadsportEventsAdapter

GOLDEN_ANSWERS: dict[str, dict] = {
    # running-first event mislabelled MTB_MARATHON by the aggregator
    "Possenlauf": {
        "classification": "exclude",
        "confidence": 0.9,
        "reason": "running race with MTB side course",
    },
    # bikepacking/ultracycling challenge
    "The HOPE 1000 ": {
        "classification": "mtb_ultra",
        "confidence": 0.75,
        "reason": "ultradistance MTB challenge",
    },
    # flagship stage race
    "Swiss Epic": {
        "classification": "mtb_stage_race",
        "confidence": 0.95,
        "reason": "5-day stage race",
    },
    # plain marathon
    "Black Forest ULTRA Bike Marathon": {
        "classification": "mtb_marathon",
        "confidence": 0.9,
        "reason": "single-day marathon",
    },
    # training/course, not a race
    "Fahrtechnikkurs Wochenend": {
        "classification": "exclude",
        "confidence": 0.85,
        "reason": "training course",
    },
    # gravel-only tour
    "Gravel Ride & Race Bern": {
        "classification": "exclude",
        "confidence": 0.9,
        "reason": "gravel-only",
    },
    # XCO cup race
    "MTB Cup XC Series": {
        "classification": "exclude",
        "confidence": 0.9,
        "reason": "cross-country cup series",
    },
}


class GoldenTransport(httpx.BaseTransport):
    """Answers each prompt using the golden verdicts; flags unknown records."""

    def __init__(self, unknown: set[str]) -> None:
        self.unknown = unknown
        self.unknown_hits: list[str] = []
        self.calls = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        body = json.loads(request.content.decode("utf-8"))
        user_prompt = body["messages"][-1]["content"]
        results = []
        for line in user_prompt.splitlines():
            if not line or not line[0].isdigit():
                continue
            index, _, text = line.partition(": ")
            verdict = self._verdict_for(text)
            if verdict is None:
                self.unknown_hits.append(text)
                continue
            results.append({"index": int(index), **verdict})
        body = {"choices": [{"message": {"content": json.dumps({"results": results})}}]}
        return httpx.Response(200, json=body)

    def _verdict_for(self, text: str) -> dict | None:
        title = text.removeprefix("Title: ").split("\n")[0].strip()
        for name, verdict in GOLDEN_ANSWERS.items():
            if name.strip().lower() in title.lower():
                return verdict
        return None


def _edition(name: str, *, description: str | None = None, country: str = "DE") -> Edition:
    from datetime import UTC, date, datetime

    return Edition(
        id=f"golden-{name.strip().lower().replace(' ', '-')}-2027",
        event_id=f"golden-{name.strip().lower().replace(' ', '-')}",
        edition=2027,
        name=name,
        description=description,
        date_start=date(2027, 5, 1),
        format="single_day",
        location=Location(name="X", country=country),
        official_url="https://example.org",
        source_refs=[
            SourceRef(
                id="golden:1",
                url="https://example.org/1",
                retrieved_at=datetime.now(UTC),
                confidence=0.9,
                adapter="golden",
            )
        ],
        classification="mtb_candidate",
        classification_confidence=0.5,
        status="unknown",
    )


class GoldenAdapter(RadsportEventsAdapter):
    def fetch(self, fetcher, options):  # noqa: ANN001, ANN202
        return []

    def parse(self, documents):  # noqa: ANN001, ANN202
        return [
            _edition(
                "Possenlauf",
                description="Neben Laufangeboten gehört auch Mountainbike zum Programm.",
            ),
            _edition(
                "The HOPE 1000 ",
                description="1000 Kilometer nonstop, Bikepacking-Herausforderung.",
                country="CH",
            ),
            _edition(
                "Black Forest ULTRA Bike Marathon",
                description="Die Klassik-Distanz über 118 Kilometer.",
            ),
            _edition(
                "Fahrtechnikkurs Wochenend",
                description="Techniktraining für Einsteiger.",
            ),
            _edition(
                "Gravel Ride & Race Bern",
                description="Gravel-Rennen auf Schotter.",
                country="CH",
            ),
            _edition("MTB Cup XC Series", description="Cross-country Rennserie."),
        ]


def test_golden_set_verdicts_are_applied():
    transport = GoldenTransport(unknown=set())

    with tempfile.TemporaryDirectory() as tmp:
        result = collect(
            GoldenAdapter(),
            fetcher=type("F", (), {"request_count": 0})(),
            classifier=LLMClassifier(
                LLMConfig(base_url="http://stub.local/v1", model="golden", api_key="k"),
                client=httpx.Client(base_url="http://stub.local/v1", transport=transport),
            ),
            classifier_cache=Path(tmp) / "cache.sqlite3",
        )

    by_name = {e.name.strip(): e for e in result.editions}
    # LLM-excluded records are dropped from the kept set entirely
    assert "Possenlauf" not in by_name
    assert "Fahrtechnikkurs Wochenend" not in by_name
    assert "Gravel Ride & Race Bern" not in by_name
    assert "MTB Cup XC Series" not in by_name
    assert by_name["The HOPE 1000"].classification == "mtb_ultra"
    assert by_name["Black Forest ULTRA Bike Marathon"].classification == "mtb_marathon"
    assert result.excluded == 4
    assert transport.unknown_hits == []


def test_verdicts_are_cached_across_runs():
    transport = GoldenTransport(unknown=set())

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "cache.sqlite3"
        classifier = LLMClassifier(
            LLMConfig(base_url="http://stub.local/v1", model="golden", api_key="k"),
            client=httpx.Client(base_url="http://stub.local/v1", transport=transport),
        )
        collect(
            GoldenAdapter(),
            fetcher=type("F", (), {"request_count": 0})(),
            classifier=classifier,
            classifier_cache=cache,
        )
        calls_after_first = transport.calls
        second = collect(
            GoldenAdapter(),
            fetcher=type("F", (), {"request_count": 0})(),
            classifier=classifier,
            classifier_cache=cache,
        )

        assert calls_after_first == 1  # one batch call on the first run
        # All 6 record prompts are cached after the first run; the excluded
        # records simply do not reach the apply step again.
        assert second.llm_cache_hits == 6
        assert second.llm_calls == 0


def test_a_provider_outage_degrades_to_candidates_instead_of_dropping():
    class BrokenTransport(httpx.BaseTransport):
        def handle_request(self, request):
            return httpx.Response(500)

    with tempfile.TemporaryDirectory() as tmp:
        result = collect(
            GoldenAdapter(),
            fetcher=type("F", (), {"request_count": 0})(),
            classifier=LLMClassifier(
                LLMConfig(
                    base_url="http://stub.local/v1", model="golden", api_key="k", max_retries=0
                ),
                client=httpx.Client(base_url="http://stub.local/v1", transport=BrokenTransport()),
            ),
            classifier_cache=Path(tmp) / "cache.sqlite3",
        )

        assert result.degraded is True
        assert result.llm_failures > 0
        assert all(e.classification == "mtb_candidate" for e in result.editions)


def test_prompt_text_is_stable_for_the_same_facts():
    a = record_prompt(name="X", description="d", distances=[60.0])
    b = record_prompt(name="X", description="d", distances=[60.0])

    assert a == b


def test_unchanged_records_skip_the_llm_on_recollect():
    """The known-series fast path: same text + model + prompt = cached verdict,
    zero API calls on a second collect of unchanged data."""
    transport = GoldenTransport(unknown=set())

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "cache.sqlite3"
        classifier = LLMClassifier(
            LLMConfig(base_url="http://stub.local/v1", model="golden", api_key="k"),
            client=httpx.Client(base_url="http://stub.local/v1", transport=transport),
        )
        first = collect(
            GoldenAdapter(),
            fetcher=type("F", (), {"request_count": 0})(),
            classifier=classifier,
            classifier_cache=cache,
        )
        calls_first = transport.calls
        second = collect(
            GoldenAdapter(),
            fetcher=type("F", (), {"request_count": 0})(),
            classifier=classifier,
            classifier_cache=cache,
        )
        # The second run classified everything from the cache: no HTTP calls.
        assert calls_first == 1
        assert transport.calls == 1
        assert second.llm_cache_hits > 0
        assert second.llm_calls == 0
        # And the verdicts are identical.
        first_classes = {e.id: e.classification for e in first.editions}
        second_classes = {e.id: e.classification for e in second.editions}
        assert first_classes == second_classes
