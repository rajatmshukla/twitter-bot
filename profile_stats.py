#!/usr/bin/env python3
"""Diagnostic script to inspect follower counts and recent post text for @first_sauce_lab.

Navigates to the user's X profile using the persistent browser profile, extracts
the 'Following' and 'Followers' counter values from header links, and prints
single-line text snippets for up to 6 recent visible posts.

Invocation:
    Run manually via CLI:
        python3 profile_stats.py
    Whether an automated scheduler or cron invokes this script is not evident
    from this file; it appears to be an ad-hoc diagnostic probe.

Inputs and Outputs:
    Reads:
        - Persistent browser cookies and session state from browser-profile.
        - Profile header anchor elements and tweet article elements from x.com.
    Writes:
        - Prints parsed statistics dict and post text snippets to stdout.

Live Account Effects:
    Launches persistent Chromium browser context, visits the profile page, and
    sleeps 3-5 seconds to allow page rendering. Read-only; does not mutate account
    state. Does not acquire browser_guard lock.
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
    # Allow client-side React rendering to populate header and tweet stream.
    time.sleep(random.uniform(3, 5))
    # Extract Following/Followers counters from dedicated profile header anchor links.
    stats = {}
    for label in ["Following", "Followers"]:
        try:
            el = page.locator(f'a[href="/{USER}/{label.lower()}"]').first
            if el.count():
                stats[label] = el.inner_text()
        except Exception:
            pass
    # Fallback inspection if specific counter links were not found.
    if not stats:
        try:
            el = page.locator('[data-testid="primaryColumn"] [role="link"]').first
            print("probe:", el.inner_text()[:200])
        except Exception:
            pass
    print("STATS:", stats)
    # Sample up to 6 visible posts to inspect recent timeline text without scrolling.
    arts = page.locator('article[data-testid="tweet"]')
    n = min(arts.count(), 6)
    for i in range(n):
        try:
            a = arts.nth(i)
            # Flatten multi-line tweet text into a single line for compact stdout display.
            txt = a.inner_text().replace("\n", " | ")
            print(f"--- post {i}: {txt[:300]}")
        except Exception as e:
            print(f"post {i}: err {e}")
    ctx.close()
