"""LLM classification: the semantic decision stage of the pipeline.

Adapters extract facts and stop; every record they produce enters as a
``mtb_candidate``. This stage reads the actual text — title, description,
distances — and decides the final class. Keyword lists and title regexes
could not keep up with German morphology, French names and the sheer variety
of race names; reading the text generalizes where patterns don't.

Design notes:

* **Batched**: ~20 records per chat call keeps the API cost at cents per
  full collection and the latency at seconds, not minutes.
* **Cached**: answers are stored per record content hash, model and prompt
  version (``llm_cache``), so re-runs only pay for changed records.
* **Validated**: every verdict is parsed through Pydantic; unusable answers
  leave the record as a candidate instead of poisoning the calendar.
* **Degraded mode**: with no API key or on provider failure, records stay
  candidates — visible on the site as unreviewed, never silently dropped.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from .classification import ClassificationResult

#: Bump when the prompt changes materially, so cached answers are invalidated.
PROMPT_VERSION = 2

SYSTEM_PROMPT = """You review sports events for a mountain bike marathon calendar.
The calendar covers: MTB marathons, MTB ultras, point-to-point MTB races, and
multi-day MTB stage races or epic festivals whose main event is an MTB marathon.
Out of scope: road running (including running events that offer an MTB side
course), road cycling, gravel-only events, XCO/XCC cup series, pure
bikepacking without a mass-start race, time trials, training camps without a
race, and non-racing rides.
Classification meanings:
- mtb_marathon: single-day mass-start MTB marathon (roughly 40-120 km)
- mtb_ultra: single-day MTB race beyond marathon distance (over ~120 km) or
  an ultracycling-style MTB challenge
- mtb_stage_race: multi-day event ridden stage by stage (epic style)
- mtb_point_to_point: single-day race over a course that does not loop back
- mtb_candidate: genuinely unclear from the given text
- exclude: anything out of scope
Judge the event by what the text says, not by its title alone. Answer with
JSON only: {"results": [...]} with one object per input, in order."""

BATCH_SIZE = 20


class LLMError(RuntimeError):
    """The LLM provider failed or returned an unusable answer."""


class Verdict(BaseModel):
    """One record's LLM verdict.

    ``index`` is optional because cached entries are stored per record and
    carry no batch position. ``classification`` accepts any string; the
    collect stage maps unknown labels to ``mtb_candidate`` so a deviating
    provider vocabulary degrades instead of crashing.
    """

    index: int | None = None
    classification: str
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


class VerdictBatch(BaseModel):
    results: list[Verdict]


class LLMClassifier:
    """Classify events through an OpenAI-compatible chat API, in batches."""

    def __init__(self, config: LLMConfig, *, client: httpx.Client | None = None) -> None:
        self._config = config
        self._client = client or httpx.Client(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def classify_batch(self, prompts: list[str]) -> list[Verdict | None]:
        """Classify one batch of record prompts, returning aligned verdicts.

        A ``None`` entry means the LLM had no usable verdict for that record;
        the caller keeps the record as a candidate.
        """
        if not prompts:
            return []
        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._batch_prompt(prompts)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        answer = self._chat(payload)
        return self._parse_batch(answer, len(prompts))

    def _batch_prompt(self, prompts: list[str]) -> str:
        numbered = "\n".join(
            f"{index}: {prompt}" for index, prompt in enumerate(prompts)
        )
        return (
            f"Classify each of these {len(prompts)} events. "
            "Reply with one result object per input, carrying its index.\n\n"
            f"{numbered}"
        )

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

    def _parse_batch(self, answer: str, expected: int) -> list[Verdict | None]:
        try:
            data = json.loads(answer)
        except json.JSONDecodeError:
            return [None] * expected
        # Some compatible providers return a bare object instead of
        # {"results": [...]} — accept a single verdict as a one-element batch.
        if isinstance(data, dict) and "results" not in data:
            data = {"results": [data]}
        try:
            batch = VerdictBatch.model_validate(data)
        except ValidationError:
            return [None] * expected
        verdicts: list[Verdict | None] = [None] * expected
        for position, verdict in enumerate(batch.results):
            index = verdict.index if verdict.index is not None else position
            if 0 <= index < expected:
                verdicts[index] = verdict
        return verdicts


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


def record_prompt(*, name: str, description: str | None, distances: list[float] | None) -> str:
    """The text the LLM sees for one record; also the cache identity."""
    parts = [f"Title: {name}"]
    if description:
        parts.append(f"Description: {description}")
    if distances:
        formatted = ", ".join(f"{km:g} km" for km in distances)
        parts.append(f"Course distances: {formatted}")
    return "\n".join(parts)


def prompt_hash(prompt: str, model: str, prompt_version: int = PROMPT_VERSION) -> str:
    """Stable cache key: same text + model + prompt version = same answer."""
    digest = hashlib.sha256(
        f"{prompt}|{model}|{prompt_version}".encode()
    ).hexdigest()
    return digest


def verdict_from_cached(cached: Any) -> ClassificationResult | None:
    """Rebuild a verdict from a cache entry; unusable entries are misses."""
    try:
        verdict = Verdict.model_validate(cached)
    except ValidationError:
        return None
    return ClassificationResult(
        classification=verdict.classification,  # type: ignore[arg-type]
        confidence=verdict.confidence,
        warnings=[f"llm:{verdict.reason}" if verdict.reason else "llm"],
    )
