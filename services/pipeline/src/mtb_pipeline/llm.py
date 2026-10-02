"""LLM-assisted classification and extraction behind a provider interface.

The rules in :mod:`mtb_pipeline.classification` are the fast path: free,
deterministic and auditable. But some judgement calls — is a "Xxx-Lauf" a
running race with an MTB side course? — need reading the actual text, and
hand-written patterns for every new false-positive class do not scale.

This module keeps the plan's promise (docs/PLAN.md: "keep LLM extraction
behind provider interfaces", "run optional LLM extraction only after
structured extraction fails"): an LLM is just another *source*, consulted
only for records the rules could not decide, and its output is validated
through the same Pydantic models and recorded with provenance like any other
source reference.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from .classification import ClassificationResult

if TYPE_CHECKING:
    pass

#: Bump when the prompt changes materially, so cached answers are invalidated.
PROMPT_VERSION = 1

SYSTEM_PROMPT = """You review sports events for a mountain bike marathon calendar.
The calendar covers: MTB marathons, MTB ultras, point-to-point MTB races, and
multi-day MTB stage races or festivals with a main MTB marathon.
Out of scope: road running, road cycling, gravel-only, XCO/XCC race series,
pure bikepacking without a mass-start race, training camps without a race.
Answer with JSON only, no markdown."""

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {
            "type": "string",
            "enum": [
                "mtb_marathon",
                "mtb_stage_race",
                "mtb_point_to_point",
                "mtb_candidate",
                "exclude",
            ],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["classification", "confidence", "reason"],
    "additionalProperties": False,
}


class LLMError(RuntimeError):
    """The LLM provider failed or returned an unusable answer."""


class Classifier(Protocol):
    """Anything that can second-guess a rule-based classification."""

    def classify_event(
        self,
        *,
        title: str,
        description: str | None,
        discipline: str | None,
        category: str | None,
        distances: list[float] | None,
    ) -> ClassificationResult | None:
        """Return an opinion, or ``None`` when the provider has none."""
        ...


@dataclass(frozen=True)
class LLMConfig:
    """Connection settings for an OpenAI-compatible API.

    The key comes from the environment (``MTB_LLM_API_KEY``) and is never
    stored in the database or the export.
    """

    base_url: str
    model: str
    api_key: str
    timeout_seconds: float = 30.0
    max_retries: int = 2

    @classmethod
    def from_env(cls, *, base_url: str, model: str) -> LLMConfig | None:
        """Read the API key from the environment; ``None`` disables the LLM."""
        api_key = os.environ.get("MTB_LLM_API_KEY", "").strip()
        if not api_key:
            return None
        return cls(base_url=base_url.rstrip("/"), model=model, api_key=api_key)


class LLMClassifier:
    """Classify ambiguous events through an OpenAI-compatible chat API."""

    def __init__(self, config: LLMConfig, *, client: httpx.Client | None = None) -> None:
        self._config = config
        self._client = client or httpx.Client(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def classify_event(
        self,
        *,
        title: str,
        description: str | None,
        discipline: str | None,
        category: str | None,
        distances: list[float] | None,
    ) -> ClassificationResult | None:
        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": self._user_prompt(
                        title, description, discipline, category, distances
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        answer = self._chat(payload)
        return self._parse_answer(answer)

    def _user_prompt(
        self,
        title: str,
        description: str | None,
        discipline: str | None,
        category: str | None,
        distances: list[float] | None,
    ) -> str:
        parts = [f"Title: {title}"]
        if description:
            parts.append(f"Description: {description}")
        if discipline:
            parts.append(f"Source discipline label: {discipline}")
        if category:
            parts.append(f"Source category: {category}")
        if distances:
            formatted = ", ".join(f"{km:g} km" for km in distances)
            parts.append(f"Course distances: {formatted}")
        parts.append(
            "Question: does this belong in an MTB marathon calendar? "
            "Reply as JSON: {\"classification\": ..., \"confidence\": ..., \"reason\": ...}"
        )
        return "\n".join(parts)

    def _chat(self, payload: dict[str, Any]) -> str:
        last_error: Exception | None = None
        for _attempt in range(self._config.max_retries + 1):
            try:
                response = self._client.post("/chat/completions", json=payload)
                response.raise_for_status()
                body = response.json()
                return str(body["choices"][0]["message"]["content"])
            except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as error:
                last_error = error
        raise LLMError(f"LLM request failed after retries: {last_error}") from last_error

    def _parse_answer(self, answer: str) -> ClassificationResult | None:
        try:
            data = json.loads(answer)
        except json.JSONDecodeError:
            return None
        try:
            verdict = LLMVerdict.model_validate(data)
        except ValidationError:
            return None
        if verdict.classification is None:
            return None
        return ClassificationResult(
            classification=verdict.classification,
            confidence=verdict.confidence,
            warnings=[f"llm:{verdict.reason}"],
        )


class LLMVerdict(BaseModel):
    """Validated LLM answer; anything unusable becomes ``None`` upstream."""

    classification: str | None
    confidence: float = Field(ge=0, le=1, default=0.0)
    reason: str = ""


def cache_key(content_hash: str, model: str, prompt_version: int = PROMPT_VERSION) -> str:
    """Stable cache key for one LLM answer.

    Changing the model or the prompt invalidates every cached answer, so a
    prompt improvement re-runs instead of serving stale verdicts.
    """
    digest = hashlib.sha256(
        f"{content_hash}|{model}|{prompt_version}".encode()
    ).hexdigest()
    return digest


class CachedClassifier:
    """Cache answers in front of any :class:`Classifier`.

    The cache key covers the exact event text, the model and the prompt
    version, so the same question never costs a second API call, while an
    edited prompt or a model switch invalidates stale verdicts.
    """

    def __init__(
        self,
        inner: Classifier,
        *,
        cache_path: Path,
        model: str,
    ) -> None:
        self._inner = inner
        self._cache_path = cache_path
        self._model = model

    def classify_event(
        self,
        *,
        title: str,
        description: str | None,
        discipline: str | None,
        category: str | None,
        distances: list[float] | None,
    ) -> ClassificationResult | None:
        content_hash = hashlib.sha256(
            json.dumps(
                {
                    "title": title,
                    "description": description,
                    "discipline": discipline,
                    "category": category,
                    "distances": distances,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        from .llm_cache import get_cached

        cached = get_cached(self._cache_path, content_hash, self._model)
        if cached is not None:
            return self._verdict_from_cache(cached)

        answer = self._inner.classify_event(
            title=title,
            description=description,
            discipline=discipline,
            category=category,
            distances=distances,
        )
        if answer is not None:
            from .llm_cache import put_cached

            put_cached(
                self._cache_path,
                content_hash,
                self._model,
                {
                    "classification": answer.classification,
                    "confidence": answer.confidence,
                    "reason": answer.warnings[0] if answer.warnings else "",
                },
            )
        return answer

    def _verdict_from_cache(self, cached: Any) -> ClassificationResult | None:
        try:
            verdict = LLMVerdict.model_validate(cached)
        except ValidationError:
            return None
        if verdict.classification is None:
            return None
        return ClassificationResult(
            classification=verdict.classification,
            confidence=verdict.confidence,
            warnings=[f"llm:{verdict.reason}"],
        )
