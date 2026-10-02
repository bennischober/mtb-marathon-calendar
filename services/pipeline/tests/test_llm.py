"""LLM second-opinion classifier and its durable cache.

The LLM is optional: without an API key the pipeline behaves exactly as
before. These tests drive the classifier with a stubbed HTTP transport so no
network access and no key are needed.
"""

import json
import tempfile
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from mtb_pipeline.classification import ClassificationResult
from mtb_pipeline.llm import (
    CachedClassifier,
    LLMClassifier,
    LLMConfig,
    LLMVerdict,
    cache_key,
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
        body = {
            "choices": [
                {"message": {"content": json.dumps(answer)}},
            ]
        }
        return httpx.Response(200, json=body)


def _config() -> LLMConfig:
    return LLMConfig(base_url="http://stub.local/v1", model="stub-model", api_key="stub-key")


def test_cache_key_changes_with_model_and_prompt():
    a = cache_key("hash", "model-a")
    b = cache_key("hash", "model-b")
    c = cache_key("hash", "model-a", prompt_version=99)

    assert a != b
    assert a != c


def test_a_verdict_is_parsed_from_the_openai_response():
    transport = StubTransport(
        [
            {
                "classification": "exclude",
                "confidence": 0.85,
                "reason": "running race with an MTB side course",
            }
        ]
    )
    client = httpx.Client(base_url="http://stub.local/v1", transport=transport)
    classifier = LLMClassifier(_config(), client=client)

    result = classifier.classify_event(
        title="Possenlauf", description="Laufangebote", discipline="MTB_MARATHON",
        category="MTB", distances=[14.0],
    )

    assert result is not None
    assert result.classification == "exclude"
    assert result.confidence == 0.85
    assert result.warnings == ["llm:running race with an MTB side course"]
    assert transport.calls == 1


def test_an_unparseable_answer_yields_no_verdict():
    class GarbageTransport(StubTransport):
        def handle_request(self, request):
            self.calls += 1
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "not json"}}]},
            )

    client = httpx.Client(base_url="http://stub.local/v1", transport=GarbageTransport())
    classifier = LLMClassifier(_config(), client=client)

    result = classifier.classify_event(
        title="X", description=None, discipline=None, category=None, distances=None
    )

    assert result is None


def test_an_invalid_verdict_yields_no_verdict():
    class BadVerdictTransport(StubTransport):
        def handle_request(self, request):
            self.calls += 1
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"classification": "not_a_classification", "confidence": 2.0}
                                )
                            }
                        }
                    ]
                },
            )

    client = httpx.Client(base_url="http://stub.local/v1", transport=BadVerdictTransport())
    classifier = LLMClassifier(_config(), client=client)

    result = classifier.classify_event(
        title="X", description=None, discipline=None, category=None, distances=None
    )

    assert result is None


def test_cached_classifier_answers_from_cache_on_second_call():
    transport = StubTransport(
        [{"classification": "mtb_marathon", "confidence": 0.9, "reason": "clear marathon"}]
    )
    with tempfile.TemporaryDirectory() as tmp:
        cache_file = Path(tmp) / "cache.sqlite3"
        inner = LLMClassifier(
            _config(), client=httpx.Client(base_url="http://stub.local/v1", transport=transport)
        )
        cached = CachedClassifier(inner, cache_path=cache_file, model="stub-model")

        kwargs = {
            "title": "Possenlauf",
            "description": None,
            "discipline": "MTB_MARATHON",
            "category": "MTB",
            "distances": [14.0],
        }
        first = cached.classify_event(**kwargs)
        second = cached.classify_event(**kwargs)

        assert first is not None and second is not None
        assert first.classification == second.classification
        assert transport.calls == 1  # second call served from cache
        assert cached_entry_count(cache_file) == 1  # the answer was stored


def test_cache_roundtrip_stores_and_returns_the_answer(tmp_path):
    cache_file = tmp_path / "cache.sqlite3"
    answer = {"classification": "exclude", "confidence": 0.8, "reason": "running"}

    put_cached(cache_file, "content-hash", "stub-model", answer)
    loaded = get_cached(cache_file, "content-hash", "stub-model")

    assert loaded == answer
    assert get_cached(cache_file, "content-hash", "other-model") is None


def test_llm_config_requires_an_api_key(monkeypatch):
    monkeypatch.delenv("MTB_LLM_API_KEY", raising=False)

    assert LLMConfig.from_env(base_url="http://x/v1", model="m") is None

    monkeypatch.setenv("MTB_LLM_API_KEY", " secret ")

    config = LLMConfig.from_env(base_url="http://x/v1/", model="m")

    assert config is not None
    assert config.api_key == "secret"
    assert config.base_url == "http://x/v1"


def test_verdict_rejects_out_of_range_confidence():
    with pytest.raises(ValidationError):
        LLMVerdict.model_validate({"classification": "mtb_marathon", "confidence": 5})


def test_a_classification_result_round_trips_through_the_verdict():
    result = ClassificationResult("mtb_marathon", 0.9, ["note"])

    verdict = LLMVerdict.model_validate(
        {"classification": result.classification, "confidence": result.confidence, "reason": "note"}
    )

    assert verdict.classification == "mtb_marathon"
