"""Polite HTTP fetcher: host allowlist, >=1 s between requests, descriptive User-Agent, raw-file cache.

Every external fetch in ReportBack goes through `PoliteClient`.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from reportback.config import Settings


class HostNotAllowedError(RuntimeError):
    """Raised when a request (or a redirect) targets a host outside the allowlist."""


class RateLimiter:
    """Enforces a minimum interval between consecutive requests (single-threaded)."""

    def __init__(
        self,
        min_interval_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval_s = min_interval_s
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            remaining = self.min_interval_s - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                now = self._clock()
        self._last = now


@dataclass(frozen=True)
class FetchResult:
    url: str
    content: bytes
    from_cache: bool
    path: Path | None  # cache file, when cached


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def cache_path(raw_dir: Path, url: str, version: str | None = None) -> Path:
    """Deterministic cache location: data/raw/<host>/<sha16(url|version)>-<basename>."""
    parts = urlsplit(url)
    basename = _SAFE.sub("_", Path(parts.path).name or "index")[:80]
    key = hashlib.sha256(f"{url}|{version or ''}".encode()).hexdigest()[:16]
    return raw_dir / parts.netloc / f"{key}-{basename}"


class PoliteClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self.allowed_hosts = frozenset(h.lower() for h in settings.allowed_hosts)
        self.limiter = rate_limiter or RateLimiter(settings.http_min_interval_s)
        self._client = httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.http_timeout_s,
            follow_redirects=True,
            transport=transport,
            # Fires for every request, including each redirect hop.
            event_hooks={"request": [self._check_host]},
        )

    def _check_host(self, request: httpx.Request) -> None:
        host = (request.url.host or "").lower()
        if request.url.scheme != "https" or host not in self.allowed_hosts:
            raise HostNotAllowedError(f"Refusing to fetch {request.url!s}: host not in allowlist")

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """Uncached, rate-limited GET (for APIs whose answers change, e.g. CKAN metadata)."""
        self.limiter.wait()
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        return resp

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self.get(url, params=params).json()

    def fetch_cached(self, url: str, *, version: str | None = None) -> FetchResult:
        """GET a raw file, caching it under data/raw/. `version` (e.g. CKAN last_modified)
        is part of the cache key so a re-published file gets a new cache entry."""
        path = cache_path(self.settings.raw_dir, url, version)
        if path.exists():
            return FetchResult(url=url, content=path.read_bytes(), from_cache=True, path=path)
        content = self.get(url).content
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(content)
        tmp.replace(path)
        return FetchResult(url=url, content=content, from_cache=False, path=path)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
