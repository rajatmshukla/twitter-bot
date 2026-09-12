#!/usr/bin/env python3
"""Diagnose why mentions_guy reports NOT LOGGED IN.

Read-only: launches the shared profile, runs BOTH session checks against the
same context, and prints what each one actually saw. Posts nothing.

  python3 scripts/diag_session_checks.py
"""
import os, sys, time

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)

import reply_guy as rg
import browser_post as bp
from playwright.sync_api import sync_playwright

MARKERS = {
    "SideNav_NewTweet_Button": '[data-testid="SideNav_NewTweet_Button"]',
    "tweetTextarea_0": '[data-testid="tweetTextarea_0"]',
    "primaryColumn": '[data-testid="primaryColumn"]',
    "SideNav_AccountSwitcher_Button": '[data-testid="SideNav_AccountSwitcher_Button"]',
    "login button": '[data-testid="loginButton"]',
}


def markers(page):
    out = {}
    for name, sel in MARKERS.items():
        try:
            out[name] = page.locator(sel).count()
        except Exception as e:
            out[name] = f"err:{type(e).__name__}"
    return out


def dump(page, label):
    print(f"--- {label} ---")
    try:
        print(f"url   : {page.url}")
        print(f"title : {page.title()}")
    except Exception as e:
        print(f"url/title failed: {e}")
    print(f"marks : {markers(page)}")
    try:
        print(f"cookies: {len(page.context.cookies())}")
    except Exception as e:
        print(f"cookies failed: {e}")


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            rg.PROFILE, headless=True, viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        t0 = time.time()
        ok_rg = rg.check_session(page)
        print(f"reply_guy.check_session    -> {ok_rg}  ({time.time()-t0:.1f}s)")
        dump(page, "after reply_guy.check_session")

        page2 = ctx.new_page()
        t0 = time.time()
        ok_bp = bp.check_session(page2)
        print(f"browser_post.check_session -> {ok_bp}  ({time.time()-t0:.1f}s)")
        dump(page2, "after browser_post.check_session")

        ctx.close()

    print(f"\nVERDICT: reply_guy={ok_rg} browser_post={ok_bp}")
    if ok_bp and not ok_rg:
        print("=> FALSE NEGATIVE in reply_guy.check_session. Session is alive.")
    elif ok_bp and ok_rg:
        print("=> Session is fine; the cron failure was something else (or transient).")
    else:
        print("=> Session genuinely dead. Run browser_login.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
