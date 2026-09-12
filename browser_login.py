#!/usr/bin/env python3
"""Log into X once so the bot can post from a persistent browser session.

Opens a real (headed) Chromium window with a persistent profile. YOU log in
manually (type your credentials yourself — the script never sees them). Once
you're logged in and see your home timeline, close the window. The session is
saved in the profile and reused by browser_post.py.

Usage: python3 browser_login.py
"""
import os, sys, time

from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")

def main():
    os.makedirs(PROFILE, exist_ok=True)
    print("Opening X login in a real browser window...")
    print("LOG IN YOURSELF. When you see your home timeline, close the window.")
    print("(You have up to 3 minutes.)")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://x.com/login", wait_until="domcontentloaded")
        # Wait up to 3 minutes for the user to finish logging in and close.
        try:
            page.wait_for_url("https://x.com/home", timeout=180_000)
        except Exception:
            pass
        ctx.close()
    # verify session: open a quick headless check
    ok = check_session()
    if ok:
        print("LOGIN OK — session saved. Bot can post.")
    else:
        print("WARNING: could not verify session. Try again and make sure you reach the home timeline.")

def check_session():
    """Reliable check: logged-in-only DOM marker (SideNav_NewTweet_Button).

    The old URL-only check (`\"login\" not in url`) is NOT trustworthy — a
    logged-out x.com/ splash contains no \"login\" and falsely reports OK.
    """
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                PROFILE, headless=True,
                viewport={"width": 1280, "height": 900})
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=45_000)
            time.sleep(3)
            try:
                page.wait_for_selector('[data-testid="SideNav_NewTweet_Button"]', timeout=15_000)
                ok = True
            except Exception:
                ok = False
            ctx.close()
            return ok
    except Exception as e:
        print(f"  session check error: {e}")
        return False

if __name__ == "__main__":
    main()
