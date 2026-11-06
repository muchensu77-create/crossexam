"""Public record lookup for any contract number or company name.

This shows what the public record says and draws no conclusion about anyone.
"""
from __future__ import annotations

import re

from app.sources import exclusions, ofac, usaspending
from app.sources.http import SourceUnavailable

PIID_SHAPE = re.compile(r"^[A-Z0-9-]{10,24}$")


def public_record_lookup(query: str) -> dict:
    upper = query.upper()
    if PIID_SHAPE.match(upper) and any(ch.isdigit() for ch in upper):
        return _contract(upper.replace("-", ""))
    return _company(query)


def _contract(piid: str) -> dict:
    try:
        matches = usaspending.find_by_piid(piid)
        if not matches:
            return {"kind": "contract", "found": False, "query": piid}
        record = usaspending.load_contract(matches[0]["generated_internal_id"])
    except SourceUnavailable as exc:
        return {"kind": "contract", "found": False, "query": piid, "error": str(exc)}
    return {"kind": "contract", "found": True, "query": piid, "record": record.to_dict()}


def _company(name: str) -> dict:
    result: dict = {"kind": "company", "query": name, "awards": [], "terminations": [], "gaps": []}
    try:
        awards = usaspending.find_for_recipient(name, limit=25)
        result["awards"] = [
            {
                "piid": award.get("Award ID"),
                "recipient": award.get("Recipient Name"),
                "amount": award.get("Award Amount"),
                "agency": award.get("Awarding Agency"),
                "start": award.get("Start Date"),
                "end": award.get("End Date"),
                "url": usaspending.award_url(award["generated_internal_id"]),
            }
            for award in awards
        ]
        result["terminations"] = [
            {
                "piid": row.get("Award ID"),
                "date": row.get("Action Date"),
                "agency": row.get("Awarding Agency"),
                "label": usaspending.ACTION_LABELS.get(row.get("Action Type"), "termination"),
                "url": usaspending.award_url(row["generated_internal_id"]),
            }
            for row in usaspending.termination_actions(name)
        ]
    except SourceUnavailable:
        result["gaps"].append(usaspending.SOURCE)
    try:
        result["exclusions"] = [hit.__dict__ for hit in exclusions.screen(name)]
    except SourceUnavailable:
        result["gaps"].append(exclusions.SOURCE)
    try:
        result["sanctions"] = [hit.__dict__ for hit in ofac.screen(name)]
    except SourceUnavailable:
        result["gaps"].append(ofac.SOURCE)
    return result
