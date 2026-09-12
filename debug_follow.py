#!/usr/bin/env python3
"""Debug the follow button DOM on one profile."""
import os, sys, time
BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
from playwright.sync_api import sync_playwright
import browser_post

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
    page.goto("https://x.com/OpenAI", wait_until="domcontentloaded", timeout=60_000)
    time.sleep(4)
    def dump(label):
        btns = page.locator('button')
        n = btns.count()
        print(f"--- {label}: {n} buttons ---")
        for i in range(n):
            tid = btns.nth(i).get_attribute("data-testid") or ""
            if "follow" in tid.lower() or "ollow" in tid:
                txt = (btns.nth(i).inner_text() or "")[:40].replace("\n", " ")
                print(f"  [{i}] testid={tid!r} text={txt!r} visible={btns.nth(i).is_visible()}")
    dump("BEFORE")
    # try clicking the header-level follow button: testid ends with -follow
    btn = page.locator('button[data-testid$="-follow"]')
    print("match count:", btn.count())
    if btn.count():
        print("first button text:", btn.first.inner_text()[:40])
        btn.first.click()
        time.sleep(3)
    dump("AFTER")
    ctx.close()
