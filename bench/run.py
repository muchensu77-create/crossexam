"""Claim forgery benchmark.

Builds past-performance claims from real federal contracts, forges a share of them in known ways, and runs the production
rules with and without the reverse pass. Ground truth is known because the forgeries are ours; the records are real.

Run with: python -m bench.run
"""
from __future__ import annotations

import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app import matching, rules
from app.sources import usaspending as usa
from app.sources.http import SourceUnavailable, fetch_json

SEED = 20261107
PER_TYPE = 20
CLEAN = 50
OUT = Path(__file__).parent / "results.json"

AGENCIES = [
    "Department of Defense",
    "Department of Veterans Affairs",
    "Department of the Interior",
    "Department of Agriculture",
    "Department of Health and Human Services",
    "Department of Energy",
]


def sample_awards(rng: random.Random) -> list[dict]:
    awards: list[dict] = []
    for agency in AGENCIES:
        for page in rng.sample(range(1, 30), 3):
            payload = {
                "filters": {
                    "award_type_codes": usa.CONTRACT_TYPES,
                    "agencies": [{"type": "awarding", "tier": "toptier", "name": agency}],
                    "award_amounts": [{"lower_bound": 100000, "upper_bound": 5000000}],
                    "time_period": [{"start_date": "2022-10-01", "end_date": "2026-06-30"}],
                },
                "fields": ["Award ID", "Recipient Name", "Recipient UEI", "Award Amount", "generated_internal_id"],
                "page": page,
                "limit": 40,
                "sort": "Award Amount",
                "order": "desc",
            }
            data = fetch_json(usa.SOURCE, "POST", f"{usa.API}/search/spending_by_award/", payload=payload)
            awards += data.get("results", [])
    seen, unique = set(), []
    for award in awards:
        if award.get("Recipient UEI") and award["Recipient UEI"] not in seen:
            seen.add(award["Recipient UEI"])
            unique.append(award)
    rng.shuffle(unique)
    return unique


BROAD_KEYWORDS = ("default", "terminate", "termination", "terminated", "T4D", "cancel")


def default_terminations(rng: random.Random, pages: int = 6) -> list[dict]:
    """Sampled with wider search words than the product's own sweep uses, so the sweep is not graded on its own query."""
    rows: list[dict] = []
    for keyword in BROAD_KEYWORDS:
        for page in range(1, pages + 1):
            payload = {
                "filters": {
                    "award_type_codes": usa.CONTRACT_TYPES,
                    "keywords": [keyword],
                    "time_period": [{"start_date": "2021-10-01", "end_date": date.today().isoformat()}],
                },
                "fields": ["Award ID", "Recipient Name", "Recipient UEI", "Action Date", "Action Type", "Transaction Amount"],
                "page": page,
                "limit": 100,
                "sort": "Action Date",
                "order": "desc",
            }
            data = fetch_json(usa.SOURCE, "POST", f"{usa.API}/search/spending_by_transaction/", payload=payload)
            rows += [row for row in data.get("results", []) if row.get("Action Type") == "E"]
    seen, unique = set(), []
    for row in rows:
        if row["generated_internal_id"] not in seen and row.get("Recipient UEI"):
            seen.add(row["generated_internal_id"])
            unique.append(row)
    rng.shuffle(unique)
    return unique


def honest_claim(record: usa.ContractRecord) -> dict:
    return {
        "contract_number": record.piid,
        "amount_usd": record.total_obligation,
        "end": (record.end or "")[:7],
        "customer": record.sub_agency or record.agency,
        "status_claimed": "completed",
    }


@dataclass
class Case:
    kind: str
    claim: dict
    vendor: str
    uei: str | None


def evaluate(case: Case) -> dict:
    located = matching.locate_by_number(case.claim["contract_number"], case.vendor, case.uei)
    forward: list[rules.Finding] = []
    reverse: list[rules.Finding] = []
    if located.ambiguous:
        forward.append(rules.Finding("CX-02A", rules.WARN, "number shared", ""))
    elif located.record is None:
        forward.append(rules.Finding("CX-01", rules.WARN, "not found", ""))
    else:
        forward = rules.check_forward(case.claim, located.record, case.vendor, case.uei)
        if rules.same_entity(case.vendor, case.uei, located.record):
            reverse = rules.check_reverse(case.claim, located.record)

    history = matching.default_history(case.uei or case.vendor)
    if any(item["piid"] != case.claim["contract_number"] for item in history):
        reverse.append(rules.Finding("VX-01", rules.BLOCK, "undisclosed default", ""))

    def flagged(findings: list[rules.Finding]) -> bool:
        return any(finding.severity in (rules.BLOCK, rules.WARN) for finding in findings)

    return {
        "kind": case.kind,
        "forward": flagged(forward),
        "full": flagged(forward + reverse),
        "rules": sorted({finding.rule for finding in forward + reverse if finding.severity != rules.INFO}),
    }


def load(award_id: str) -> usa.ContractRecord | None:
    try:
        return usa.load_contract(award_id)
    except SourceUnavailable:
        return None


def clean_record(award: dict) -> usa.ContractRecord | None:
    record = load(award["generated_internal_id"])
    if record is None or not record.end or record.total_obligation <= 0:
        return None
    return record


def build_cases(rng: random.Random) -> list[Case]:
    awards = sample_awards(rng)
    with ThreadPoolExecutor(6) as pool:
        records = [record for record in pool.map(clean_record, awards[: CLEAN + 6 * PER_TYPE]) if record]
    print(f"{len(records)} real contracts loaded")

    cases: list[Case] = []
    take = iter(records)

    def batch(count: int) -> list[usa.ContractRecord]:
        return [next(take) for _ in range(count)]

    for record in batch(CLEAN):
        cases.append(Case("clean", honest_claim(record), record.recipient_name, record.recipient_uei))

    for record in batch(PER_TYPE):
        claim = honest_claim(record)
        peak = max(record.total_obligation, record.ceiling, record.peak_obligation)
        claim["amount_usd"] = round(peak * rng.uniform(1.5, 3.0))
        cases.append(Case("inflated_value", claim, record.recipient_name, record.recipient_uei))

    donors = batch(PER_TYPE)
    for record, donor in zip(batch(PER_TYPE), donors):
        claim = honest_claim(donor)
        cases.append(Case("someone_elses_contract", claim, record.recipient_name, record.recipient_uei))

    for record in batch(PER_TYPE):
        claim = honest_claim(record)
        claim["contract_number"] = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(13))
        cases.append(Case("invented_contract", claim, record.recipient_name, record.recipient_uei))

    for record in batch(PER_TYPE):
        claim = honest_claim(record)
        year, month = record.end[:7].split("-")
        claim["end"] = f"{int(year) + rng.choice((1, 2))}-{month}"
        cases.append(Case("stretched_dates", claim, record.recipient_name, record.recipient_uei))

    terminations = default_terminations(rng)
    print(f"{len(terminations)} real default terminations found")
    terminated_records: list[usa.ContractRecord] = []
    for row in terminations[: PER_TYPE * 3]:
        record = load(row["generated_internal_id"])
        if record and any(action.type == "E" for action in record.actions) and len(terminated_records) < PER_TYPE:
            terminated_records.append(record)
    for record in terminated_records:
        claim = honest_claim(record)
        claim["amount_usd"] = record.peak_obligation or record.total_obligation
        cases.append(Case("terminated_claimed_as_completed", claim, record.recipient_name, record.recipient_uei))

    omitted = 0
    for row in terminations[PER_TYPE * 3 :]:
        if omitted >= PER_TYPE:
            break
        others = [
            award
            for award in usa.find_for_recipient(row["Recipient UEI"], limit=25)
            if award.get("Award ID") != row["Award ID"] and float(award.get("Award Amount") or 0) > 25000
        ]
        for award in others[:4]:
            record = load(award["generated_internal_id"])
            if record and record.end and not any(action.type in ("E", "X") for action in record.actions):
                shrunk = record.peak_obligation and record.total_obligation / record.peak_obligation < 0.75
                if not shrunk:
                    claim = honest_claim(record)
                    cases.append(Case("default_left_out", claim, record.recipient_name, record.recipient_uei))
                    omitted += 1
                    break
    return cases


LABELS = {
    "inflated_value": "Value inflated 1.5x to 3x",
    "someone_elses_contract": "Another company's contract",
    "invented_contract": "Contract number invented",
    "stretched_dates": "End date stretched 1 to 2 years",
    "terminated_claimed_as_completed": "Terminated for default, claimed as completed",
    "default_left_out": "Clean claim, default on another contract left out",
}


def sweep_recall(rng: random.Random, sample: int = 50) -> dict:
    """How many real default terminations does the sweep find, by text search alone and with action codes added?"""
    rows = default_terminations(rng)[:sample]

    def check(row: dict) -> tuple[bool, bool]:
        uei = row["Recipient UEI"]
        text = any(hit.get("Award ID") == row["Award ID"] for hit in usa.termination_actions(uei))
        full = any(item["piid"] == row["Award ID"] for item in matching.default_history(uei))
        return text, full

    with ThreadPoolExecutor(4) as pool:
        checks = list(pool.map(check, rows))
    return {"sample": len(checks), "text_only": sum(c[0] for c in checks), "with_action_codes": sum(c[1] for c in checks)}


def main() -> int:
    rng = random.Random(SEED)
    started = time.time()
    cases = build_cases(rng)
    print(f"{len(cases)} cases built in {time.time() - started:.0f}s")

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(evaluate, cases))

    rows = []
    for kind, label in LABELS.items():
        group = [result for result in results if result["kind"] == kind]
        rows.append(
            {
                "kind": kind,
                "label": label,
                "cases": len(group),
                "forward": f"{sum(r['forward'] for r in group)} of {len(group)}",
                "full": f"{sum(r['full'] for r in group)} of {len(group)}",
            }
        )

    recall = sweep_recall(random.Random(SEED + 1))
    print("sweep recall", recall)

    forged = [result for result in results if result["kind"] != "clean"]
    clean = [result for result in results if result["kind"] == "clean"]
    forward_caught = sum(result["forward"] for result in forged)
    full_caught = sum(result["full"] for result in forged)
    clean_flags = [result for result in clean if result["full"]]
    reasons: dict[str, int] = {}
    for result in clean_flags:
        for rule in result["rules"]:
            reasons[rule] = reasons.get(rule, 0) + 1

    late = [r for r in forged if r["kind"] in ("terminated_claimed_as_completed", "default_left_out")]
    summary = {
        "generated": date.today().isoformat(),
        "headline": "A forward check cannot see a contract that went bad after award.",
        "method": (
            f"{len(forged)} forged claims and {len(clean)} honest claims built from real federal contracts on USAspending.gov, "
            "run through the production code. The records are real and the forgeries are ours, so the right answer is known."
        ),
        "stats": [
            {"value": f"{sum(r['forward'] for r in late)} of {len(late)}", "label": "post-award problems caught by forward checks"},
            {"value": f"{sum(r['full'] for r in late)} of {len(late)}", "label": "caught once the reverse pass runs"},
            {"value": f"{len(clean_flags)} of {len(clean)}", "label": "honest claims sent to a person for review"},
            {
                "value": f"{recall['with_action_codes']} of {recall['sample']}",
                "label": "real default terminations found by the portfolio sweep",
            },
        ],
        "rows": rows,
        "note": (
            "How to read this: we wrote these forgeries knowing the rules, so the rows show that each check works end to end on "
            "real records. They are not a field accuracy rate. The sweep figure is the harder test: real terminations sampled "
            f"with wider search words than the product uses. Text search alone found {recall['text_only']} of {recall['sample']}; "
            f"action codes raised that to {recall['with_action_codes']}. A termination worded with none of those search words would not be in the sample, and the action-code scan reads only a vendor's latest 25 contracts. Honest claims flagged, by rule: {reasons or 'none'}."
        ),
        "recall": recall,
        "forward_caught": forward_caught,
        "full_caught": full_caught,
        "forged": len(forged),
        "seed": SEED,
        "elapsed_s": round(time.time() - started),
    }
    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("stats", "rows", "note", "recall", "forward_caught", "full_caught", "forged", "elapsed_s")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
