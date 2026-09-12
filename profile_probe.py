#!/usr/bin/env python3
"""Diagnostic probe for inspecting raw profile header text of @first_sauce_lab.

Launches a headless browser session with the persistent profile, navigates to
the user's X profile page, and prints the first 1200 characters of the primary
column DOM text to stdout. Used for ad-hoc inspection of header formatting,
follower/following count strings, and bio text.

Invocation:
    Run manually via CLI:
        python3 profile_probe.py
    Whether an automated scheduler or cron invokes this script is not evident
    from this file; it appears to be an ad-hoc diagnostic probe.

Inputs and Outputs:
    Reads:
        - Persistent browser cookies and session state from browser-profile.
        - Primary column DOM text from https://x.com/first_sauce_lab.
    Writes:
        - Prints first 1200 characters of profile header text to stdout.

Live Account Effects:
    Opens persistent Chromium browser context, visits the profile page, and
    sleeps 3-5 seconds to allow dynamic rendering. Read-only; does not modify
    account data. Does not acquire browser_guard lock.
"""
import os, time, random
from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")
USER = "first_sauce_lab"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, headless=True, viewport={"width": 1280, "height": 1000},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(f"https://x.com/{USER}", wait_until="domcontentloaded", timeout=60_000)
    # Allow client-side rendering to populate header and metric elements.
    time.sleep(random.uniform(3, 5))
    # Dump all text in the primary column top area to inspect raw text layout.
    try:
        # primaryColumn contains the header, bio, follower counters, and tweet feed.
        col = page.locator('[data-testid="primaryColumn"]').first
        txt = col.inner_text()
        # Truncate to 1200 characters to capture profile metadata without the post feed.
        print(txt[:1200])
    except Exception as e:
        print("err", e)
    ctx.close()
