"""OFAC Specially Designated Nationals list, screened by fuzzy name match.

A hit here is a lead for a human, because names collide. The rules treat it as a hold, never as a finding of fact.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from functools import lru_cache

from rapidfuzz import fuzz

from app.sources.http import EvidenceItem, Recorder, fetch

URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"
SOURCE = "OFAC SDN list"
MATCH_THRESHOLD = 92
LIST_TTL_S = 7 * 24 * 3600

_SUFFIXES = re.compile(
    r"\b(llc|l\.l\.c|inc|incorporated|corp|corporation|co|company|ltd|limited|lp|llp|plc|gmbh|sa|the)\b\.?",
    re.IGNORECASE,
)


def normalize_name(name: str) -> str:
    cleaned = _SUFFIXES.sub(" ", name.lower())
    cleaned = re.sub(r"[^a-z0-9 ]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


@dataclass
class SanctionsHit:
    name: str
    program: str
    score: float
    entry_id: str


@lru_cache(maxsize=1)
def _load(raw_hash: str, raw: bytes) -> list[tuple[str, str, str, str]]:
    rows = []
    for row in csv.reader(io.StringIO(raw.decode("utf-8", errors="replace"))):
        if len(row) < 4:
            continue
        entry_id, name, kind, program = row[0], row[1], row[2].strip(), row[3]
        if kind in ("individual", "vessel", "aircraft"):
            continue  # vendors are organisations
        rows.append((entry_id, name, program, normalize_name(name)))
    return rows


def screen(name: str, recorder: Recorder | None = None) -> list[SanctionsHit]:
    raw = fetch(SOURCE, "GET", URL, ttl_s=LIST_TTL_S, timeout_s=60)
    digest = hashlib.sha256(raw).hexdigest()
    if recorder is not None:
        recorder.add(EvidenceItem(SOURCE, URL, "GET", digest, 0, len(raw), request=f"name={name}"))

    target = normalize_name(name)
    if len(target) < 4:
        return []
    hits = []
    for entry_id, original, program, normalized in _load(digest, raw):
        score = fuzz.token_sort_ratio(target, normalized)
        if score >= MATCH_THRESHOLD:
            hits.append(SanctionsHit(original, program, score, entry_id))
    return sorted(hits, key=lambda hit: -hit.score)[:5]
