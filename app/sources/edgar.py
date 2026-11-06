"""SEC EDGAR full-text search, limited to filings made by the vendor itself.

Full-text search returns any filing that mentions a phrase, so results are kept only when the filer's own name
matches the vendor. Most small vendors file nothing with the SEC, which is reported as "no filings", not as clean.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from rapidfuzz import fuzz

from app.sources.http import Recorder, fetch_json
from app.sources.ofac import normalize_name

SOURCE = "SEC EDGAR"
API = "https://efts.sec.gov/LATEST/search-index"
PHRASES = ["terminated for default", "suspension or debarment", "False Claims Act"]
NAME_THRESHOLD = 92


@dataclass
class FilingHit:
    filer: str
    form: str
    filed: str
    phrase: str
    url: str


def _filing_url(hit_id: str, ciks: list[str]) -> str:
    accession, _, document = hit_id.partition(":")
    folder = accession.replace("-", "")
    cik = (ciks[0] if ciks else "").lstrip("0")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{document}"


def search(name: str, recorder: Recorder | None = None) -> list[FilingHit]:
    target = normalize_name(name)
    found: list[FilingHit] = []
    for phrase in PHRASES:
        query = quote(f'"{name}" "{phrase}"')
        url = f"{API}?q={query}&forms=10-K,10-Q,8-K"
        data = fetch_json(SOURCE, "GET", url, recorder=recorder)
        for hit in (data.get("hits") or {}).get("hits", [])[:20]:
            source = hit.get("_source") or {}
            filers = source.get("display_names") or []
            own = any(fuzz.token_sort_ratio(target, normalize_name(f.split("(")[0])) >= NAME_THRESHOLD for f in filers)
            if own:
                found.append(
                    FilingHit(
                        filer=filers[0].split("(")[0].strip(),
                        form=source.get("form") or "",
                        filed=source.get("file_date") or "",
                        phrase=phrase,
                        url=_filing_url(hit.get("_id") or "", source.get("ciks") or []),
                    )
                )
    return found[:10]
