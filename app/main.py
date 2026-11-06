"""Crossexam web application."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app import db, memo, pipeline, present
from app.config import ROOT, settings
from app.lookup import public_record_lookup

WEB = ROOT / "web"
UPLOADS = settings.data_dir / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)
RUNS_PER_HOUR_PER_IP = 12

app = FastAPI(title="Crossexam", docs_url="/api/docs", openapi_url="/api/openapi.json")
_active: set[str] = set()
_active_lock = threading.Lock()
_recent_runs: dict[str, list[float]] = {}


@app.on_event("startup")
def startup() -> None:
    db.init()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")


def _throttle(request: Request) -> None:
    ip = _client_ip(request)
    now = time.time()
    recent = [stamp for stamp in _recent_runs.get(ip, []) if now - stamp < 3600]
    if len(recent) >= RUNS_PER_HOUR_PER_IP:
        raise HTTPException(429, "This demo allows 12 live runs per hour. Saved results stay available.")
    recent.append(now)
    _recent_runs[ip] = recent


def _vendor_or_404(vendor_id: str) -> dict:
    vendor = db.row("SELECT * FROM vendors WHERE id = ?", vendor_id)
    if vendor is None:
        raise HTTPException(404, "Vendor not found")
    return vendor


def _case_or_404(case_id: str) -> dict:
    case = db.row("SELECT * FROM cases WHERE id = ?", case_id)
    if case is None:
        raise HTTPException(404, "Case not found")
    return case


def _start(vendor_id: str) -> bool:
    with _active_lock:
        if vendor_id in _active:
            return False
        _active.add(vendor_id)

    def work() -> None:
        try:
            pipeline.run_vendor(vendor_id)
        finally:
            with _active_lock:
                _active.discard(vendor_id)

    threading.Thread(target=work, daemon=True).start()
    return True


# --- cases ------------------------------------------------------------------------------------------------------------


class NewCase(BaseModel):
    solicitation: str = Field(min_length=2, max_length=60)
    title: str = Field(min_length=2, max_length=120)
    buyer: str = Field(min_length=2, max_length=120)


@app.get("/api/cases")
def list_cases() -> list[dict]:
    cases = db.rows("SELECT * FROM cases ORDER BY is_demo DESC, created_at DESC LIMIT 30")
    for case in cases:
        vendors = db.rows("SELECT id, outcome, stage FROM vendors WHERE case_id = ?", case["id"])
        case["vendor_count"] = len(vendors)
        case["holds"] = sum(1 for vendor in vendors if vendor["outcome"] == "HOLD")
    return cases


@app.post("/api/cases")
def create_case(body: NewCase) -> dict:
    return {"id": db.create_case(body.solicitation, body.title, body.buyer)}


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict:
    case = _case_or_404(case_id)
    vendors = db.rows("SELECT * FROM vendors WHERE case_id = ? ORDER BY created_at", case_id)
    case["vendors"] = [present.vendor_summary(vendor) for vendor in vendors]
    return case


@app.post("/api/cases/{case_id}/clone")
def clone_case(case_id: str, request: Request) -> dict:
    """Copies a case and reviews it from scratch, so a visitor can watch a live run without touching the sample."""
    case = _case_or_404(case_id)
    if not pipeline.llm_budget_left():
        raise HTTPException(429, "Today's model budget for this demo is used up. Saved results stay available.")
    _throttle(request)
    copy_id = db.create_case(case["solicitation"], case["title"], case["buyer"])
    vendor_ids = [
        db.create_vendor(
            copy_id,
            vendor["display_name"],
            vendor["legal_name"],
            vendor["uei"],
            vendor["doc_name"],
            vendor["doc_path"],
            vendor["doc_sha256"],
            masked=bool(vendor["masked"]),
        )
        for vendor in db.rows("SELECT * FROM vendors WHERE case_id = ? ORDER BY created_at", case_id)
    ]
    for vendor_id in vendor_ids:
        _start(vendor_id)
    return {"id": copy_id}


@app.post("/api/cases/{case_id}/vendors")
async def add_vendor(
    case_id: str,
    legal_name: str = Form(min_length=2, max_length=160),
    uei: str = Form(default="", max_length=12),
    document: UploadFile = File(),
) -> dict:
    case = _case_or_404(case_id)
    if case["is_demo"]:
        raise HTTPException(403, "The sample case is read-only. Create a new case to upload files.")
    data = await document.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "File is larger than 5 MB.")
    digest = hashlib.sha256(data).hexdigest()
    path = UPLOADS / f"{digest}.bin"
    path.write_bytes(data)
    name = re.sub(r"[^\w .()-]", "_", document.filename or "document")[:80]
    vendor_id = db.create_vendor(
        case_id, legal_name.strip(), legal_name.strip(), uei.strip().upper() or None, name, str(path), digest
    )
    return {"id": vendor_id}


# --- vendors ----------------------------------------------------------------------------------------------------------


@app.post("/api/vendors/{vendor_id}/run")
def run_vendor(vendor_id: str, request: Request) -> dict:
    vendor = _vendor_or_404(vendor_id)
    if _case_or_404(vendor["case_id"])["is_demo"]:
        raise HTTPException(403, "The sample case is read-only. Run a live copy instead.")
    if not pipeline.llm_budget_left():
        raise HTTPException(429, "Today's model budget for this demo is used up. Saved results stay available.")
    _throttle(request)
    return {"started": _start(vendor_id)}


@app.get("/api/vendors/{vendor_id}")
def get_vendor(vendor_id: str) -> dict:
    vendor = _vendor_or_404(vendor_id)
    case = _case_or_404(vendor["case_id"])
    events = db.rows("SELECT * FROM events WHERE vendor_id = ? ORDER BY seq", vendor_id)
    return present.vendor_detail(vendor, case, events)


@app.get("/api/vendors/{vendor_id}/document")
def get_document(vendor_id: str) -> FileResponse:
    vendor = _vendor_or_404(vendor_id)
    if not vendor["doc_path"] or not Path(vendor["doc_path"]).exists():
        raise HTTPException(404, "Document not found")
    return FileResponse(vendor["doc_path"], media_type="application/octet-stream", filename=vendor["doc_name"])


@app.get("/api/vendors/{vendor_id}/events")
async def stream_events(vendor_id: str, request: Request, after: int = 0) -> EventSourceResponse:
    vendor = _vendor_or_404(vendor_id)

    async def generate():
        last = after
        idle = 0
        while not await request.is_disconnected() and idle < 600:
            events = await asyncio.to_thread(
                db.rows, "SELECT * FROM events WHERE vendor_id = ? AND seq > ? ORDER BY seq", vendor_id, last
            )
            for event in events:
                last = event["seq"]
                yield {"event": "step", "data": json.dumps(present.event(event, vendor))}
                if event["step"] == "END":
                    yield {"event": "end", "data": "{}"}
                    return
            idle = 0 if events else idle + 1
            await asyncio.sleep(0.25)

    return EventSourceResponse(generate())


class LetterUpdate(BaseModel):
    text: str = Field(min_length=10, max_length=6000)
    status: str = Field(pattern="^(draft|approved)$")
    reviewer: str = Field(default="", max_length=80)


@app.post("/api/vendors/{vendor_id}/letter")
def update_letter(vendor_id: str, body: LetterUpdate) -> dict:
    vendor = _vendor_or_404(vendor_id)
    if body.status == "approved" and not body.reviewer.strip():
        raise HTTPException(400, "Name the person approving this letter.")
    case = _case_or_404(vendor["case_id"])
    if case["is_demo"]:
        return {"ok": True, "saved": False}
    db.set_vendor(vendor_id, letter=body.text, letter_status=body.status)
    if body.status == "approved":
        db.set_vendor(vendor_id, stage="awaiting_vendor")
    return {"ok": True, "saved": True}


class Decision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reviewer: str = Field(min_length=2, max_length=80)
    note: str = Field(default="", max_length=800)


@app.post("/api/vendors/{vendor_id}/decision")
def decide(vendor_id: str, body: Decision) -> dict:
    vendor = _vendor_or_404(vendor_id)
    case = _case_or_404(vendor["case_id"])
    if not vendor["outcome"]:
        raise HTTPException(409, "Run the review before signing a decision.")
    if vendor["outcome"] == "HOLD" and body.decision == "approve" and len(body.note.strip()) < 20:
        raise HTTPException(400, "Approving a vendor on hold needs a written reason of at least 20 characters.")
    if case["is_demo"]:
        return {"ok": True, "saved": False}
    record = {"decision": body.decision, "reviewer": body.reviewer, "note": body.note, "at": time.time()}
    db.set_vendor(vendor_id, decision=record, stage="decided")
    return {"ok": True, "saved": True}


# --- outputs ----------------------------------------------------------------------------------------------------------


@app.get("/api/cases/{case_id}/memo", response_class=HTMLResponse)
def award_memo(case_id: str) -> HTMLResponse:
    case = _case_or_404(case_id)
    vendors = db.rows("SELECT * FROM vendors WHERE case_id = ? ORDER BY created_at", case_id)
    return HTMLResponse(memo.render(case, vendors))


class Lookup(BaseModel):
    query: str = Field(min_length=3, max_length=120)


@app.post("/api/lookup")
def lookup(body: Lookup, request: Request) -> dict:
    _throttle(request)
    return public_record_lookup(body.query.strip())


@app.get("/api/benchmark")
def benchmark() -> JSONResponse:
    path = ROOT / "bench" / "results.json"
    if not path.exists():
        return JSONResponse({"available": False})
    return JSONResponse({"available": True, **json.loads(path.read_text(encoding="utf-8"))})


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "model_configured": bool(settings.inference_key),
        "sandbox_mode": settings.sandbox_mode,
        "spent_today_usd": round(db.spent_today(), 4),
    }


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
