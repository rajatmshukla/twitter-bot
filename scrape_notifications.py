#!/usr/bin/env python3
"""Scrape Notifications tab for replies to the account."""
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
    for _ in range(3):
        page.mouse.wheel(0, 1200)
        time.sleep(random.uniform(1.5, 2.5))
    body = page.locator('[data-testid="primaryColumn"]').first.inner_text()
    print(body[:3000])
    ctx.close()
