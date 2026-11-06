"""Opens vendor documents inside a disposable sandbox.

A vendor's file is untrusted input from an outside party. On the VM each file gets its own gVisor container with no
network, no secrets, a read-only root, a memory and process cap, and a hard timeout; the container is destroyed after
one document. The parser's only way out is the JSON it prints.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import ROOT, settings

PARSER = ROOT / "sandbox" / "parse_doc.py"


@dataclass
class ParseResult:
    ok: bool
    text: str = ""
    pages: int = 0
    hidden: list[dict] = field(default_factory=list)
    isolation: str = "none"
    limits: dict = field(default_factory=dict)
    duration_s: float = 0.0
    failure: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _docker_command(path: Path, name: str, runtime: str | None) -> list[str]:
    command = [
        "docker", "run", "--rm", "--name", name,
        "--network=none",
        "--read-only",
        "--tmpfs", "/tmp:size=16m,noexec,nosuid",
        f"--memory={settings.sandbox_memory}",
        f"--memory-swap={settings.sandbox_memory}",
        "--cpus=1",
        "--pids-limit=64",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "-v", f"{path}:/in/doc:ro",
    ]
    if runtime:
        command.append(f"--runtime={runtime}")
    return command + [settings.sandbox_image, "/in/doc"]


def _kill(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def parse_document(path: Path) -> ParseResult:
    mode = settings.sandbox_mode
    limits = {
        "network": "none",
        "secrets": "none",
        "filesystem": "read-only",
        "memory": settings.sandbox_memory,
        "timeout_s": settings.sandbox_timeout_s,
        "lifetime": "one document",
    }
    name = f"parse-{uuid.uuid4().hex[:12]}"
    if mode == "local":
        command = [sys.executable, "-I", str(PARSER), str(path)]
        isolation, limits = "none (development mode)", {"timeout_s": settings.sandbox_timeout_s}
    else:
        runtime = "runsc" if mode == "gvisor" else None
        command = _docker_command(path.resolve(), name, runtime)
        isolation = "gVisor (runsc)" if runtime else "Docker"

    started = time.time()
    try:
        completed = subprocess.run(command, capture_output=True, timeout=settings.sandbox_timeout_s, check=False)
    except subprocess.TimeoutExpired:
        if mode != "local":
            _kill(name)
        return ParseResult(
            ok=False,
            isolation=isolation,
            limits=limits,
            duration_s=time.time() - started,
            failure=f"Parser stopped at the {settings.sandbox_timeout_s}s limit and the sandbox was destroyed.",
        )

    duration = time.time() - started
    if completed.returncode != 0:
        reason = "ran out of its memory limit" if completed.returncode == 137 else f"exited with code {completed.returncode}"
        return ParseResult(
            ok=False, isolation=isolation, limits=limits, duration_s=duration, failure=f"Parser {reason}; sandbox destroyed."
        )
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return ParseResult(
            ok=False, isolation=isolation, limits=limits, duration_s=duration, failure="Parser output was not valid JSON."
        )
    return ParseResult(
        ok=True,
        text=data.get("text", ""),
        pages=data.get("pages", 0),
        hidden=data.get("hidden", []),
        isolation=isolation,
        limits=limits,
        duration_s=duration,
    )
