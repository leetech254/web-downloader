from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "tools" / "social-preview.html"
OUTPUT = ROOT / "static" / "social-preview.jpg"


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", headless=True)
    page = browser.new_page(viewport={"width": 1200, "height": 760}, device_scale_factor=1)
    page.goto(HTML.as_uri(), wait_until="load")
    page.locator("main.social-card").screenshot(path=str(OUTPUT), type="jpeg", quality=91)
    browser.close()

print(f"Rendered {OUTPUT}")
