"""Runtime settings. Everything comes from the environment so the same code runs on a laptop and on the Vultr VM."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    inference_base: str
    inference_key: str | None
    model_extract: str
    model_write: str
    model_vision: str
    sandbox_mode: str  # "gvisor", "docker" or "local" (local gives no isolation and is for development only)
    sandbox_image: str
    sandbox_timeout_s: int
    sandbox_memory: str
    max_upload_bytes: int
    daily_llm_budget_usd: float


def load() -> Settings:
    data_dir = Path(os.environ.get("CROSSEXAM_DATA", ROOT / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        data_dir=data_dir,
        inference_base=os.environ.get("VULTR_INFERENCE_BASE", "https://api.vultrinference.com/v1"),
        inference_key=os.environ.get("VULTR_INFERENCE_KEY") or None,
        model_extract=os.environ.get("MODEL_EXTRACT", "glm-5.3-flash"),
        model_write=os.environ.get("MODEL_WRITE", "glm-5.3"),
        model_vision=os.environ.get("MODEL_VISION", "glm-5.3-flash"),
        sandbox_mode=os.environ.get("SANDBOX_MODE", "local"),
        sandbox_image=os.environ.get("SANDBOX_IMAGE", "crossexam-parser:latest"),
        sandbox_timeout_s=int(os.environ.get("SANDBOX_TIMEOUT_S", "20")),
        sandbox_memory=os.environ.get("SANDBOX_MEMORY", "256m"),
        max_upload_bytes=int(os.environ.get("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024))),
        daily_llm_budget_usd=float(os.environ.get("DAILY_LLM_BUDGET_USD", "5")),
    )


settings = load()
