#!/usr/bin/env python3
"""Probe profile header text to find follower count."""
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
    time.sleep(random.uniform(3, 5))
    # dump all text in the primary column top area
    try:
        col = page.locator('[data-testid="primaryColumn"]').first
        txt = col.inner_text()
        print(txt[:1200])
    except Exception as e:
        print("err", e)
    ctx.close()
