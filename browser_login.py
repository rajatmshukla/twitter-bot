#!/usr/bin/env python3
"""Interactive login utility to establish persistent browser session for X.

Opens a headed Chromium window with a persistent profile directory where the
operator manually logs in. The script never inspects or stores credentials.
Once login succeeds and the home timeline loads, the session is saved in the
profile directory and reused by automated posting modules (browser_post.py,
browser_share.py, browser_thread.py).

Invocation:
- CLI (manual operator only):
    python browser_login.py
- Never invoked automatically via cron or background jobs because it requires
  manual interaction in a headed browser window.

Inputs / Reads:
- Interactive operator keyboard and mouse input in the headed browser.
- Reads home timeline DOM to check for authenticated navigation elements.

Outputs / Writes:
- Creates and updates persistent browser profile files in browser-profile/
  (cookies, local storage, session state).

Live X Account Impact:
- Read-only. Does not publish posts, retweets, or modify profile settings.
- Establishes authenticated session state for subsequent posting scripts.
"""
import os, sys, time

from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")

def main():
    """Launch headed browser for manual operator login and verify session.

    Opens a headed Chromium window pointing to the X login page and waits up to
    180 seconds for the user to complete login and reach the home feed.
    After the window closes, runs check_session() in headless mode to verify.

    Arguments:
        None.
    Returns:
        None.
    Side effects:
        Launches a visible browser window, writes authentication state to
        browser-profile/, and prints progress to stdout. Does not post to X.
    """
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
    """Verify that the saved browser profile holds an active logged-in session.

    Launches a headless browser context, loads the home timeline, and waits for
    the tweet composer button (SideNav_NewTweet_Button). The URL check
    ('login' not in url) is untrustworthy because the logged-out splash page
    does not contain 'login' in its URL.

    Arguments:
        None.
    Returns:
        True if the logged-in sidebar button is detected, False otherwise.
    Side effects:
        Launches and closes a headless Chromium context. Navigates to
        https://x.com/home and sleeps 3 seconds. Spends no API budget.
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
