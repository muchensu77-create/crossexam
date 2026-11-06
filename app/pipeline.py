"""The review protocol for one vendor.

The order is fixed in code. `Protocol.verdict` refuses to run until every reverse step has reported, and a source that
could not be reached counts as a coverage gap, which rules out a clean result. An agent left to itself confirms what it
is told and stops; the reverse pass exists because the contracts that went wrong are the ones a vendor leaves out.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from app import db, llm, rules, sandbox
from app.config import settings
from app.matching import Located, default_history, locate_by_number
from app.rules import BLOCK, INFO, WARN, Finding
from app.sources import edgar, exclusions, ofac, usaspending
from app.sources.http import Recorder, SourceUnavailable
from app.sources.usaspending import ContractRecord

REVERSE_STEPS = ("contract_history", "portfolio_sweep", "exclusions", "sanctions", "filings")

INSTRUCTION_PATTERN = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier|your)\b"
    r"|\b(mark|treat|record|classify|score)\b.{0,40}\b(verified|approved|compliant|clear|passed)\b"
    r"|\bdo not\s+(flag|report|mention|check|verify)\b"
    r"|\bsystem prompt\b|\byou are (now\s+)?an?\b|\bas an ai\b|\bpre-?approved\b",
    re.IGNORECASE,
)


class ProtocolError(Exception):
    pass


class Protocol:
    """Tracks which required steps have reported."""

    def __init__(self) -> None:
        self.reported: set[str] = set()
        self.gaps: list[str] = []

    def done(self, step: str) -> None:
        self.reported.add(step)

    def gap(self, step: str, source: str) -> None:
        self.reported.add(step)
        self.gaps.append(source)

    def verdict(self, claim_states: list[str], findings: list[Finding]) -> str:
        missing = [step for step in REVERSE_STEPS if step not in self.reported]
        if missing:
            raise ProtocolError(f"reverse pass incomplete: {missing}")
        return rules.judge_vendor(claim_states, findings, self.gaps)


class Run:
    def __init__(self, vendor: dict, case: dict) -> None:
        self.vendor = vendor
        self.case = case
        self.run_id = uuid.uuid4().hex[:8]
        self.recorder = Recorder()
        self.usage = llm.Usage()
        self.protocol = Protocol()
        self.findings: list[Finding] = []
        self.claims: list[dict] = []
        self.started = time.time()

    def emit(
        self, phase: str, step: str, status: str, title: str, detail: str | None = None, url: str | None = None
    ) -> None:
        db.add_event(self.vendor["id"], self.run_id, phase, step, status, title, detail, url)

    # --- document -------------------------------------------------------------------------------------------------

    def open_document(self) -> sandbox.ParseResult | None:
        self.emit("intake", "D1", "running", "Opening the document in a disposable sandbox")
        parsed = sandbox.parse_document(Path(self.vendor["doc_path"]))
        if not parsed.ok:
            self.findings.append(Finding("VX-07", BLOCK, "Document could not be opened safely", parsed.failure or ""))
            self.emit("intake", "D1", "block", "Sandbox stopped the document", parsed.failure)
            return None
        self.emit(
            "intake",
            "D1",
            "pass",
            f"Parsed {parsed.pages} page(s) in {parsed.duration_s:.1f}s",
            f"Isolation: {parsed.isolation}. Network none, no secrets, destroyed after this file.",
        )
        return parsed

    def inspect_document(self, parsed: sandbox.ParseResult) -> None:
        hidden_text = " ".join(item["text"] for item in parsed.hidden)
        instruction = INSTRUCTION_PATTERN.search(hidden_text) or INSTRUCTION_PATTERN.search(parsed.text)
        if instruction:
            where = "hidden text" if INSTRUCTION_PATTERN.search(hidden_text) else "the visible text"
            finding = Finding(
                "VX-05",
                BLOCK,
                "Document contains instructions aimed at the reviewer",
                f"Found in {where}: \"{instruction.group(0)}\". The text was kept away from the model, "
                "and no verdict depends on it.",
            )
            self.findings.append(finding)
            self.emit("intake", "D2", "block", finding.title, finding.detail)
        elif parsed.hidden:
            finding = Finding(
                "VX-06", WARN, "Document contains hidden text", f"{len(parsed.hidden)} span(s) a reader would not see."
            )
            self.findings.append(finding)
            self.emit("intake", "D2", "flag", finding.title, finding.detail)
        else:
            self.emit("intake", "D2", "pass", "No hidden text or embedded instructions")

    def extract(self, parsed: sandbox.ParseResult) -> None:
        self.emit("intake", "E1", "running", "Extracting past-performance claims")
        try:
            extracted = llm.extract_claims(parsed.text, self.usage)
        except (llm.ModelUnavailable, ValueError):
            extracted = llm.extract_claims_offline(parsed.text)
        self.claims = extracted["claims"]
        note = f"Read by {extracted['method']}."
        if extracted["dropped"]:
            note += f" {extracted['dropped']} claim(s) discarded because the quote was not in the document."
        self.emit("intake", "E1", "pass", f"Found {len(self.claims)} claim(s)", note)
        if not self.vendor["uei"] and extracted.get("uei"):
            self.vendor["uei"] = extracted["uei"]

    # --- forward --------------------------------------------------------------------------------------------------

    def locate(self, claim: dict) -> Located:
        number = (claim.get("contract_number") or "").replace("-", "").strip()
        if number:
            return locate_by_number(number, self.vendor["legal_name"], self.vendor["uei"], self.recorder)
        return Located(self._locate_without_number(claim))

    def _locate_without_number(self, claim: dict) -> ContractRecord | None:
        recipient = self.vendor["uei"] or self.vendor["legal_name"]
        amount = claim.get("amount_usd") or 0
        best, best_gap = None, None
        for award in usaspending.find_for_recipient(recipient, self.recorder):
            value = float(award.get("Award Amount") or 0)
            gap = abs(value - amount) / max(amount, 1)
            if amount and gap < 0.5 and (best_gap is None or gap < best_gap):
                best, best_gap = award, gap
        return usaspending.load_contract(best["generated_internal_id"], self.recorder) if best else None

    def check_claim(self, index: int, claim: dict) -> None:
        label = f"Claim {index}"
        number = claim.get("contract_number") or "no contract number given"
        self.emit("forward", f"F{index}", "running", f"{label}: looking up {number}")
        result = rules.ClaimResult()
        try:
            located = self.locate(claim)
        except SourceUnavailable as exc:
            self.protocol.gap("contract_history", usaspending.SOURCE)
            self.emit("forward", f"F{index}", "gap", f"{label}: source unavailable", str(exc))
            claim["result"] = result.to_dict()
            return

        record = located.record
        if located.ambiguous:
            finding = Finding(
                "CX-02A",
                WARN,
                "Contract number is shared by several companies",
                f"{claim.get('contract_number')} exists under more than one parent contract, none held by this vendor. "
                "Ask the vendor for the parent contract number.",
            )
            result.findings.append(finding)
            claim["result"] = result.to_dict()
            self.emit("forward", f"F{index}", "flag", f"{label}: {finding.title}", finding.detail)
            return

        if record is None:
            detail = (
                f"No federal contract numbered {claim.get('contract_number')}."
                if claim.get("contract_number")
                else "No matching federal contract. Non-federal work needs a customer reference."
            )
            finding = Finding("CX-01", WARN, "Contract not found in federal records", detail)
            result.findings.append(finding)
            claim["result"] = result.to_dict()
            self.emit("forward", f"F{index}", "flag", f"{label}: {finding.title}", detail)
            return

        forward = rules.check_forward(claim, record, self.vendor["legal_name"], self.vendor["uei"])
        status = _worst(forward)
        title = f"{label}: record matches the claim" if not forward else f"{label}: {forward[0].title}"
        self.emit(
            "forward",
            f"F{index}",
            status,
            title,
            f"{record.piid}, {record.sub_agency or record.agency}, {record.start} to {record.end}, "
            f"peak ${record.peak_obligation:,.0f}",
            record.url,
        )

        self.emit("reverse", f"R{index}", "running", f"{label}: reading all {len(record.actions)} contract modifications")
        reverse = rules.check_reverse(claim, record) if rules.same_entity(
            self.vendor["legal_name"], self.vendor["uei"], record
        ) else []
        status = _worst(reverse)
        title = f"{label}: no adverse actions in the history" if not reverse else f"{label}: {reverse[0].title}"
        self.emit("reverse", f"R{index}", status, title, reverse[0].detail if reverse else None, record.url)

        result.findings = forward + reverse
        result.state = rules.judge_claim(result.findings, located=True)
        claim["result"] = result.to_dict()
        claim["record"] = record.to_dict()

    # --- reverse, vendor level ------------------------------------------------------------------------------------

    def sweep_portfolio(self) -> None:
        recipient = self.vendor["uei"] or self.vendor["legal_name"]
        self.emit("reverse", "S1", "running", "Sweeping every federal contract for default terminations")
        try:
            terminated = default_history(recipient, self.recorder)
        except SourceUnavailable as exc:
            self.protocol.gap("portfolio_sweep", usaspending.SOURCE)
            self.emit("reverse", "S1", "gap", "Portfolio sweep unavailable", str(exc))
            return
        self.protocol.done("portfolio_sweep")

        claimed = {(claim.get("contract_number") or "").replace("-", "") for claim in self.claims}
        undisclosed = [item for item in terminated if item["piid"] not in claimed]
        if undisclosed:
            first = undisclosed[0]
            finding = Finding(
                "VX-01",
                BLOCK,
                "Default termination the vendor did not disclose",
                f"{first['piid']}, {first.get('agency')}, {first.get('date')}: not listed in the statement. "
                f"{len(undisclosed)} such contract(s) on record.",
                usaspending.award_url(first["award_id"]),
            )
            self.findings.append(finding)
            self.emit("reverse", "S1", "block", finding.title, finding.detail, finding.url)
        else:
            self.emit("reverse", "S1", "pass", "No undisclosed default terminations")

    def screen_lists(self) -> None:
        name, uei = self.vendor["legal_name"], self.vendor["uei"]

        self.emit("reverse", "S2", "running", "Checking federal suspensions and debarments")
        try:
            hits = exclusions.screen(name, uei, self.recorder)
            self.protocol.done("exclusions")
            if hits:
                finding = Finding(
                    "VX-02",
                    BLOCK,
                    "Possible match on the federal exclusions list",
                    f"Matched by {hits[0].matched_on}, score {hits[0].score:.0f}. A person must confirm the identity.",
                )
                self.findings.append(finding)
                self.emit("reverse", "S2", "block", finding.title, finding.detail)
            else:
                self.emit("reverse", "S2", "pass", "Not on the federal exclusions list")
        except SourceUnavailable as exc:
            self.protocol.gap("exclusions", exclusions.SOURCE)
            self.emit("reverse", "S2", "gap", "Exclusions list unavailable", str(exc))

        self.emit("reverse", "S3", "running", "Screening the OFAC sanctions list")
        try:
            hits = ofac.screen(name, self.recorder)
            self.protocol.done("sanctions")
            if hits:
                finding = Finding(
                    "VX-03",
                    BLOCK,
                    "Possible match on the OFAC sanctions list",
                    f"Score {hits[0].score:.0f}, program {hits[0].program}. A person must confirm the identity.",
                )
                self.findings.append(finding)
                self.emit("reverse", "S3", "block", finding.title, finding.detail)
            else:
                self.emit("reverse", "S3", "pass", "No OFAC match")
        except SourceUnavailable as exc:
            self.protocol.gap("sanctions", ofac.SOURCE)
            self.emit("reverse", "S3", "gap", "OFAC list unavailable", str(exc))

        self.emit("reverse", "S4", "running", "Searching the vendor's own SEC filings")
        try:
            filings = edgar.search(name, self.recorder)
            self.protocol.done("filings")
            if filings:
                finding = Finding(
                    "VX-04",
                    INFO,
                    "SEC filings mention a procurement risk term",
                    f"{filings[0].form} filed {filings[0].filed} mentions \"{filings[0].phrase}\".",
                    filings[0].url,
                )
                self.findings.append(finding)
                self.emit("reverse", "S4", "info", finding.title, finding.detail, finding.url)
            else:
                self.emit("reverse", "S4", "pass", "No relevant SEC filings by this vendor")
        except SourceUnavailable as exc:
            self.protocol.gap("filings", edgar.SOURCE)
            self.emit("reverse", "S4", "gap", "SEC search unavailable", str(exc))

    # --- outcome --------------------------------------------------------------------------------------------------

    def conclude(self) -> dict:
        claim_findings = [
            Finding(**finding) for claim in self.claims for finding in claim.get("result", {}).get("findings", [])
        ]
        all_findings = self.findings + claim_findings
        states = [claim.get("result", {}).get("state", "UNVERIFIABLE") for claim in self.claims]
        outcome = self.protocol.verdict(states, all_findings)
        self.emit("verdict", "J1", _outcome_status(outcome), f"Rules outcome: {outcome}", _outcome_note(outcome))

        claimed = sum(claim.get("amount_usd") or 0 for claim in self.claims)
        verified = sum(
            claim.get("amount_usd") or 0
            for claim in self.claims
            if claim.get("result", {}).get("state") == "VERIFIED"
        )
        return {
            "outcome": outcome,
            "claims": self.claims,
            "findings": [asdict(finding) for finding in self.findings],
            "coverage_gaps": self.protocol.gaps,
            "claimed_value": claimed,
            "verified_value": verified,
            "evidence": [asdict(item) for item in self.recorder.items],
            "usage": {"cost_usd": self.usage.cost_usd, "calls": self.usage.calls},
            "elapsed_s": round(time.time() - self.started, 1),
            "run_id": self.run_id,
            "finished_at": time.time(),
        }

    def letter(self, result: dict) -> str | None:
        if result["outcome"] == "READY":
            return None
        facts = _letter_facts(result)
        self.emit("verdict", "L1", "running", "Drafting a clarification request for human review")
        name = self.vendor["display_name"]
        try:
            text = llm.draft_letter(name, self.case["solicitation"], facts, self.usage)
        except llm.ModelUnavailable:
            text = llm.letter_offline(name, self.case["solicitation"], facts)
        result["usage"] = {"cost_usd": self.usage.cost_usd, "calls": self.usage.calls}
        self.emit("verdict", "L1", "pass", "Draft ready. Nothing is sent until a person approves it")
        return text


def _worst(findings: list[Finding]) -> str:
    severities = {finding.severity for finding in findings}
    if BLOCK in severities:
        return "block"
    if WARN in severities:
        return "flag"
    return "info" if INFO in severities else "pass"


def _outcome_status(outcome: str) -> str:
    return {"READY": "pass", "CLARIFY": "flag", "HOLD": "block"}[outcome]


def _outcome_note(outcome: str) -> str:
    return {
        "READY": "Every claim verified. A person still signs the decision.",
        "CLARIFY": "Open questions the vendor can answer.",
        "HOLD": "A blocker needs a person's attention before this vendor goes further.",
    }[outcome]


def _letter_facts(result: dict) -> list[str]:
    facts: list[str] = []
    for claim in result["claims"]:
        number = claim.get("contract_number") or "an unnumbered contract"
        for finding in claim.get("result", {}).get("findings", []):
            if finding["severity"] != INFO:
                facts.append(f"{number}: {finding['title']}. {finding['detail']}")
    facts += [f"{item['title']}. {item['detail']}" for item in result["findings"] if item["severity"] != INFO]
    return facts


def run_vendor(vendor_id: str) -> None:
    vendor = db.row("SELECT * FROM vendors WHERE id = ?", vendor_id)
    if vendor is None:
        return
    case = db.row("SELECT * FROM cases WHERE id = ?", vendor["case_id"])
    db.execute("DELETE FROM events WHERE vendor_id = ?", vendor_id)
    db.set_vendor(vendor_id, stage="running", outcome=None, result=None, letter=None, letter_status=None, decision=None)

    run = Run(vendor, case)
    try:
        parsed = run.open_document()
        if parsed is not None:
            run.inspect_document(parsed)
            run.extract(parsed)
            for index, claim in enumerate(run.claims, start=1):
                run.check_claim(index, claim)
        run.protocol.done("contract_history")
        run.sweep_portfolio()
        run.screen_lists()
        result = run.conclude()
        letter = run.letter(result)
        if parsed is not None:
            result["document"] = {"text": parsed.text, "hidden": parsed.hidden, "pages": parsed.pages}
            result["sandbox"] = {
                "isolation": parsed.isolation,
                "limits": parsed.limits,
                "duration_s": round(parsed.duration_s, 2),
            }
        db.add_spend(run.usage.cost_usd)
        db.set_vendor(
            vendor_id,
            stage="review",
            outcome=result["outcome"],
            result=result,
            letter=letter,
            letter_status="draft" if letter else None,
        )
        run.emit("verdict", "END", "done", "Review complete")
    except Exception as exc:  # the run must end visibly, whatever went wrong
        run.emit("verdict", "END", "error", "Review stopped", str(exc)[:300])
        db.set_vendor(vendor_id, stage="error")
        raise


def llm_budget_left() -> bool:
    return db.spent_today() < settings.daily_llm_budget_usd
