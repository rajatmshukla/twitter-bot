#!/usr/bin/env python3
"""Probe script to scrape and dump the account Notifications timeline.

Answers:
    What recent notifications, mentions, and interactions are visible in the
    authenticated account's Notifications feed?

Requirements:
    Requires a persistent Playwright browser context with an active login
    session in browser-profile.

Output:
    Prints up to 3000 characters of text from the primary notification column
    to standard output.

Side effects:
    Read-only navigation and scraping. Does not interact or mutate live state.
    It is not clear from this file if an automated scheduler triggers it.
"""
import os, time, random
from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, headless=True, viewport={"width": 1280, "height": 1000},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://x.com/notifications", wait_until="domcontentloaded", timeout=60_000)
    time.sleep(random.uniform(4, 6))
    # Scroll down repeatedly to trigger lazy loading of additional notifications
    for _ in range(3):
        page.mouse.wheel(0, 1200)
        time.sleep(random.uniform(1.5, 2.5))
    # Extract text from primary feed container and truncate output to 3000 chars
    body = page.locator('[data-testid="primaryColumn"]').first.inner_text()
    print(body[:3000])
    ctx.close()
