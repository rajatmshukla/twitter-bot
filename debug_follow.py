#!/usr/bin/env python3
"""Debug probe for follow button DOM states and click interaction behavior.

Answers:
    How do follow button data-testids, label text, and visibility change in the
    DOM before and after clicking follow on a target profile?

Invocation:
    python debug_follow.py

Inputs:
    Hardcoded target profile: https://x.com/OpenAI.
    Requires an active authenticated session in browser_post.PROFILE.

Outputs:
    Prints button counts, testids, inner text, and visibility before and after
    click to standard output.

Side effects:
    Live mutation: clicks the follow button on @OpenAI, following or toggling
    follow status for that account. Exits with status 2 if not authenticated.
    It is not clear from this file if an automated scheduler triggers it.
"""
import os, sys, time
BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
from playwright.sync_api import sync_playwright
import browser_post

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        browser_post.PROFILE, headless=True,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    if not browser_post.check_session(page):
        print("NOT LOGGED IN")
        ctx.close()
        sys.exit(2)
    page.goto("https://x.com/OpenAI", wait_until="domcontentloaded", timeout=60_000)
    time.sleep(4)
    def dump(label):
        """Log testid, label text, and visibility for follow-related buttons."""
        btns = page.locator('button')
        n = btns.count()
        print(f"--- {label}: {n} buttons ---")
        for i in range(n):
            tid = btns.nth(i).get_attribute("data-testid") or ""
            if "follow" in tid.lower() or "ollow" in tid:
                txt = (btns.nth(i).inner_text() or "")[:40].replace("\n", " ")
                print(f"  [{i}] testid={tid!r} text={txt!r} visible={btns.nth(i).is_visible()}")
    dump("BEFORE")
    # Locate profile follow button whose data-testid ends with '-follow'
    btn = page.locator('button[data-testid$="-follow"]')
    print("match count:", btn.count())
    if btn.count():
        print("first button text:", btn.first.inner_text()[:40])
        btn.first.click()
        time.sleep(3)
    dump("AFTER")
    ctx.close()
