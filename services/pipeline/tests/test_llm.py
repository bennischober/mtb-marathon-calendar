"""LLM classification stage: batching, caching and degraded mode.

These tests stub the OpenAI-compatible HTTP layer, so they run without
network access and without an API key.
"""

import json

import httpx

from mtb_pipeline.llm import (
    PROMPT_VERSION,
    LLMClassifier,
    LLMConfig,
    prompt_hash,
    record_prompt,
    verdict_from_cached,
)
from mtb_pipeline.llm_cache import cached_entry_count, get_cached, put_cached


class StubTransport(httpx.BaseTransport):
    """OpenAI-compatible responses without network access."""

    def __init__(self, answers: list[dict] | None = None) -> None:
        self.answers = answers or []
        self.calls = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        answer = self.answers[min(self.calls - 1, len(self.answers) - 1)]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})


def _config() -> LLMConfig:
    return LLMConfig(base_url="http://stub.local/v1", model="stub-model", api_key="stub-key")


def _classifier(transport):
    client = httpx.Client(base_url="http://stub.local/v1", transport=transport)
    return LLMClassifier(_config(), client=client)


def _batch_answer(verdicts: list[dict]) -> dict:
    return {"results": verdicts}


def test_prompt_hash_changes_with_model_and_prompt_version():
    a = prompt_hash("text", "model-a")
    b = prompt_hash("text", "model-b")
    c = prompt_hash("text", "model-a", prompt_version=PROMPT_VERSION + 1)

    assert a != b
    assert a != c


def test_record_prompt_carries_title_description_and_distances():
    prompt = record_prompt(name="X Race", description="A trail race.", distances=[60.0, 90.0])

    assert "X Race" in prompt
    assert "A trail race." in prompt
    assert "60 km" in prompt


def test_a_batch_is_parsed_into_aligned_verdicts():
    transport = StubTransport(
        [_batch_answer([
            {"index": 0, "classification": "exclude", "confidence": 0.85, "reason": "running race"},
            {"index": 1, "classification": "mtb_marathon", "confidence": 0.9, "reason": "clear"},
        ])]
    )
    classifier = _classifier(transport)

    verdicts = classifier.classify_batch(["Title: A", "Title: B"])

    assert len(verdicts) == 2
    assert verdicts[0].classification == "exclude"
    assert verdicts[1].classification == "mtb_marathon"
    assert transport.calls == 1


def test_an_unparseable_batch_yields_no_verdicts():
    class GarbageTransport(StubTransport):
        def handle_request(self, request):
            self.calls += 1
            return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    verdicts = _classifier(GarbageTransport()).classify_batch(["Title: A"])

    assert verdicts == [None]


def test_an_out_of_range_index_is_dropped_not_misapplied():
    transport = StubTransport(
        [_batch_answer([{"index": 7, "classification": "mtb_marathon", "confidence": 0.9}])]
    )
    verdicts = _classifier(transport).classify_batch(["Title: A"])

    assert verdicts == [None]


def test_verdict_from_cached_round_trips():
    cached = {"index": 0, "classification": "mtb_ultra", "confidence": 0.8, "reason": "long"}

    result = verdict_from_cached(cached)

    assert result is not None
    assert result.classification == "mtb_ultra"
    assert result.warnings == ["llm:long"]


def test_cache_roundtrip_stores_and_returns_the_answer(tmp_path):
    cache_file = tmp_path / "cache.sqlite3"
    answer = {"index": 0, "classification": "exclude", "confidence": 0.8, "reason": "running"}

    put_cached(cache_file, "prompt-text", "stub-model", answer)
    loaded = get_cached(cache_file, "prompt-text", "stub-model")

    assert loaded == answer
    assert get_cached(cache_file, "prompt-text", "other-model") is None
    assert cached_entry_count(cache_file) == 1


def test_llm_config_requires_an_api_key(monkeypatch):
    monkeypatch.delenv("MTB_LLM_API_KEY", raising=False)

    assert LLMConfig.from_env(base_url="http://x/v1", model="m") is None

    monkeypatch.setenv("MTB_LLM_API_KEY", " secret ")

    config = LLMConfig.from_env(base_url="http://x/v1/", model="m")

    assert config is not None
    assert config.api_key == "secret"
    assert config.base_url == "http://x/v1"
