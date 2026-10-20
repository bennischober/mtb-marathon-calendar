"""Automated source discovery: find races beyond the known calendars.

The known adapters cover sources we deliberately maintain. Discovery is the
other half of the pipeline's promise: continuously *find* candidate races
from permissive public directories, sitemaps and feeds, screen them, and
report what is new — instead of waiting for someone to notice a missing race.

The flow (each step logged, every claim traceable):

1. **Crawl** a discovery source (a sitemap or directory listing) and extract
   candidate events: name + description, the minimum a public page offers.
2. **Pre-filter** cheaply on country signals (a five-country calendar does
   not need 200 LLM calls for obviously-international candidates).
3. **Evaluate** the survivors with the LLM: in scope or not, which country,
   why. Answers are cached by content like every other LLM verdict.
4. **Report**: candidates that pass are proposed — never auto-imported. A
   human decides whether a race enters the calendar (and usually which
   source should cover it properly).
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from .llm import DISCOVERY_PROMPT_VERSION, LLMClassifier
from .llm_cache import get_cached, put_cached

# Cheap country pre-filter: which candidate text plausibly mentions one of
# the five countries (or well-known regions) before spending LLM calls.
EURO_HINTS = (
    "germany",
    "deutschland",
    "bavaria",
    "bayern",
    "black-forest",
    "schwarzwald",
    "allgaeu",
    "alps",
    "alpen",
    "tyrol",
    "tirol",
    "suedtirol",
    "dolomit",
    "garda",
    "austria",
    "osterreich",
    "swiss",
    "schweiz",
    "italy",
    "italia",
    "france",
    "andorra",
    "vosges",
    "jura",
    "vogesen",
    "eifel",
    "hunsrueck",
    "hunsruck",
    "erzgebirge",
    "harz",
    "rhein",
    "mosel",
    "taunus",
    "odenwald",
    "emilia",
    "romagna",
    "appennin",
    "schladming",
    "dachstein",
    "vanoise",
    "maurienne",
    "pyrenees",
)


@dataclass
class DiscoveryCandidate:
    """One candidate event scraped from a discovery source."""

    source: str
    slug: str
    url: str
    name: str
    description: str
    verdict: str | None = None
    country: str | None = None
    reason: str | None = None


@dataclass
class DiscoveryResult:
    candidates: list[DiscoveryCandidate] = field(default_factory=list)
    evaluated: int = 0
    in_scope: list[DiscoveryCandidate] = field(default_factory=list)
    new_names: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = [
            f"discovery: {len(self.candidates)} candidates crawled, "
            f"{self.evaluated} evaluated, {len(self.in_scope)} in scope",
        ]
        for candidate in self.in_scope:
            lines.append(
                f"  {candidate.country or '--'} {candidate.name} "
                f"({candidate.url}) — {candidate.reason}"
            )
        return lines


class DiscoveryScraper(Protocol):
    """Extracts candidates from one discovery source."""

    def discover(self) -> list[DiscoveryCandidate]:
        """Return every candidate the source currently lists."""
        ...


class StageracesDiscovery:
    """stageraces.com: permissive WordPress directory of stage races.

    robots.txt allows everything and publishes an event sitemap; pages carry
    the name in ``<h1>`` and a description in ``og:description``. Dates are
    mostly absent, so this source *proposes* races — the calendar entry comes
    from the organizer's own page, not from here.
    """

    SITEMAP = "https://stageraces.com/event-sitemap.xml"

    def __init__(self, *, client: httpx.Client | None = None, delay: float = 0.2) -> None:
        self._client = client or httpx.Client(
            headers={"User-Agent": "mtb-marathon-calendar/0.1 (discovery)"},
            timeout=15,
        )
        self._delay = delay

    def discover(self) -> list[DiscoveryCandidate]:
        sitemap = self._client.get(self.SITEMAP).text
        urls = re.findall(r"<loc>([^<]+)</loc>", sitemap)

        def scrape(url: str) -> DiscoveryCandidate | None:
            try:
                page = self._client.get(url).text
            except httpx.HTTPError:
                return None
            h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
            og = re.search(r'property="og:description"\s+content="([^"]+)"', page)
            name = re.sub(r"<[^>]+>", "", h1.group(1)).strip() if h1 else ""
            description = (og.group(1) if og else "").strip()
            return DiscoveryCandidate(
                source="stageraces",
                slug=url.rstrip("/").split("/")[-1],
                url=url,
                name=name,
                description=description,
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            candidates = [
                candidate for candidate in pool.map(scrape, urls) if candidate is not None
            ]
        return candidates


def prefilter_country(candidates: list[DiscoveryCandidate]) -> list[DiscoveryCandidate]:
    """Keep candidates whose text plausibly touches the five countries."""
    kept = []
    for candidate in candidates:
        blob = f"{candidate.slug} {candidate.name} {candidate.description}".lower()
        if any(hint in blob for hint in EURO_HINTS):
            kept.append(candidate)
    return kept


EVALUATE_SYSTEM = """You screen sport events for a mountain bike marathon calendar covering
DE, AT, CH, IT, FR. In scope: MTB marathons, MTB ultras, MTB point-to-point, multi-day
MTB stage races (epic style). Out of scope: trail running, road cycling, gravel-only,
enduro/XCO racing, non-race rides, and events outside the five countries.
Return JSON: {"results":[{"index": <n>, "verdict": "in_scope"|"out_of_scope",
"country": "<ISO or null>", "reason": "<max 10 words>"}]}"""


def evaluate_candidates(
    candidates: list[DiscoveryCandidate],
    classifier: LLMClassifier,
    *,
    known_names: set[str],
) -> list[DiscoveryCandidate]:
    """LLM screen + newness check; verdicts cached by candidate text.

    One candidate per call, with the full description: batched evaluation
    measurably confused the model (wrong countries and verdicts), while
    single calls with complete text are accurate. Cached, so the cost is
    paid once per candidate text.
    """
    evaluated: list[DiscoveryCandidate] = []
    model = classifier.model_name
    for candidate in candidates:
        prompt = f"Event: {candidate.name}\nDescription: {candidate.description}"
        cache_key = f"discovery:v{DISCOVERY_PROMPT_VERSION}:{prompt}"
        verdict = get_cached(Path("data/llm-cache.sqlite3"), cache_key, model)
        if verdict is None:
            verdict = classifier.discovery_single(prompt)
            if verdict is not None:
                put_cached(Path("data/llm-cache.sqlite3"), cache_key, model, verdict)
        if verdict is None:
            continue
        candidate.verdict = verdict.get("verdict")
        candidate.country = verdict.get("country")
        candidate.reason = verdict.get("reason")
        evaluated.append(candidate)
        if candidate.verdict == "in_scope" and not _known(candidate.name, known_names):
            candidate.reason = (candidate.reason or "") + " [new]"
    return evaluated


def _known(name: str, known_names: set[str]) -> bool:
    """True when a known calendar race and the candidate name overlap strongly.

    Sponsor names shift ("SPAR Swiss Epic" vs "Swiss Epic"), so containment
    on the shared distinctive part is checked rather than equality.
    """
    generic = {"mtb", "bike", "race", "epic", "the", "stage"}
    candidate_tokens = set(re.findall(r"[a-z0-9]+", name.lower())) - generic
    for known in known_names:
        known_tokens = set(re.findall(r"[a-z0-9]+", known.lower())) - generic
        overlap = candidate_tokens & known_tokens
        if not overlap:
            continue
        # Match when the candidate covers most of the known name (sponsor
        # prefixes like "SPAR" make the candidate token set larger, so the
        # threshold is relative to the smaller set).
        smaller = min(len(candidate_tokens), len(known_tokens))
        if len(overlap) >= min(2, smaller):
            return True
    return False


def run_discovery(
    scraper: DiscoveryScraper,
    classifier: LLMClassifier,
    *,
    known_names: set[str],
    max_candidates: int | None = None,
) -> DiscoveryResult:
    """Full discovery pass: crawl, pre-filter, evaluate, propose."""
    result = DiscoveryResult()
    result.candidates = scraper.discover()
    if max_candidates:
        result.candidates = result.candidates[:max_candidates]

    survivors = prefilter_country(result.candidates)

    evaluated = evaluate_candidates(survivors, classifier, known_names=known_names)
    result.evaluated = len(evaluated)
    result.in_scope = [candidate for candidate in evaluated if candidate.verdict == "in_scope"]
    result.new_names = [
        candidate.name for candidate in result.in_scope if "[new]" in (candidate.reason or "")
    ]
    return result
