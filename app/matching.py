"""Connects a vendor's claims to public records. The pipeline and the benchmark both go through here.

Contract numbers are not unique. A delivery order such as HHSN26800002 exists under many parent contracts, held by
different companies, so a number on its own proves nothing about ownership. Telling a vendor its contract belongs to
someone else is a serious statement, so it is made only when the number has exactly one other owner.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app import rules
from app.sources import usaspending as usa
from app.sources.http import Recorder, SourceUnavailable
from app.sources.usaspending import ContractRecord

SCAN_LIMIT = 25


@dataclass
class Located:
    record: ContractRecord | None = None
    ambiguous: bool = False


def locate_by_number(number: str, vendor: str, uei: str | None, recorder: Recorder | None = None) -> Located:
    for match in usa.find_by_piid(number, recorder, recipient=uei or vendor):
        record = usa.load_contract(match["generated_internal_id"], recorder)
        if rules.same_entity(vendor, uei, record):
            return Located(record)

    others = usa.find_by_piid(number, recorder)
    if not others:
        return Located()
    owners = {match.get("Recipient UEI") or match.get("Recipient Name") for match in others}
    return Located(usa.load_contract(others[0]["generated_internal_id"], recorder), ambiguous=len(owners) > 1)


def default_history(recipient: str, recorder: Recorder | None = None) -> list[dict]:
    """Default terminations across a vendor's contracts.

    Text search finds terminations anywhere in the vendor's history but depends on how the modification was worded.
    Reading the action codes on the latest contracts does not. Together they miss less than either one alone.
    """
    found: dict[str, dict] = {}
    for row in usa.termination_actions(recipient, recorder):
        found[row["Award ID"]] = {
            "piid": row["Award ID"],
            "award_id": row["generated_internal_id"],
            "date": row.get("Action Date"),
            "agency": row.get("Awarding Agency"),
            "via": "text",
        }

    awards = usa.find_for_recipient(recipient, recorder)[:SCAN_LIMIT]

    def scan(award: dict) -> tuple[dict, list]:
        try:
            return award, usa.load_actions(award["generated_internal_id"], recorder, max_pages=2)
        except SourceUnavailable:
            return award, []

    with ThreadPoolExecutor(6) as pool:
        for award, actions in pool.map(scan, awards):
            for action in actions:
                if action.type in usa.DEFAULT_ACTIONS and award["Award ID"] not in found:
                    found[award["Award ID"]] = {
                        "piid": award["Award ID"],
                        "award_id": award["generated_internal_id"],
                        "date": action.date,
                        "agency": award.get("Awarding Agency"),
                        "via": "action code",
                    }
    return list(found.values())
