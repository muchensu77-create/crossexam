"""Takes the product screenshots used in the deck. Usage: python docs/capture.py [base_url]"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765"
OUT = Path(__file__).parent
SHOTS = {
    "shot_case": ("/#/case/sample", None),
    "shot_b": ("/#/vendor/sample-b", ".passes"),
    "shot_d": ("/#/vendor/sample-d", ".hidden-text"),
    "shot_bench": ("/#/benchmark", ".stat-grid"),
}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1320, "height": 860}, device_scale_factor=2)
    for name, (route, wait_for) in SHOTS.items():
        page.goto(BASE + route)
        page.wait_for_selector(wait_for or "table", timeout=15000)
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / f"{name}.png"))
        print(name)
    browser.close()
