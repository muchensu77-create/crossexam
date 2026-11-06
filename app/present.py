"""Shapes stored rows for the browser.

Sample-case vendors are built from real public contracts, but the capability statements are invented. No real company
wrote them, so for those vendors the company's name and UEI never reach the page.
"""
from __future__ import annotations

import json
import re


def _loads(value: str | None) -> dict | None:
    return json.loads(value) if value else None


def _scrub(text: str | None, vendor: dict) -> str | None:
    if text is None or not vendor["masked"]:
        return text
    for secret in (vendor["legal_name"], vendor["uei"]):
        if secret:
            text = re.sub(re.escape(secret), vendor["display_name"], text, flags=re.IGNORECASE)
    return text


def _scrub_deep(value, vendor: dict):
    if isinstance(value, str):
        return _scrub(value, vendor)
    if isinstance(value, list):
        return [_scrub_deep(item, vendor) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_deep(item, vendor) for key, item in value.items()}
    return value


def _mask_record(record: dict, vendor: dict) -> dict:
    masked = dict(record)
    masked["recipient_name"] = vendor["display_name"]
    masked["parent_name"] = None
    masked["recipient_uei"] = None
    masked["parent_uei"] = None
    return masked


def vendor_summary(vendor: dict) -> dict:
    result = _loads(vendor["result"]) or {}
    findings = list(result.get("findings", []))
    for claim in result.get("claims", []):
        findings += claim.get("result", {}).get("findings", [])
    blockers = [finding for finding in findings if finding["severity"] == "block"]
    warnings = [finding for finding in findings if finding["severity"] == "warn"]
    claims = result.get("claims", [])
    states = [claim.get("result", {}).get("state", "UNVERIFIABLE") for claim in claims]
    return {
        "id": vendor["id"],
        "name": vendor["display_name"],
        "doc_name": vendor["doc_name"],
        "stage": vendor["stage"],
        "outcome": vendor["outcome"],
        "claims": len(claims),
        "verified": states.count("VERIFIED"),
        "contradicted": states.count("CONTRADICTED"),
        "unverifiable": states.count("UNVERIFIABLE"),
        "claimed_value": result.get("claimed_value", 0),
        "verified_value": result.get("verified_value", 0),
        "blockers": [finding["title"] for finding in blockers],
        "warnings": [finding["title"] for finding in warnings],
        "cost_usd": (result.get("usage") or {}).get("cost_usd", 0),
        "elapsed_s": result.get("elapsed_s"),
        "decision": _loads(vendor["decision"]),
        "letter_status": vendor["letter_status"],
    }


def event(row: dict, vendor: dict) -> dict:
    return {
        "seq": row["seq"],
        "ts": row["ts"],
        "phase": row["phase"],
        "step": row["step"],
        "status": row["status"],
        "title": _scrub(row["title"], vendor),
        "detail": _scrub(row["detail"], vendor),
        "url": row["url"],
    }


def vendor_detail(vendor: dict, case: dict, events: list[dict]) -> dict:
    result = _loads(vendor["result"])
    if result and vendor["masked"]:
        for claim in result.get("claims", []):
            if claim.get("record"):
                claim["record"] = _mask_record(claim["record"], vendor)
        result = _scrub_deep(result, vendor)
        for item in result.get("evidence", []):
            item["request"] = None
    return {
        **vendor_summary(vendor),
        "case": {"id": case["id"], "solicitation": case["solicitation"], "title": case["title"], "is_demo": bool(case["is_demo"])},
        "doc_sha256": vendor["doc_sha256"],
        "result": result,
        "letter": _scrub(vendor["letter"], vendor),
        "events": [event(row, vendor) for row in events],
    }
