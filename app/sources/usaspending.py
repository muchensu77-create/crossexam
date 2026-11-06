"""USAspending.gov client: federal contract awards and every modification made to them.

No API key is needed. Contract modifications carry an FPDS action type, which is how a termination shows up:
E = terminate for default, F = terminate for convenience, X = terminate for cause.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from app.sources.http import Recorder, fetch_json

API = "https://api.usaspending.gov/api/v2"
SOURCE = "USAspending.gov"
CONTRACT_TYPES = ["A", "B", "C", "D"]
EARLIEST = "2007-10-01"
TERMINATION_KEYWORDS = [
    "terminate for default",
    "termination for default",
    "terminated for default",
    "terminate for cause",
    "termination for cause",
    "default termination",
]
DEFAULT_ACTIONS = {"E", "X"}
CONVENIENCE_ACTIONS = {"F"}
ACTION_LABELS = {"E": "terminate for default", "F": "terminate for convenience", "X": "terminate for cause"}


@dataclass
class ContractAction:
    date: str
    mod: str
    type: str | None
    type_label: str | None
    amount: float
    description: str


@dataclass
class ContractRecord:
    award_id: str
    piid: str
    recipient_name: str
    recipient_uei: str | None
    parent_name: str | None
    parent_uei: str | None
    agency: str | None
    sub_agency: str | None
    description: str
    start: str | None
    end: str | None
    total_obligation: float
    ceiling: float
    peak_obligation: float
    actions: list[ContractAction] = field(default_factory=list)

    @property
    def url(self) -> str:
        return award_url(self.award_id)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["url"] = self.url
        return data


def award_url(award_id: str) -> str:
    return f"https://www.usaspending.gov/award/{award_id}"


def _today() -> str:
    return date.today().isoformat()


def _search_awards(filters: dict, recorder: Recorder | None, limit: int, sort: str) -> list[dict]:
    payload = {
        "filters": {
            "award_type_codes": CONTRACT_TYPES,
            "time_period": [{"start_date": EARLIEST, "end_date": _today()}],
            **filters,
        },
        "fields": [
            "Award ID",
            "Recipient Name",
            "Recipient UEI",
            "Award Amount",
            "Start Date",
            "End Date",
            "Awarding Agency",
            "Awarding Sub Agency",
            "Description",
            "generated_internal_id",
        ],
        "page": 1,
        "limit": limit,
        "sort": sort,
        "order": "desc",
    }
    data = fetch_json(SOURCE, "POST", f"{API}/search/spending_by_award/", payload=payload, recorder=recorder)
    return data.get("results", [])


def find_by_piid(piid: str, recorder: Recorder | None = None, recipient: str | None = None) -> list[dict]:
    filters: dict = {"award_ids": [piid]}
    if recipient:
        filters["recipient_search_text"] = [recipient]
    return _search_awards(filters, recorder, limit=10, sort="Award Amount")


def find_for_recipient(recipient: str, recorder: Recorder | None = None, limit: int = 100) -> list[dict]:
    """`recipient` may be a UEI or a legal name."""
    return _search_awards({"recipient_search_text": [recipient]}, recorder, limit=limit, sort="Start Date")


def load_contract(award_id: str, recorder: Recorder | None = None) -> ContractRecord:
    detail = fetch_json(SOURCE, "GET", f"{API}/awards/{award_id}/", recorder=recorder)
    actions = load_actions(award_id, recorder)

    running = 0.0
    peak = 0.0
    for action in actions:
        running += action.amount
        peak = max(peak, running)

    recipient = detail.get("recipient") or {}
    agency = detail.get("awarding_agency") or {}
    period = detail.get("period_of_performance") or {}
    return ContractRecord(
        award_id=award_id,
        piid=detail.get("piid") or "",
        recipient_name=recipient.get("recipient_name") or "",
        recipient_uei=recipient.get("recipient_uei"),
        parent_name=recipient.get("parent_recipient_name"),
        parent_uei=recipient.get("parent_recipient_uei"),
        agency=(agency.get("toptier_agency") or {}).get("name"),
        sub_agency=(agency.get("subtier_agency") or {}).get("name"),
        description=detail.get("description") or "",
        start=(period.get("start_date") or "")[:10] or None,
        end=(period.get("end_date") or "")[:10] or None,
        total_obligation=float(detail.get("total_obligation") or 0),
        ceiling=float(detail.get("base_and_all_options") or 0),
        peak_obligation=peak,
        actions=actions,
    )


def load_actions(award_id: str, recorder: Recorder | None = None, max_pages: int = 5) -> list[ContractAction]:
    actions: list[ContractAction] = []
    for page in range(1, max_pages + 1):
        payload = {"award_id": award_id, "limit": 100, "page": page, "sort": "action_date", "order": "asc"}
        data = fetch_json(SOURCE, "POST", f"{API}/transactions/", payload=payload, recorder=recorder)
        for row in data.get("results", []):
            actions.append(
                ContractAction(
                    date=(row.get("action_date") or "")[:10],
                    mod=str(row.get("modification_number") or ""),
                    type=row.get("action_type"),
                    type_label=row.get("action_type_description"),
                    amount=float(row.get("federal_action_obligation") or 0),
                    description=row.get("description") or "",
                )
            )
        if not (data.get("page_metadata") or {}).get("hasNext"):
            break
    return actions


def termination_actions(recipient: str, recorder: Recorder | None = None, limit: int = 50) -> list[dict]:
    """Modifications across all of a recipient's contracts whose text or action type marks a termination."""
    payload = {
        "filters": {
            "award_type_codes": CONTRACT_TYPES,
            "keywords": TERMINATION_KEYWORDS,
            "recipient_search_text": [recipient],
            "time_period": [{"start_date": EARLIEST, "end_date": _today()}],
        },
        "fields": [
            "Award ID",
            "Recipient Name",
            "Action Date",
            "Action Type",
            "Mod",
            "Transaction Amount",
            "Awarding Agency",
            "Transaction Description",
        ],
        "page": 1,
        "limit": limit,
        "sort": "Action Date",
        "order": "desc",
    }
    data = fetch_json(SOURCE, "POST", f"{API}/search/spending_by_transaction/", payload=payload, recorder=recorder)
    return [row for row in data.get("results", []) if row.get("Action Type") in DEFAULT_ACTIONS]
