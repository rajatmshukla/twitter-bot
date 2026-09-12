#!/usr/bin/env python3
"""Audit probe 3: scroll profile posts, find orphan draft text, dump engagement stats."""
import sys, time
sys.path.append(r"C:\Users\Rajat\twitter-bot")
from playwright.sync_api import sync_playwright

PROFILE = r"C:\Users\Rajat\twitter-bot\browser-profile"
MARKER = "Swapped playwright-mcp for playwright-cli"

def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, headless=True,
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://x.com/first_sauce_lab", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3500)
        found = False
        seen = set()
        for scroll in range(12):
            arts = page.locator('article').all()
            new = 0
            for a in arts:
                try:
                    t = a.inner_text(timeout=5000)
                except Exception:
                    continue
                key = t[:80]
                if key in seen:
                    continue
                seen.add(key); new += 1
                if MARKER in t:
                    print("FOUND ORPHAN DRAFT TEXT LIVE ON PROFILE (scroll", scroll, ")")
                    print("TEXT:", t[:220].replace("\n", " | "))
                    found = True
            if not new:
                break
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(1200)
        print("---")
        print("ORPHAN LIVE:", found)
        print("total distinct articles seen:", len(seen))
        ctx.close()

if __name__ == "__main__":
    main()
