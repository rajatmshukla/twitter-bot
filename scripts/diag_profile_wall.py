#!/usr/bin/env python3
"""Why does the profile read as logged out when the cookies are on disk?

Launches the shared profile, dumps what the browser itself can see, and saves
a screenshot so the page can be looked at instead of guessed about.

Read-only: no posting, no navigation beyond x.com/home.

  python3 scripts/diag_profile_wall.py
"""
import json, os, sys, time

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
from playwright.sync_api import sync_playwright
import browser_post, browser_guard

SHOT = os.path.join(BOT, "tmp", "profile_wall.png")


def main():
    os.makedirs(os.path.dirname(SHOT), exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            browser_post.PROFILE, headless=True,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        r = page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60_000)
        print(f"goto status: {r.status if r else None}")
        time.sleep(6)
        print(f"url  : {page.url}")
        print(f"title: {page.title()}")

        cks = ctx.cookies()
        names = sorted({c["name"] for c in cks})
        print(f"\ncookies visible to the BROWSER: {len(cks)}")
        print(f"  names: {names}")
        for c in cks:
            if c["name"] in ("auth_token", "ct0", "twid"):
                print(f"  {c['name']}: domain={c['domain']} path={c['path']} "
                      f"expires={c.get('expires')}")

        body = ""
        try:
            body = page.locator("body").inner_text()[:600]
        except Exception as e:
            print(f"body read failed: {e}")
        print(f"\npage text (first 600 chars):\n{body}")

        page.screenshot(path=SHOT, full_page=False)
        print(f"\nscreenshot: {SHOT}")
        ctx.close()

    print(f"\nhealth record: {json.dumps(browser_guard.health())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
