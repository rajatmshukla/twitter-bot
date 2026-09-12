#!/usr/bin/env python3
"""Debug why follow clicks stop flipping — dump page state after click."""
import os, sys, time
BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
from playwright.sync_api import sync_playwright
import browser_post

h = sys.argv[1] if len(sys.argv) > 1 else "ylecun"
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
    page.goto(f"https://x.com/{h}", wait_until="domcontentloaded", timeout=60_000)
    time.sleep(4)
    btn = page.locator('[data-testid="primaryColumn"] button[data-testid$="-follow"]').first
    print("header follow button count:", btn.count())
    if btn.count():
        print("before:", btn.inner_text()[:40])
        btn.click()
        time.sleep(3)
        print("after:", btn.inner_text()[:40] if btn.count() else "GONE")
        print("unfollow buttons in primary column:",
              page.locator('[data-testid="primaryColumn"] button[data-testid$="-unfollow"]').count())
        # dump any toast/dialog text
        body = page.locator('body').inner_text()
        for kw in ["too fast", "limit", "Slow down", "confirm", "Follow", "Following"]:
            for line in body.splitlines():
                if kw.lower() in line.lower():
                    print(f"BODY[{kw}]: {line[:120]}")
                    break
    ctx.close()
