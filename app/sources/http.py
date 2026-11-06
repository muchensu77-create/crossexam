"""HTTP access for public records.

Every response that a finding depends on goes through `fetch`, which caches the raw bytes on disk and reports
their SHA-256 to a `Recorder`. That is what lets a reviewer trace any flag back to the exact bytes it came from.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

CACHE_DIR: Path = settings.data_dir / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
USER_AGENT = "crossexam/0.1 (procurement due diligence research; public records only)"
DEFAULT_TTL_S = 24 * 3600
_slots = threading.BoundedSemaphore(3)  # these are shared public services, so keep the load on them small


class SourceUnavailable(Exception):
    """The source could not be reached. Callers record a coverage gap; they never treat it as a clean result."""


@dataclass
class EvidenceItem:
    source: str
    url: str
    method: str
    sha256: str
    fetched_at: float
    bytes: int
    request: str | None = None


@dataclass
class Recorder:
    items: list[EvidenceItem] = field(default_factory=list)

    def add(self, item: EvidenceItem) -> str:
        self.items.append(item)
        return item.sha256


def _cache_key(method: str, url: str, body: str | None) -> Path:
    digest = hashlib.sha256(f"{method} {url} {body or ''}".encode()).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def fetch(
    source: str,
    method: str,
    url: str,
    *,
    payload: dict | None = None,
    headers: dict | None = None,
    recorder: Recorder | None = None,
    ttl_s: int = DEFAULT_TTL_S,
    timeout_s: float = 30,
    retries: int = 4,
) -> bytes:
    body = json.dumps(payload, sort_keys=True) if payload is not None else None
    path = _cache_key(method, url, body)

    if path.exists() and time.time() - path.stat().st_mtime < ttl_s:
        raw = path.read_bytes()
        fetched_at = path.stat().st_mtime
    else:
        raw = _download(method, url, payload, headers, timeout_s, retries)
        path.write_bytes(raw)
        fetched_at = time.time()

    if recorder is not None:
        recorder.add(
            EvidenceItem(
                source=source,
                url=url,
                method=method,
                sha256=hashlib.sha256(raw).hexdigest(),
                fetched_at=fetched_at,
                bytes=len(raw),
                request=body,
            )
        )
    return raw


def _download(method: str, url: str, payload: dict | None, headers: dict | None, timeout_s: float, retries: int) -> bytes:
    merged = {"User-Agent": USER_AGENT, **(headers or {})}
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with _slots, httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
                response = client.request(method, url, json=payload, headers=merged)
            if response.status_code == 200:
                return response.content
            last_error = SourceUnavailable(f"{url} returned HTTP {response.status_code}")
            if response.status_code < 500 and response.status_code != 429:
                break
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(1.5 * (attempt + 1))
    raise SourceUnavailable(str(last_error))


def fetch_json(source: str, method: str, url: str, **kwargs: Any) -> Any:
    return json.loads(fetch(source, method, url, **kwargs))
