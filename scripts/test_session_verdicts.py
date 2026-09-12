#!/usr/bin/env python3
"""WARNING: This test launches the live browser profile and accesses x.com.
Do not run casually, although it reads session state only and never posts.

Behaviour under test:
Verifies that browser_guard.classify_session accurately distinguishes session states:
- "dead": A clean context without auth cookies immediately classifies as dead on attempt 1.
- "busy": A live profile with auth cookies but blocked JS exhausts retries and classifies as busy.
- "ok": A normal authenticated live profile evaluates cleanly to ok within two tries.

Why this matters:
When another process holds the profile, the React app fails to hydrate despite valid cookies.
If misclassified as dead, it fires false "NOT LOGGED IN" Discord alerts. True expired logins
must be identified immediately on attempt 1 without burning unnecessary retries.

How to run:
    python scripts/test_session_verdicts.py
Requires: Live logged-in Chromium profile, network access to x.com, and Playwright.
Acquires browser_guard lock. Never publishes or mutates any tweets.

What a failure means in practice:
Transient lock contention could wake engineers with false alarms, or expired credentials
could waste execution time looping through retries before reporting failure.
"""
import os, sys, json

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
import browser_guard as g
import browser_post
from playwright.sync_api import sync_playwright

FAILS = []


def check(label, got, want):
    """Assert actual equals expected, logging failures to FAILS list and returning success."""
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r} want {want!r}")
    if not ok:
        FAILS.append(label)
    return ok


def case_dead(p):
    """Verify a cookie-free context classifies as dead immediately on attempt 1."""
    import time
    ctx = p.chromium.launch()
    page = ctx.new_page()
    t0 = time.time()
    v, d = g.classify_session(page, tries=3, verbose=True)
    dt = time.time() - t0
    print(f"   detail: {json.dumps(d)}")
    print(f"   took {dt:.1f}s")
    ctx.close()
    check("no-cookie context -> dead", v, "dead")
    # A dead login needs no retries: it is decided on hard evidence.
    check("dead decided on attempt 1", d.get("attempt"), 1)


def case_busy(p):
    """Verify a real profile with blocked JS exhausts retries and classifies as busy."""
    ctx = p.chromium.launch_persistent_context(
        browser_post.PROFILE, headless=True,
        viewport={"width": 1280, "height": 900})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.route("**/*.js", lambda route: route.abort())
    import time
    t0 = time.time()
    v, d = g.classify_session(page, tries=3, verbose=True)
    dt = time.time() - t0
    print(f"   detail: {json.dumps(d)}")
    print(f"   took {dt:.1f}s")
    ctx.close()
    check("js-blocked real profile -> busy", v, "busy")
    check("busy exhausted its retries", d.get("attempt"), 3)
    check("busy kept the auth cookie", bool(d.get("auth_cookie")), True)


def case_ok(p):
    """Verify an unblocked live profile is recognized as authenticated ok."""
    ctx = p.chromium.launch_persistent_context(
        browser_post.PROFILE, headless=True,
        viewport={"width": 1280, "height": 900})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    v, d = g.classify_session(page, tries=2)
    print(f"   detail: {json.dumps(d)}")
    ctx.close()
    check("real profile -> ok", v, "ok")


def main():
    """Acquire browser guard lock and execute dead, busy, and ok session tests."""
    if not g.acquire("test_session_verdicts"):
        print(f"SKIP: {g.busy_reason()}")
        return 0
    import atexit
    atexit.register(g.release)
    with sync_playwright() as p:
        print("== dead (throwaway context, no cookies) ==")
        case_dead(p)
        print("\n== busy (real profile, JS blocked) ==")
        case_busy(p)
        print("\n== ok (real profile) ==")
        case_ok(p)
    print("\n" + ("ALL PASS" if not FAILS else f"FAILURES: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
