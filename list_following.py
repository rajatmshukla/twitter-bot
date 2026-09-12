#!/usr/bin/env python3
"""List the accounts @first_sauce_lab currently follows."""
import os, sys, time, random
BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
from playwright.sync_api import sync_playwright
import browser_post

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        browser_post.PROFILE, headless=True,
        viewport={"width": 1280, "height": 1000},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://x.com/first_sauce_lab/following", wait_until="domcontentloaded", timeout=60_000)
    time.sleep(random.uniform(3, 5))
    # scroll to load more
    for _ in range(4):
        page.mouse.wheel(0, 2000)
        time.sleep(1.2)
    cells = page.locator('[data-testid="UserCell"]')
    n = cells.count()
    print("USERCELLS:", n)
    for i in range(n):
        try:
            txt = cells.nth(i).inner_text().replace("\n", " | ")
            print(f"[{i}] {txt[:90]}")
        except Exception as e:
            print(f"[{i}] err {e}")
    ctx.close()
