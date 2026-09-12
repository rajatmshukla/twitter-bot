#!/usr/bin/env python3
"""Audit probe 2: last ~10 posts with engagement counts + check 21:01 unsure post."""
import sys
sys.path.append(r"C:\Users\Rajat\twitter-bot")
from playwright.sync_api import sync_playwright

PROFILE = r"C:\Users\Rajat\twitter-bot\browser-profile"

def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, headless=True,
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://x.com/first_sauce_lab", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)
        arts = page.locator('article').all()
        print(f"articles visible: {len(arts)}")
        for i, a in enumerate(arts[:10]):
            try:
                t = a.inner_text(timeout=8000)
            except Exception:
                continue
            # engagement line: last numbers cluster
            tshort = t[:600].replace("\n", " | ")
            print(f"--- ART {i}: {tshort[:420]}")
        ctx.close()

if __name__ == "__main__":
    main()
