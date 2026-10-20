"""Polite HTTP fetching with robots.txt checks, rate limiting and raw snapshots."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx

DEFAULT_USER_AGENT = (
    "mtb-marathon-calendar/0.1 (+https://github.com/mtb-marathon-calendar; "
    "contact: set-your-email@example.org)"
)


class RobotsDisallowed(RuntimeError):
    """Raised when robots.txt forbids fetching a URL for our user agent."""


class FetchError(RuntimeError):
    """Raised when a request fails or returns an unusable response."""


@dataclass(frozen=True)
class FetchedDocument:
    """One retrieved resource plus everything needed to reproduce and audit it."""

    url: str
    content: bytes
    content_type: str | None
    retrieved_at: datetime
    status_code: int
    content_hash: str
    from_cache: bool = False
    etag: str | None = None
    last_modified: str | None = None

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> object:
        return json.loads(self.text)


@dataclass
class RobotsRules:
    """RFC 9309 rules for one user-agent group, evaluated with longest match.

    Implemented here instead of delegating to ``urllib.robotparser`` because
    that module changed its matching algorithm in CPython 3.13 (first-match to
    RFC 9309 longest-match). On 3.12 an ``Allow: /`` placed before a more
    specific ``Disallow:`` silently wins, which would make the crawler ignore
    path-specific exclusions — the opposite of what politeness requires.
    """

    allow: tuple[str, ...]
    disallow: tuple[str, ...]

    def allows(self, url: str) -> bool:
        path = _path_of(url)
        allow = _longest_match(self.allow, path)
        disallow = _longest_match(self.disallow, path)
        if disallow is None:
            return True
        if allow is None:
            return False
        return allow >= disallow


def _path_of(url: str) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path or "/")
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def _longest_match(patterns: tuple[str, ...], path: str) -> int | None:
    """Length of the longest prefix ``patterns`` match on ``path``, or None.

    RFC 9309 §2.2.2: a pattern matches the beginning of the path (so ``/``
    matches everything); ``*`` matches any character sequence and ``$``
    anchors the pattern to the path's end.
    """
    best: int | None = None
    for pattern in patterns:
        if pattern == "":
            # An empty Disallow value allows everything in this group.
            continue
        length = _matched_prefix_length(pattern, path)
        if length is not None and (best is None or length > best):
            best = length
    return best


def _matched_prefix_length(pattern: str, path: str) -> int | None:
    """Length of the path prefix ``pattern`` matches, or None.

    The matched length — not the pattern's source length — decides which
    pattern wins, because a wildcard pattern can consume a longer prefix than
    a short literal one.
    """
    if "$" in pattern:
        head = pattern.partition("$")[0]
        if re.fullmatch(f"{_pattern_regex(head)}.*", path):
            return len(path)
        return None
    match = re.match(_pattern_regex(pattern), path)
    return match.end() if match else None


def _pattern_regex(pattern: str) -> str:
    """Translate a robots pattern into an unanchored regex fragment."""
    return ".*".join(re.escape(part) for part in pattern.split("*"))


@dataclass
class RobotsCache:
    """Per-host robots.txt decisions, evaluated once per run.

    Following RFC 9309: a robots.txt that cannot be retrieved because of a 4xx
    status is treated as "allow all", while a 5xx status means "disallow all"
    until the server recovers.
    """

    user_agent: str
    client: httpx.Client
    _rules: dict[str, RobotsRules | None] = field(default_factory=dict)
    _unavailable: set[str] = field(default_factory=set)

    def allowed(self, url: str) -> bool:
        host_key = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        if host_key not in self._rules:
            self._load(host_key)
        if host_key in self._unavailable:
            return False
        rules = self._rules[host_key]
        if rules is None:
            return True
        return rules.allows(url)

    def _load(self, host_key: str) -> None:
        robots_url = urljoin(host_key, "/robots.txt")
        try:
            response = self.client.get(robots_url, headers={"Accept": "text/plain"})
        except httpx.HTTPError:
            self._unavailable.add(host_key)
            self._rules[host_key] = None
            return

        if response.status_code >= 500:
            self._unavailable.add(host_key)
            self._rules[host_key] = None
            return
        if response.status_code >= 400:
            self._rules[host_key] = None
            return

        self._rules[host_key] = _parse_robots(response.text, self.user_agent)


def _parse_robots(text: str, user_agent: str) -> RobotsRules:
    """Extract the rules of the group that best matches ``user_agent``.

    RFC 9309 §2.3: when several groups match, the most specific agent wins;
    the ``*`` group applies only when no named group matches. Lines before
    the first ``User-agent:`` are ignored, and each rule belongs to the group
    whose ``User-agent:`` line most recently preceded it.
    """
    agent = user_agent.lower()
    groups: list[tuple[str, RobotsRules]] = []
    allow: list[str] = []
    disallow: list[str] = []
    agents: list[str] = []
    in_group = False

    def flush() -> None:
        nonlocal allow, disallow, agents, in_group
        if agents and (allow or disallow):
            groups.append((tuple(agents), RobotsRules(tuple(allow), tuple(disallow))))
        allow, disallow, agents = [], [], []
        in_group = False

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if in_group and allow or disallow or agents:
                flush()
            agents.append(value)
            in_group = False
        elif key in ("allow", "disallow"):
            in_group = True
            (allow if key == "allow" else disallow).append(value)
        # crawl-delay, sitemap and unknown keys are ignored.
    flush()

    best: RobotsRules | None = None
    best_specificity = -1
    for group_agents, rules in groups:
        for candidate in group_agents:
            candidate = candidate.lower()
            if candidate == "*":
                if best_specificity < 0:
                    best, best_specificity = rules, 0
            elif agent and (candidate in agent or agent in candidate):
                # Named groups beat ``*``; among named groups the last
                # matching one wins (order is the file's, specificity of
                # partial tokens is not further ranked).
                if best_specificity < 1:
                    best, best_specificity = rules, 1
    return best or RobotsRules((), ())


class Fetcher:
    """Fetches documents, keeping a local raw snapshot of everything it reads.

    Snapshots are written under ``raw_dir`` keyed by content hash so that a
    later adapter change can be re-parsed against exactly the bytes that were
    retrieved at collection time.
    """

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        delay_seconds: float = 1.0,
        timeout_seconds: float = 30.0,
        raw_dir: Path | None = None,
        respect_robots: bool = True,
        max_retries: int = 3,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.user_agent = user_agent
        self.delay_seconds = delay_seconds
        self.raw_dir = raw_dir
        self.respect_robots = respect_robots
        self.max_retries = max_retries
        self._sleep = sleep
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        )
        self._robots = RobotsCache(user_agent=user_agent, client=self._client)
        self._last_request_at = 0.0
        self.request_count = 0

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def get(
        self,
        url: str,
        *,
        params: dict[str, object] | None = None,
        accept: str = "*/*",
        headers: dict[str, str] | None = None,
        snapshot_name: str | None = None,
    ) -> FetchedDocument:
        if self.respect_robots and not self._robots.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows {url} for {self.user_agent}")

        request_headers = {"Accept": accept}
        if headers:
            request_headers.update(headers)

        response = self._request_with_retries(url, params, request_headers)
        self.request_count += 1
        self._throttle()

        if response.status_code >= 400:
            raise FetchError(f"{url} returned HTTP {response.status_code}")

        content = response.content
        digest = hashlib.sha256(content).hexdigest()
        document = FetchedDocument(
            url=str(response.url),
            content=content,
            content_type=response.headers.get("content-type"),
            retrieved_at=datetime.now(UTC),
            status_code=response.status_code,
            content_hash=digest,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
        )

        if self.raw_dir is not None:
            self._write_snapshot(document, snapshot_name or _default_snapshot_name(url))
        return document

    def post(
        self,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        snapshot_name: str | None = None,
    ) -> FetchedDocument:
        """POST like :meth:`get` — robots-checked, retried, snapshotted.

        Needed for filter-form navigation (ASP.NET postbacks), which is
        mechanical page retrieval, not parsing.
        """
        if self.respect_robots and not self._robots.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows {url} for {self.user_agent}")

        request_headers = {"Accept": "*/*"}
        if headers:
            request_headers.update(headers)

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.post(url, content=data, headers=request_headers)
                break
            except httpx.HTTPError as error:
                last_error = error
                time.sleep(2**attempt)
        else:
            raise FetchError(f"{url} failed after retries: {last_error}") from last_error

        self.request_count += 1
        self._throttle()

        if response.status_code >= 400:
            raise FetchError(f"{url} returned HTTP {response.status_code}")

        content = response.content
        digest = hashlib.sha256(content).hexdigest()
        document = FetchedDocument(
            url=str(response.url),
            content=content,
            content_type=response.headers.get("content-type"),
            retrieved_at=datetime.now(UTC),
            status_code=response.status_code,
            content_hash=digest,
        )
        if self.raw_dir is not None:
            self._write_snapshot(document, snapshot_name or _default_snapshot_name(url + "#post"))
        return document

    def _request_with_retries(
        self,
        url: str,
        params: dict[str, object] | None,
        headers: dict[str, str],
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code < 500:
                    return response
                last_error = FetchError(f"{url} returned HTTP {response.status_code}")
            if attempt < self.max_retries - 1:
                self._sleep(self.delay_seconds * (2**attempt))
        raise FetchError(f"failed to fetch {url}: {last_error}") from last_error

    def _throttle(self) -> None:
        if self.delay_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay_seconds:
            self._sleep(self.delay_seconds - elapsed)
        self._last_request_at = time.monotonic()

    def _write_snapshot(self, document: FetchedDocument, name: str) -> Path:
        """Store the retrieved bytes content-addressed, with per-URL metadata.

        Identical payloads are written once even when several URLs return them,
        while each URL keeps its own retrieval record for auditing.
        """
        host = urlparse(document.url).netloc.replace(":", "_")
        directory = self.raw_dir / host  # type: ignore[operator]
        directory.mkdir(parents=True, exist_ok=True)

        body_path = directory / f"{document.content_hash[:16]}{_extension(document)}"
        if not body_path.exists():
            body_path.write_bytes(document.content)

        metadata_path = directory / f"{name}.meta.json"
        metadata = {
            "url": document.url,
            "status_code": document.status_code,
            "content_type": document.content_type,
            "retrieved_at": document.retrieved_at.isoformat(),
            "content_hash": document.content_hash,
            "etag": document.etag,
            "last_modified": document.last_modified,
            "user_agent": self.user_agent,
        }
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return body_path


def _default_snapshot_name(url: str) -> str:
    parsed = urlparse(url)
    slug = (parsed.path.strip("/") or "index").replace("/", "-")[:60]
    return f"{slug}-{parsed.query[:40]}" if parsed.query else slug


def _extension(document: FetchedDocument) -> str:
    content_type = (document.content_type or "").lower()
    if "json" in content_type:
        return ".json"
    if "html" in content_type:
        return ".html"
    if "xml" in content_type:
        return ".xml"
    if "text/plain" in content_type:
        return ".txt"
    return ".bin"
