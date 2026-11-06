"""Renders docs/deck.html to the PDF and cover image that go into the submission.

Usage: python docs/build_deck.py [public_url] [model_cost_per_vendor]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

DOCS = Path(__file__).parent
URL = sys.argv[1] if len(sys.argv) > 1 else "github.com/muchensu77-create/crossexam"
COST = sys.argv[2] if len(sys.argv) > 2 else "TBD"  # only ever a measured figure; never ship the deck with TBD
REPO = "github.com/muchensu77-create/crossexam"

html = (DOCS / "deck.html").read_text(encoding="utf-8")
rendered = DOCS / "_deck_rendered.html"
rendered.write_text(html.replace("{{URL}}", URL).replace("{{REPO}}", REPO).replace("{{COST}}", COST), encoding="utf-8")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=2)
    page.goto(rendered.as_uri(), wait_until="networkidle")
    page.evaluate("document.fonts.ready")
    page.pdf(path=str(DOCS / "Crossexam_deck.pdf"), width="1280px", height="720px", print_background=True)
    page.locator("section.title").screenshot(path=str(DOCS / "Crossexam_cover.png"))
    overflow = page.evaluate(
        """() => [...document.querySelectorAll('section.s')].map((s, i) => {
            const foot = s.querySelector('.foot');
            const limit = foot ? foot.getBoundingClientRect().top : s.getBoundingClientRect().bottom;
            const worst = Math.max(...[...s.querySelectorAll('.body, .body *, p.src, section > .card')]
                .map(e => e.getBoundingClientRect().bottom));
            return worst > limit + 1 ? `slide ${i + 1} overflows by ${Math.round(worst - limit)}px` : null;
        }).filter(Boolean)"""
    )
    browser.close()

rendered.unlink()
print("overflow:", overflow or "none")
