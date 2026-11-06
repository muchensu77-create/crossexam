"""Federal procurement exclusions (suspensions and debarments from SAM.gov).

Read from the daily OpenSanctions export of the GSA list, which needs no key and carries UEIs for exact matching.
OpenSanctions data is free for non-commercial use; a commercial deployment would call the SAM.gov API directly.
"""
from __future__ import annotations

import csv
import hashlib
import io
import sys
from dataclasses import dataclass
from functools import lru_cache

from rapidfuzz import fuzz

from app.sources.http import EvidenceItem, Recorder, fetch
from app.sources.ofac import normalize_name

URL = "https://data.opensanctions.org/datasets/latest/us_sam_exclusions/targets.simple.csv"
SOURCE = "SAM.gov exclusions (via OpenSanctions)"
MATCH_THRESHOLD = 94
LIST_TTL_S = 3 * 24 * 3600

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


@dataclass
class ExclusionHit:
    name: str
    matched_on: str
    score: float
    detail: str
    entry_id: str


@lru_cache(maxsize=1)
def _load(raw_hash: str, raw: bytes) -> tuple[dict[str, int], list[tuple[str, list[str], str, str]]]:
    by_identifier: dict[str, int] = {}
    rows: list[tuple[str, list[str], str, str]] = []
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8", errors="replace"))):
        if row.get("schema") == "Person":
            continue  # vendors are organisations
        names = [row.get("name") or ""] + (row.get("aliases") or "").split(";")
        normalized = [normalize_name(name) for name in names if name]
        index = len(rows)
        rows.append((row.get("name") or "", normalized, row.get("sanctions") or "", row.get("id") or ""))
        for identifier in (row.get("identifiers") or "").split(";"):
            if identifier:
                by_identifier[identifier.strip()] = index
    return by_identifier, rows


def screen(name: str, uei: str | None = None, recorder: Recorder | None = None) -> list[ExclusionHit]:
    raw = fetch(SOURCE, "GET", URL, ttl_s=LIST_TTL_S, timeout_s=120)
    digest = hashlib.sha256(raw).hexdigest()
    if recorder is not None:
        recorder.add(EvidenceItem(SOURCE, URL, "GET", digest, 0, len(raw), request=f"name={name} uei={uei}"))

    by_identifier, rows = _load(digest, raw)
    if uei and uei in by_identifier:
        original, _, detail, entry_id = rows[by_identifier[uei]]
        return [ExclusionHit(original, "UEI", 100.0, detail[:300], entry_id)]

    target = normalize_name(name)
    if len(target) < 4:
        return []
    hits = []
    for original, normalized, detail, entry_id in rows:
        best = max((fuzz.token_sort_ratio(target, candidate) for candidate in normalized), default=0)
        if best >= MATCH_THRESHOLD:
            hits.append(ExclusionHit(original, "name", best, detail[:300], entry_id))
    return sorted(hits, key=lambda hit: -hit.score)[:5]
