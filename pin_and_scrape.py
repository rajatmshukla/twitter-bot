#!/usr/bin/env python3
"""Pin the Gemini 3.7 Flash thread root and scrape replies to it."""
import os, time, random
from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")
MAIN_ID = "2087993179127599252"

def human_delay(a=1.0, b=2.0):
    time.sleep(random.uniform(a, b))

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, headless=True, viewport={"width": 1280, "height": 1000},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    # ---- pin the main tweet ----
    page.goto(f"https://x.com/first_sauce_lab/status/{MAIN_ID}", wait_until="domcontentloaded", timeout=60_000)
    human_delay(2, 4)
    try:
        page.wait_for_selector('[data-testid="caret"]', timeout=15_000)
        page.locator('[data-testid="caret"]').first.click()
        human_delay(1, 2)
        page.wait_for_selector('text=Pin to your profile', timeout=10_000)
        page.locator('text=Pin to your profile').first.click()
        human_delay(2, 3)
        print("PIN: submitted")
    except Exception as e:
        print("PIN: failed", e)

    # ---- scrape replies to the main tweet ----
    page.goto(f"https://x.com/first_sauce_lab/status/{MAIN_ID}", wait_until="domcontentloaded", timeout=60_000)
    human_delay(3, 5)
    arts = page.locator('article[data-testid="tweet"]')
    n = min(arts.count(), 12)
    print(f"--- {n} articles on page ---")
    for i in range(n):
        try:
            a = arts.nth(i)
            txt = a.inner_text().replace("\n", " | ")
            print(f"[{i}] {txt[:260]}")
        except Exception as e:
            print(f"[{i}] err {e}")
    ctx.close()
