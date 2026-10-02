import hashlib
import json

import httpx
import pytest

from mtb_pipeline.http_client import (
    DEFAULT_USER_AGENT,
    Fetcher,
    FetchError,
    RobotsDisallowed,
)

ALLOWED_ROBOTS = """User-agent: *
Allow: /
Disallow: /admin
"""

DISALLOWED_ROBOTS = """User-agent: *
Disallow: /
"""


def make_fetcher(handler, **kwargs) -> Fetcher:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    return Fetcher(
        http_client=client,
        delay_seconds=0,
        sleep=lambda _seconds: None,
        **kwargs,
    )


def robots_handler(robots_body: str, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(status_code, text=robots_body)
        return httpx.Response(200, json={"ok": True})

    return handler


def test_robots_txt_is_honoured():
    fetcher = make_fetcher(robots_handler(ALLOWED_ROBOTS))

    assert fetcher.get("https://example.org/events").json() == {"ok": True}
    with pytest.raises(RobotsDisallowed):
        fetcher.get("https://example.org/admin")


def test_disallowed_by_robots_stops_the_request():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=DISALLOWED_ROBOTS)
        raise AssertionError("no request should reach the server")

    fetcher = make_fetcher(handler)
    with pytest.raises(RobotsDisallowed):
        fetcher.get("https://example.org/events")


def test_missing_robots_txt_allows_access():
    """RFC 9309: a 4xx robots.txt means allow all."""
    fetcher = make_fetcher(robots_handler("", status_code=403))

    assert fetcher.get("https://example.org/events").json() == {"ok": True}


def test_server_error_robots_txt_blocks_access():
    """RFC 9309: a 5xx robots.txt means disallow all."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(503, text="")
        raise AssertionError("no request should reach the server")

    fetcher = make_fetcher(handler)
    with pytest.raises(RobotsDisallowed):
        fetcher.get("https://example.org/events")


def test_robots_txt_is_only_fetched_once_per_host():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOWED_ROBOTS)
        return httpx.Response(200, json={"ok": True})

    fetcher = make_fetcher(handler)
    fetcher.get("https://example.org/one")
    fetcher.get("https://example.org/two")

    assert calls.count("/robots.txt") == 1


def test_snapshots_are_written_with_metadata(tmp_path):
    fetcher = make_fetcher(
        robots_handler(ALLOWED_ROBOTS), raw_dir=tmp_path, user_agent="test-agent/1.0"
    )
    document = fetcher.get("https://example.org/events", accept="application/json")

    snapshots = list(tmp_path.rglob("*.json"))
    bodies = [path for path in snapshots if not path.name.endswith(".meta.json")]

    assert len(bodies) == 1
    assert bodies[0].read_bytes() == document.content
    assert document.content_hash == hashlib.sha256(document.content).hexdigest()

    metadata = json.loads(next(tmp_path.rglob("*.meta.json")).read_text(encoding="utf-8"))
    assert metadata["url"] == "https://example.org/events"
    assert metadata["content_hash"] == document.content_hash
    assert metadata["user_agent"] == "test-agent/1.0"


def test_identical_content_reuses_the_same_snapshot(tmp_path):
    fetcher = make_fetcher(robots_handler(ALLOWED_ROBOTS), raw_dir=tmp_path)

    first = fetcher.get("https://example.org/a")
    second = fetcher.get("https://example.org/b")

    assert first.content_hash == second.content_hash
    assert len([p for p in tmp_path.rglob("*.json") if not p.name.endswith(".meta.json")]) == 1


def test_error_status_raises_fetch_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOWED_ROBOTS)
        return httpx.Response(404, text="missing")

    fetcher = make_fetcher(handler)
    with pytest.raises(FetchError, match="404"):
        fetcher.get("https://example.org/missing")


def test_server_errors_are_retried():
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOWED_ROBOTS)
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={"ok": True})

    fetcher = make_fetcher(handler, max_retries=3)
    assert fetcher.get("https://example.org/events").json() == {"ok": True}
    assert len(attempts) == 3


def test_gives_up_after_max_retries():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOWED_ROBOTS)
        return httpx.Response(500)

    fetcher = make_fetcher(handler, max_retries=2)
    with pytest.raises(FetchError):
        fetcher.get("https://example.org/events")


def test_default_user_agent_identifies_the_project():
    assert "mtb-marathon-calendar" in DEFAULT_USER_AGENT
