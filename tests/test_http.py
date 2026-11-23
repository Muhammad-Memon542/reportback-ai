from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from reportback.config import Settings
from reportback.ingest.http import HostNotAllowedError, PoliteClient, RateLimiter, cache_path
from tests.conftest import make_settings

CKAN = "https://ckan0.cf.opendata.inter.prod-toronto.ca"


class NoSleepLimiter(RateLimiter):
    def __init__(self) -> None:
        super().__init__(1.0, clock=lambda: 0.0, sleep=lambda _s: None)


def client(settings: Settings, handler: httpx.MockTransport) -> PoliteClient:
    return PoliteClient(settings, transport=handler, rate_limiter=NoSleepLimiter())


def test_rejects_host_outside_allowlist(settings: Settings) -> None:
    calls: list[httpx.Request] = []
    t = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))
    with client(settings, t) as c, pytest.raises(HostNotAllowedError):
        c.get("https://example.com/data.csv")
    assert calls == []


def test_rejects_plain_http_even_on_allowed_host(settings: Settings) -> None:
    t = httpx.MockTransport(lambda r: httpx.Response(200))
    with client(settings, t) as c, pytest.raises(HostNotAllowedError):
        c.get("http://secure.toronto.ca/council/agenda-item.do?item=2023.EX3.1")


def test_rejects_redirect_off_allowlist(settings: Settings) -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.host == "www.toronto.ca":
            return httpx.Response(302, headers={"Location": "https://tracker.example.net/x"})
        return httpx.Response(200)

    with client(settings, httpx.MockTransport(handler)) as c, pytest.raises(HostNotAllowedError):
        c.get("https://www.toronto.ca/legdocs/mmis/2024/ex/bgrd/backgroundfile-1.pdf")


def test_sends_descriptive_user_agent(settings: Settings) -> None:
    seen: dict[str, str] = {}

    def handler(r: httpx.Request) -> httpx.Response:
        seen["ua"] = r.headers["user-agent"]
        return httpx.Response(200, json={"ok": True})

    with client(settings, httpx.MockTransport(handler)) as c:
        c.get_json(f"{CKAN}/api/3/action/status_show")
    assert seen["ua"].startswith("ReportBackTO/")
    assert "@" not in seen["ua"]  # no personal contact data in requests


def test_rate_limiter_enforces_min_interval() -> None:
    now = [100.0]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    rl = RateLimiter(1.0, clock=lambda: now[0], sleep=sleep)
    rl.wait()  # first request: no wait
    now[0] += 0.25
    rl.wait()  # 0.25 s later: must sleep 0.75 s
    now[0] += 5.0
    rl.wait()  # long gap: no wait
    assert slept == [pytest.approx(0.75)]


def test_min_interval_cannot_go_below_one_second(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        make_settings(tmp_path, http_min_interval_s=0.5)


def test_fetch_cached_hits_network_once_and_versions_key(settings: Settings) -> None:
    calls: list[str] = []

    def handler(r: httpx.Request) -> httpx.Response:
        calls.append(str(r.url))
        return httpx.Response(200, content=b"PK\x03\x04 xlsx bytes")

    url = f"{CKAN}/dataset/x/resource/y/download/reportrequestlog2022-2026.xlsx"
    with client(settings, httpx.MockTransport(handler)) as c:
        first = c.fetch_cached(url, version="2026-11-13T13:52:59")
        second = c.fetch_cached(url, version="2026-11-13T13:52:59")
        newer = c.fetch_cached(url, version="2026-12-01T09:00:00")
    assert (first.from_cache, second.from_cache, newer.from_cache) == (False, True, False)
    assert len(calls) == 2
    assert first.path is not None and first.path.read_bytes() == second.content
    assert first.path != newer.path
    assert first.path.is_relative_to(settings.raw_dir)
    assert first.path.name.endswith("reportrequestlog2022-2026.xlsx")


def test_cache_path_is_deterministic(tmp_path: Path) -> None:
    a = cache_path(tmp_path, "https://www.toronto.ca/legdocs/a b.pdf")
    b = cache_path(tmp_path, "https://www.toronto.ca/legdocs/a b.pdf")
    assert a == b
    assert " " not in a.name
