"""Runs inside the sandbox. Reads one untrusted document and prints JSON to stdout.

This process has no network, no credentials and a read-only filesystem. It separates what a person would see on
the page from text hidden from them: ink matching its background, tiny type, and text placed outside the page.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MIN_VISIBLE_PT = 3.0
MIN_CONTRAST = 40  # out of 255, on the worst colour channel
RENDER_DPI = 72


def split_rgb(color: int) -> tuple[int, int, int]:
    return (color >> 16) & 255, (color >> 8) & 255, color & 255


def background_at(pixmap, bbox: tuple[float, float, float, float]) -> tuple[int, int, int]:
    """The most common colour in a rendered box. Body text covers little of its box, so this is the background."""
    scale = RENDER_DPI / 72
    x0, y0 = max(int(bbox[0] * scale), 0), max(int(bbox[1] * scale), 0)
    x1, y1 = min(int(bbox[2] * scale) + 1, pixmap.width), min(int(bbox[3] * scale) + 1, pixmap.height)
    counts: dict[tuple[int, int, int], int] = {}
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixel = pixmap.pixel(x, y)[:3]
            counts[pixel] = counts.get(pixel, 0) + 1
    return max(counts, key=counts.get) if counts else (255, 255, 255)


def blends_in(color: int, background: tuple[int, int, int]) -> bool:
    return max(abs(a - b) for a, b in zip(split_rgb(color), background)) < MIN_CONTRAST


def parse_pdf(path: Path) -> dict:
    import fitz  # PyMuPDF

    visible: list[str] = []
    hidden: list[dict] = []
    with fitz.open(path) as document:
        pages = document.page_count
        for number, page in enumerate(document, start=1):
            bounds = page.rect
            pixmap = page.get_pixmap(dpi=RENDER_DPI, alpha=False)
            lines: list[str] = []
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    shown = ""
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        if not text.strip():
                            shown += text
                            continue
                        x0, y0, x1, y1 = span.get("bbox", (0, 0, 0, 0))
                        reason = None
                        if x1 < bounds.x0 or x0 > bounds.x1 or y1 < bounds.y0 or y0 > bounds.y1:
                            reason = "text placed outside the page"
                        elif span.get("size", 12) < MIN_VISIBLE_PT:
                            reason = "text too small to read"
                        elif blends_in(span.get("color", 0), background_at(pixmap, (x0, y0, x1, y1))):
                            reason = "text the same colour as its background"
                        if reason:
                            hidden.append({"page": number, "reason": reason, "text": text.strip()})
                        else:
                            shown += text
                    if shown.strip():
                        lines.append(shown.strip())
            visible.append("\n".join(lines))
    return {"kind": "pdf", "pages": pages, "text": "\n\n".join(visible), "hidden": hidden}


def parse_text(path: Path) -> dict:
    return {"kind": "text", "pages": 1, "text": path.read_text(encoding="utf-8", errors="replace"), "hidden": []}


def main() -> int:
    path = Path(sys.argv[1])
    head = path.read_bytes()[:5]
    result = parse_pdf(path) if head == b"%PDF-" else parse_text(path)
    json.dump(result, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
