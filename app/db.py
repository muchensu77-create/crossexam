"""SQLite storage. Case data is small and written by one process, so one file on the VM is the right size."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from app.config import settings

DB_PATH = settings.data_dir / "crossexam.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    solicitation TEXT NOT NULL,
    title TEXT NOT NULL,
    buyer TEXT NOT NULL,
    is_demo INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS vendors (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(id),
    display_name TEXT NOT NULL,
    legal_name TEXT NOT NULL,
    uei TEXT,
    masked INTEGER NOT NULL DEFAULT 0,
    doc_name TEXT,
    doc_path TEXT,
    doc_sha256 TEXT,
    stage TEXT NOT NULL DEFAULT 'new',
    outcome TEXT,
    result TEXT,
    letter TEXT,
    letter_status TEXT,
    decision TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id TEXT NOT NULL REFERENCES vendors(id),
    run_id TEXT NOT NULL,
    ts REAL NOT NULL,
    phase TEXT NOT NULL,
    step TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT,
    url TEXT
);
CREATE INDEX IF NOT EXISTS events_vendor ON events(vendor_id, seq);
CREATE TABLE IF NOT EXISTS spend (
    day TEXT PRIMARY KEY,
    usd REAL NOT NULL
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init() -> None:
    with connect() as db:
        db.executescript(SCHEMA)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def row(query: str, *args: Any) -> dict | None:
    with connect() as db:
        found = db.execute(query, args).fetchone()
    return dict(found) if found else None


def rows(query: str, *args: Any) -> list[dict]:
    with connect() as db:
        return [dict(found) for found in db.execute(query, args).fetchall()]


def execute(query: str, *args: Any) -> None:
    with connect() as db:
        db.execute(query, args)


def create_case(solicitation: str, title: str, buyer: str, is_demo: bool = False, case_id: str | None = None) -> str:
    case_id = case_id or new_id()
    execute(
        "INSERT INTO cases (id, solicitation, title, buyer, is_demo, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        case_id, solicitation, title, buyer, int(is_demo), time.time(),
    )  # fmt: skip
    return case_id


def create_vendor(
    case_id: str,
    display_name: str,
    legal_name: str,
    uei: str | None,
    doc_name: str,
    doc_path: str,
    doc_sha256: str,
    masked: bool = False,
    vendor_id: str | None = None,
) -> str:
    vendor_id = vendor_id or new_id()
    execute(
        """INSERT INTO vendors (id, case_id, display_name, legal_name, uei, masked, doc_name, doc_path, doc_sha256, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        vendor_id, case_id, display_name, legal_name, uei, int(masked), doc_name, doc_path, doc_sha256, time.time(),
    )  # fmt: skip
    return vendor_id


def set_vendor(vendor_id: str, **fields: Any) -> None:
    encoded = {key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in fields.items()}
    assignments = ", ".join(f"{key} = ?" for key in encoded)
    execute(f"UPDATE vendors SET {assignments} WHERE id = ?", *encoded.values(), vendor_id)


def add_event(
    vendor_id: str, run_id: str, phase: str, step: str, status: str, title: str, detail: str | None, url: str | None
) -> None:
    execute(
        "INSERT INTO events (vendor_id, run_id, ts, phase, step, status, title, detail, url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        vendor_id, run_id, time.time(), phase, step, status, title, detail, url,
    )  # fmt: skip


def spent_today() -> float:
    found = row("SELECT usd FROM spend WHERE day = date('now')")
    return found["usd"] if found else 0.0


def add_spend(usd: float) -> None:
    execute(
        "INSERT INTO spend (day, usd) VALUES (date('now'), ?) ON CONFLICT(day) DO UPDATE SET usd = usd + excluded.usd",
        usd,
    )
