#!/usr/bin/env python3
"""Debug follow click state transitions and detect rate limits or warning dialogs.

Answers:
    Why does clicking the follow button fail to transition to an unfollow state,
    and are rate limits or confirmation modals present in the page body?

Invocation:
    python debug_follow2.py [handle]

Inputs:
    sys.argv[1] (optional): Twitter handle to test (defaults to 'ylecun').
    Requires an active authenticated session in browser_post.PROFILE.

Outputs:
    Prints header button status before and after click, unfollow button counts,
    and any body lines matching rate limit or confirmation warning keywords.

Side effects:
    Live mutation: clicks the follow button for the target handle on X.
    Exits with status 2 if session check fails.
    It is not clear from this file if an automated scheduler triggers it.
"""
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
    # Scope to primaryColumn so sidebar suggested users are ignored
    btn = page.locator('[data-testid="primaryColumn"] button[data-testid$="-follow"]').first
    print("header follow button count:", btn.count())
    if btn.count():
        print("before:", btn.inner_text()[:40])
        btn.click()
        time.sleep(3)
        print("after:", btn.inner_text()[:40] if btn.count() else "GONE")
        # Check if follow flipped to unfollow, indicating successful follow action
        print("unfollow buttons in primary column:",
              page.locator('[data-testid="primaryColumn"] button[data-testid$="-unfollow"]').count())
        # Scan page body for warning toasts or modal prompts (rate limits, bot challenge)
        body = page.locator('body').inner_text()
        for kw in ["too fast", "limit", "Slow down", "confirm", "Follow", "Following"]:
            for line in body.splitlines():
                if kw.lower() in line.lower():
                    print(f"BODY[{kw}]: {line[:120]}")
                    break
    ctx.close()
