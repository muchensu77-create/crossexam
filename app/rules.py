"""The deterministic rule set. Verdicts are decided here and nowhere else.

The language model reads documents and writes drafts. It has no path to these functions' outcomes: a claim is
judged only on public records, so text inside a vendor's document cannot talk its way to a pass.

Claim states
    VERIFIED      the record supports every checked field
    CONTRADICTED  the record disagrees with the claim
    UNVERIFIABLE  no record was found, which is the default until evidence says otherwise

Vendor outcomes
    READY    every claim verified, no flags, all sources answered; still needs a human signature
    CLARIFY  open questions that a vendor can answer
    HOLD     at least one blocker; a human has to look before this vendor goes further
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from rapidfuzz import fuzz

from app.sources.ofac import normalize_name
from app.sources.usaspending import CONVENIENCE_ACTIONS, DEFAULT_ACTIONS, ContractRecord

VALUE_TOLERANCE = 1.10
DATE_TOLERANCE_DAYS = 92
SHRINKAGE_THRESHOLD = 0.25
IDENTITY_THRESHOLD = 90
AGENCY_THRESHOLD = 80

BLOCK = "block"
WARN = "warn"
INFO = "info"

AGENCY_ALIASES = {
    "ihs": "indian health service",
    "va": "department of veterans affairs",
    "dod": "department of defense",
    "army": "department of the army",
    "navy": "department of the navy",
    "air force": "department of the air force",
    "usda": "department of agriculture",
    "doe": "department of energy",
    "doi": "department of the interior",
    "hhs": "department of health and human services",
    "dhs": "department of homeland security",
    "gsa": "general services administration",
    "nasa": "national aeronautics and space administration",
}


@dataclass
class Finding:
    rule: str
    severity: str
    title: str
    detail: str
    url: str | None = None


@dataclass
class ClaimResult:
    state: str = "UNVERIFIABLE"
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_date(text: str | None, end_of_period: bool = False) -> date | None:
    if not text:
        return None
    parts = text.split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else (12 if end_of_period else 1)
        day = int(parts[2]) if len(parts) > 2 else (28 if end_of_period else 1)
        return date(year, month, day)
    except (ValueError, IndexError):
        return None


def same_entity(vendor_name: str, vendor_uei: str | None, record: ContractRecord) -> bool:
    if vendor_uei and vendor_uei in (record.recipient_uei, record.parent_uei):
        return True
    target = normalize_name(vendor_name)
    names = [record.recipient_name, record.parent_name or ""]
    return any(fuzz.token_sort_ratio(target, normalize_name(name)) >= IDENTITY_THRESHOLD for name in names if name)


def same_agency(claimed: str | None, record: ContractRecord) -> bool:
    if not claimed:
        return True
    text = claimed.lower().strip()
    text = AGENCY_ALIASES.get(text, text)
    candidates = [(record.agency or "").lower(), (record.sub_agency or "").lower()]
    return any(fuzz.token_set_ratio(text, candidate) >= AGENCY_THRESHOLD for candidate in candidates if candidate)


def check_forward(claim: dict, record: ContractRecord, vendor_name: str, vendor_uei: str | None) -> list[Finding]:
    """Does the record match what the vendor said? Everything here can pass while the contract still went badly."""
    findings: list[Finding] = []

    if not same_entity(vendor_name, vendor_uei, record):
        findings.append(
            Finding(
                "CX-02",
                BLOCK,
                "Contract belongs to another company",
                f"The public record lists a different recipient for {record.piid}.",
                record.url,
            )
        )

    claimed_value = claim.get("amount_usd")
    record_value = max(record.total_obligation, record.ceiling, record.peak_obligation)
    if claimed_value and record_value > 0 and claimed_value > record_value * VALUE_TOLERANCE:
        ratio = claimed_value / record_value
        findings.append(
            Finding(
                "CX-03",
                WARN,
                "Claimed value exceeds the record",
                f"Claimed ${claimed_value:,.0f}; the highest value on record is ${record_value:,.0f} ({ratio:.1f}x).",
                record.url,
            )
        )

    claimed_end = _parse_date(claim.get("end"), end_of_period=True)
    record_end = _parse_date(record.end, end_of_period=True)
    if claimed_end and record_end and (claimed_end - record_end).days > DATE_TOLERANCE_DAYS:
        findings.append(
            Finding(
                "CX-04",
                WARN,
                "Claimed end date is later than the record",
                f"Claimed {claim.get('end')}; the record ends {record.end}.",
                record.url,
            )
        )

    if not same_agency(claim.get("customer"), record):
        findings.append(
            Finding(
                "CX-05",
                WARN,
                "Customer does not match",
                f"Claimed {claim.get('customer')}; the record shows {record.sub_agency or record.agency}.",
                record.url,
            )
        )
    return findings


def check_reverse(claim: dict, record: ContractRecord) -> list[Finding]:
    """What happened to the contract after award? This is the pass a vendor's own statement never volunteers."""
    findings: list[Finding] = []

    for action in record.actions:
        if action.type in DEFAULT_ACTIONS:
            completed = claim.get("status_claimed") == "completed"
            findings.append(
                Finding(
                    "CX-07",
                    BLOCK,
                    "Contract was terminated for default" + (" but is claimed as completed" if completed else ""),
                    f"Modification {action.mod} on {action.date}: {action.type_label}.",
                    record.url,
                )
            )
        elif action.type in CONVENIENCE_ACTIONS:
            findings.append(
                Finding(
                    "CX-07C",
                    INFO,
                    "Contract was terminated for convenience",
                    f"Modification {action.mod} on {action.date}. This is the buyer's choice and no fault of the vendor.",
                    record.url,
                )
            )

    if record.peak_obligation > 0:
        shrinkage = 1 - record.total_obligation / record.peak_obligation
        if shrinkage >= SHRINKAGE_THRESHOLD:
            findings.append(
                Finding(
                    "CX-08",
                    WARN,
                    "Funding was pulled back after award",
                    f"Obligations peaked at ${record.peak_obligation:,.0f} and now stand at "
                    f"${record.total_obligation:,.0f} ({shrinkage:.0%} lower).",
                    record.url,
                )
            )
    return findings


def judge_claim(findings: list[Finding], located: bool) -> str:
    if not located:
        return "UNVERIFIABLE"
    if any(f.severity in (BLOCK, WARN) for f in findings):
        return "CONTRADICTED"
    return "VERIFIED"


def judge_vendor(claim_states: list[str], findings: list[Finding], coverage_gaps: list[str]) -> str:
    if any(f.severity == BLOCK for f in findings):
        return "HOLD"
    if not claim_states:
        return "CLARIFY"
    if coverage_gaps or any(f.severity == WARN for f in findings):
        return "CLARIFY"
    if any(state != "VERIFIED" for state in claim_states):
        return "CLARIFY"
    return "READY"
