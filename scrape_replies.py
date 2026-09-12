#!/usr/bin/env python3
"""Probe script to verify profile pin status and scrape third-party replies.

Answers:
    1. Is the target tweet pinned to the top of @first_sauce_lab's profile?
    2. Have any third-party accounts replied to thread root MAIN_ID?

Requirements:
    Requires a persistent Playwright browser context with an active session
    in browser-profile.

Output:
    Prints up to 3 top profile tweets to stdout, followed by any third-party
    reply articles detected on the thread status page after scrolling.

Side effects:
    Read-only navigation and DOM inspection. Does not mutate live state.
    It is not clear from this file if an automated scheduler triggers it.
"""
import os, time, random
from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")
MAIN_ID = "2087993179127599252"
USER = "first_sauce_lab"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, headless=True, viewport={"width": 1280, "height": 1000},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    # ---- verify pin on profile ----
    page.goto(f"https://x.com/{USER}", wait_until="domcontentloaded", timeout=60_000)
    time.sleep(random.uniform(3, 5))
    try:
        arts = page.locator('article[data-testid="tweet"]')
        # Check first 3 tweets to verify whether MAIN_ID is pinned at the top
        n = min(arts.count(), 3)
        for i in range(n):
            txt = arts.nth(i).inner_text().replace("\n", " | ")
            print(f"PROFILE[{i}]: {txt[:160]}")
    except Exception as e:
        print("profile err", e)

    # ---- scroll status page for outside replies ----
    page.goto(f"https://x.com/{USER}/status/{MAIN_ID}", wait_until="domcontentloaded", timeout=60_000)
    time.sleep(random.uniform(3, 5))
    # Scroll repeatedly to trigger dynamic DOM loading of reply tweets
    for _ in range(5):
        page.mouse.wheel(0, 1500)
        time.sleep(random.uniform(1.5, 2.5))
    arts = page.locator('article[data-testid="tweet"]')
    n = arts.count()
    print(f"--- {n} articles after scroll ---")
    for i in range(n):
        try:
            a = arts.nth(i)
            txt = a.inner_text().replace("\n", " | ")
            # Exclude self-authored thread posts by checking handle in header block
            if "@" in txt and USER not in txt.split("|")[0]:
                print(f"[{i}] OTHER: {txt[:300]}")
        except Exception as e:
            print(f"[{i}] err {e}")
    ctx.close()
