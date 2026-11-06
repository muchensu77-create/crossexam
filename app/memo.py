"""Printable source-selection memo with its evidence ledger."""
from __future__ import annotations

import html
import time

from app import present


def _money(value: float | None) -> str:
    return f"${value:,.0f}" if value else "$0"


def render(case: dict, vendors: list[dict]) -> str:
    summaries = [present.vendor_summary(vendor) for vendor in vendors]
    rows = "".join(
        f"<tr><td>{html.escape(item['name'])}</td><td>{item['outcome'] or 'Not reviewed'}</td>"
        f"<td>{item['verified']} of {item['claims']}</td>"
        f"<td>{_money(item['verified_value'])} of {_money(item['claimed_value'])}</td>"
        f"<td>{html.escape('; '.join(item['blockers'] + item['warnings']) or 'None')}</td>"
        f"<td>{_decision_text(item['decision'])}</td></tr>"
        for item in summaries
    )
    ledger = ""
    for vendor in vendors:
        detail = present.vendor_detail(vendor, case, [])
        for item in (detail["result"] or {}).get("evidence", []):
            ledger += (
                f"<tr><td>{html.escape(detail['name'])}</td><td>{html.escape(item['source'])}</td>"
                f"<td><code>{item['sha256'][:24]}</code></td></tr>"
            )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Memo {html.escape(case['solicitation'])}</title>
<style>body{{font:15px/1.5 Georgia,serif;max-width:860px;margin:40px auto;padding:0 16px;color:#1b1b1b}}
table{{border-collapse:collapse;width:100%;margin:14px 0;font-size:13px}}td,th{{border:1px solid #bbb;padding:6px 8px;text-align:left;vertical-align:top}}
th{{background:#f1eee6}}code{{font-size:12px}}h1{{font-size:24px;margin-bottom:0}}.sub{{color:#555}}</style></head><body>
<h1>Past-performance review memo</h1>
<p class="sub">{html.escape(case['solicitation'])}: {html.escape(case['title'])}<br>{html.escape(case['buyer'])}<br>
Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}</p>
<p>Outcomes come from fixed rules applied to public records. The decision column records what a named person signed.
A blank decision means no one has signed yet.</p>
<table><tr><th>Vendor</th><th>Rules outcome</th><th>Claims verified</th><th>Value verified</th><th>Open items</th><th>Decision</th></tr>{rows}</table>
<h2>Evidence ledger</h2><p>SHA-256 of each public record response that the findings rely on.</p>
<table><tr><th>Vendor</th><th>Source</th><th>SHA-256 (first 24)</th></tr>{ledger}</table></body></html>"""


def _decision_text(decision: dict | None) -> str:
    if not decision:
        return "Not signed"
    return f"{decision['decision']} by {html.escape(decision['reviewer'])}"
