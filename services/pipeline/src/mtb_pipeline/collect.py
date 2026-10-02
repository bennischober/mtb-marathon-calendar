"""Collection run orchestration: fetch, snapshot, parse, filter, persist."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .http_client import DEFAULT_USER_AGENT, Fetcher
from .llm import Classifier
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
    classifier: Classifier | None = None,
) -> CollectResult:
    """Run one collection pass for a single source.

    ``fetcher`` can be injected so tests can drive the whole pipeline without
    network access; a real fetcher is created and closed otherwise.

    ``on_finished`` and ``on_error`` let the caller record run history. Errors
    are re-raised after ``on_error`` runs, so a failed source is recorded as
    failed instead of disappearing.

    ``classifier`` (typically the LLM-backed one) is consulted only for records
    the rules left ambiguous, so a run without an API key behaves exactly as
    before.
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

    result = CollectResult(
        adapter=adapter.name,
        documents=len(documents),
        parsed=len(editions),
        kept=len(kept),
        excluded=len(editions) - len(kept),
        candidates=sum(1 for edition in kept if edition.classification == "mtb_candidate"),
        editions=kept,
        requests=request_count,
    )

    if classifier is not None:
        kept, second_opinions = _apply_second_opinions(kept, classifier)
        result.second_opinions = second_opinions
        result.editions = kept
        result.candidates = sum(
            1 for edition in kept if edition.classification == "mtb_candidate"
        )

    for edition in kept:
        result.classifications[edition.classification] += 1
        for warning in edition.extraction_warnings:
            result.warnings[warning] += 1

    if on_finished is not None:
        on_finished(result)

    return result


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
    return "\n".join(lines)


def _in_scope(edition: Edition, countries: tuple[str, ...]) -> bool:
    return edition.location.country in countries


def _apply_second_opinions(
    editions: list[Edition], classifier: Classifier
) -> tuple[list[Edition], list[str]]:
    """Re-classify ambiguous records with the LLM classifier.

    Only records the rules flagged as candidates or ambiguous go to the LLM;
    clear marathon/exclude verdicts are too cheap to re-ask. The classifier's
    verdict replaces the classification and its reason is recorded as a
    warning, keeping the decision auditable.
    """
    second_opinions: list[str] = []
    reclassified: list[Edition] = []
    for edition in editions:
        ambiguous = edition.classification == "mtb_candidate" or any(
            "ambiguous" in warning or "needs_review" in warning
            for warning in edition.extraction_warnings
        )
        if not ambiguous:
            reclassified.append(edition)
            continue
        verdict = classifier.classify_event(
            title=edition.name,
            description=edition.description,
            discipline=None,
            category=None,
            distances=[course.distance_km for course in edition.course_variants],
        )
        if verdict is None:
            reclassified.append(edition)
            continue
        reclassified.append(
            edition.model_copy(
                update={
                    "classification": verdict.classification,
                    "classification_confidence": verdict.confidence,
                    "extraction_warnings": [
                        *edition.extraction_warnings,
                        *verdict.warnings,
                    ],
                }
            )
        )
        second_opinions.append(f"{edition.id}: llm -> {verdict.classification}")
    return reclassified, second_opinions
