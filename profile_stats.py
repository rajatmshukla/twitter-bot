#!/usr/bin/env python3
"""Read profile stats + recent own-post engagement for first_sauce_lab."""
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
    # profile stats: following/followers links in header
    stats = {}
    for label in ["Following", "Followers"]:
        try:
            el = page.locator(f'a[href="/{USER}/{label.lower()}"]').first
            if el.count():
                stats[label] = el.inner_text()
        except Exception:
            pass
    # try the aria-label on profile header container
    if not stats:
        try:
            el = page.locator('[data-testid="primaryColumn"] [role="link"]').first
            print("probe:", el.inner_text()[:200])
        except Exception:
            pass
    print("STATS:", stats)
    # recent posts: text + engagement counts (replies/retweets/likes on each article)
    arts = page.locator('article[data-testid="tweet"]')
    n = min(arts.count(), 6)
    for i in range(n):
        try:
            a = arts.nth(i)
            txt = a.inner_text().replace("\n", " | ")
            print(f"--- post {i}: {txt[:300]}")
        except Exception as e:
            print(f"post {i}: err {e}")
    ctx.close()
