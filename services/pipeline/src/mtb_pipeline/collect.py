"""Collection run orchestration: fetch, snapshot, parse, LLM-classify, persist."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .http_client import DEFAULT_USER_AGENT, Fetcher
from .llm import (
    BATCH_SIZE,
    LLMClassifier,
    Verdict,
    record_prompt,
)
from .llm_cache import get_cached, put_cached
from .models import Edition
from .sources import CollectOptions, SourceAdapter
from .sources.radsport_events import SUPPORTED_COUNTRIES


@dataclass
class CollectResult:
    adapter: str
    documents: int
    parsed: int
    kept: int
    excluded: int
    candidates: int
    classifications: Counter[str] = field(default_factory=Counter)
    warnings: Counter[str] = field(default_factory=Counter)
    editions: list[Edition] = field(default_factory=list)
    requests: int = 0
    second_opinions: list[str] = field(default_factory=list)
    llm_calls: int = 0
    llm_cache_hits: int = 0
    llm_failures: int = 0
    degraded: bool = False


def collect(
    adapter: SourceAdapter,
    *,
    countries: tuple[str, ...] = SUPPORTED_COUNTRIES,
    raw_dir: Path | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    delay_seconds: float = 1.0,
    options: CollectOptions | None = None,
    fetcher: Fetcher | None = None,
    on_finished: Callable[[CollectResult], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
    classifier: LLMClassifier | None = None,
    classifier_cache: Path | None = None,
) -> CollectResult:
    """Run one collection pass for a single source.

    ``fetcher`` can be injected so tests can drive the whole pipeline without
    network access; a real fetcher is created and closed otherwise.

    ``on_finished`` and ``on_error`` let the caller record run history. Errors
    are re-raised after ``on_error`` runs, so a failed source is recorded as
    failed instead of disappearing.

    ``classifier`` runs the LLM classification stage over every kept record.
    Without one (no API key), records stay ``mtb_candidate`` — degraded mode:
    visible, reviewable, never silently dropped.
    """
    options = options or CollectOptions()

    try:
        if fetcher is not None:
            documents = adapter.fetch(fetcher, options)
            request_count = fetcher.request_count
        else:
            with Fetcher(
                user_agent=user_agent,
                delay_seconds=delay_seconds,
                raw_dir=raw_dir,
            ) as owned_fetcher:
                documents = adapter.fetch(owned_fetcher, options)
                request_count = owned_fetcher.request_count
        # Parsing belongs inside the guard. A season PDF that is replaced with a
        # different layout can break parsing, and a broken parse must be
        # recorded as a failed run, otherwise the source still looks healthy and
        # the publish gate has nothing to catch.
        editions = adapter.parse(documents)
    except Exception as error:
        if on_error is not None:
            on_error(error)
        raise

    kept = [
        edition
        for edition in editions
        if edition.classification != "exclude" and _in_scope(edition, countries)
    ]

    llm_calls = 0
    llm_cache_hits = 0
    llm_failures = 0
    second_opinions: list[str] = []
    if classifier is not None:
        kept, llm_calls, llm_cache_hits, llm_failures, second_opinions = _classify_with_llm(
            kept, classifier, classifier_cache
        )
        # The LLM may exclude records the scope filter let through; apply the
        # filter again so an LLM-excluded record is neither persisted nor
        # counted as kept.
        kept = [edition for edition in kept if edition.classification != "exclude"]

    result = CollectResult(
        adapter=adapter.name,
        documents=len(documents),
        parsed=len(editions),
        kept=len(kept),
        excluded=len(editions) - len(kept),
        candidates=sum(1 for edition in kept if edition.classification == "mtb_candidate"),
        editions=kept,
        requests=request_count,
        second_opinions=second_opinions,
        llm_calls=llm_calls,
        llm_cache_hits=llm_cache_hits,
        llm_failures=llm_failures,
        # Degraded when records are still candidates that an LLM would have
        # decided but none was available or it failed.
        degraded=(
            classifier is None or llm_failures > 0
        ) and any(edition.classification == "mtb_candidate" for edition in kept),
    )

    for edition in kept:
        result.classifications[edition.classification] += 1
        for warning in edition.extraction_warnings:
            result.warnings[warning] += 1

    if on_finished is not None:
        on_finished(result)

    return result


def _classify_with_llm(
    editions: list[Edition],
    classifier: LLMClassifier,
    cache_path: Path | None,
) -> tuple[list[Edition], int, int, int, list[str]]:
    """Decide the final classification for every record with the LLM.

    Records are prompted in batches; answers are cached per record text, so
    re-runs only pay for new or changed records. A failed or unusable verdict
    leaves the record as a candidate — degraded, not dropped.
    """
    prompts = [
        record_prompt(
            name=edition.name,
            description=edition.description,
            distances=[course.distance_km for course in edition.course_variants] or None,
        )
        for edition in editions
    ]
    verdicts: list[Verdict | None] = [None] * len(editions)
    uncached: list[int] = []
    cache_hits = 0
    if cache_path is not None:
        for index, prompt in enumerate(prompts):
            cached = get_cached(cache_path, prompt, classifier_model(classifier))
            if cached is None:
                uncached.append(index)
            else:
                try:
                    verdicts[index] = Verdict.model_validate(cached)
                except Exception:  # noqa: BLE001 — a corrupt entry is a miss
                    uncached.append(index)
                else:
                    if verdicts[index] is not None:
                        cache_hits += 1
    else:
        uncached = list(range(len(editions)))

    failures = 0
    for start in range(0, len(uncached), BATCH_SIZE):
        batch = uncached[start : start + BATCH_SIZE]
        try:
            answers = classifier.classify_batch([prompts[index] for index in batch])
        except Exception:  # noqa: BLE001 — degraded mode must survive provider outages
            failures += len(batch)
            continue
        for index, verdict in zip(batch, answers, strict=False):
            if verdict is None:
                failures += 1
                continue
            verdicts[index] = verdict
            if cache_path is not None:
                put_cached(
                    cache_path,
                    prompts[index],
                    classifier_model(classifier),
                    {
                        "classification": verdict.classification,
                        "confidence": verdict.confidence,
                        "reason": verdict.reason,
                    },
                )

    decided: list[Edition] = []
    notes: list[str] = []
    for edition, verdict in zip(editions, verdicts, strict=True):
        if verdict is None:
            decided.append(edition)
            continue
        decided.append(
            edition.model_copy(
                update={
                    "classification": _classification(verdict.classification),
                    "classification_confidence": verdict.confidence,
                    "extraction_warnings": [
                        *edition.extraction_warnings,
                        *( [f"llm:{verdict.reason}"] if verdict.reason else ["llm"] ),
                    ],
                }
            )
        )
        notes.append(f"{edition.id}: llm -> {verdict.classification}")
    api_calls = (len(uncached) + BATCH_SIZE - 1) // BATCH_SIZE
    return decided, api_calls, cache_hits, failures, notes


def _verdict_or_none(result):
    return result


def classifier_model(classifier: LLMClassifier) -> str:
    return classifier._config.model  # noqa: SLF001 — the cache key needs the model name


def _classification(value: str) -> str:
    from .models import Classification

    allowed = Classification.__args__ if hasattr(Classification, "__args__") else None
    return value if value in allowed else "mtb_candidate"


def summarize(result: CollectResult, *, generated_at: datetime | None = None) -> str:
    timestamp = (generated_at or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"source={result.adapter} at {timestamp}",
        f"  requests={result.requests} documents={result.documents} "
        f"parsed={result.parsed} kept={result.kept} excluded={result.excluded} "
        f"candidates={result.candidates}",
    ]
    for classification, count in result.classifications.most_common():
        lines.append(f"  {classification}: {count}")
    for warning, count in result.warnings.most_common(10):
        lines.append(f"  warning {warning}: {count}")
    for opinion in result.second_opinions:
        lines.append(f"  llm {opinion}")
    if result.llm_cache_hits:
        lines.append(f"  llm cache hits: {result.llm_cache_hits}")
    if result.llm_failures:
        lines.append(f"  llm failures (kept as candidates): {result.llm_failures}")
    if result.degraded:
        lines.append("  DEGRADED: records await LLM classification or human review")
    return "\n".join(lines)


def _in_scope(edition: Edition, countries: tuple[str, ...]) -> bool:
    return edition.location.country in countries
