"""Attempts what a hostile document parser would attempt, from inside the sandbox.

Each check prints whether the attempt was blocked. It runs in the same container and with the same limits as a real parse.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path


def attempt(name: str, action) -> dict:
    try:
        detail = action()
        return {"attempt": name, "blocked": False, "detail": str(detail)[:120]}
    except Exception as exc:  # a failure here is the sandbox doing its job
        return {"attempt": name, "blocked": True, "detail": type(exc).__name__}


def reach_internet() -> str:
    with socket.create_connection(("1.1.1.1", 443), timeout=3):
        return "connected"


def reach_metadata() -> str:
    with socket.create_connection(("169.254.169.254", 80), timeout=3):
        return "connected"


def resolve_dns() -> str:
    return socket.gethostbyname("api.vultrinference.com")


def write_root() -> str:
    Path("/w/planted").write_text("x")
    return "wrote /w/planted"


def overwrite_document() -> str:
    Path("/in/doc").write_text("x")
    return "overwrote the input"


def find_secret() -> str:
    leaked = [key for key in os.environ if "KEY" in key or "TOKEN" in key or "SECRET" in key]
    if leaked:
        return f"found {leaked}"
    raise PermissionError("no secrets in the environment")


def main() -> int:
    checks = [
        ("Connect to the internet", reach_internet),
        ("Reach the cloud metadata service", reach_metadata),
        ("Resolve a hostname", resolve_dns),
        ("Write to the root filesystem", write_root),
        ("Overwrite the input document", overwrite_document),
        ("Find a credential in the environment", find_secret),
    ]
    results = [attempt(name, action) for name, action in checks]
    print(json.dumps({"kernel": os.uname().release, "uid": os.getuid(), "results": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
