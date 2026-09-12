#!/usr/bin/env python3
"""Prove browser_guard.classify_session separates 'busy' from 'dead'.

Both cases are real, not simulated by monkeypatching the classifier:

  dead  : a throwaway context with no cookies. That is exactly what an expired
          login looks like.
  busy  : the REAL profile with JavaScript blocked via page.route. The HTML
          loads and the auth cookie is present, but the React app never
          hydrates, so no logged-in marker ever appears. That is exactly what
          profile contention looks like from inside the page.

Never posts anything.

  python3 scripts/test_session_verdicts.py
"""
import os, sys, json

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
import browser_guard as g
import browser_post
from playwright.sync_api import sync_playwright

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r} want {want!r}")
    if not ok:
        FAILS.append(label)
    return ok


def case_dead(p):
    """No cookies at all -> dead, and fast (must not burn 3 retries)."""
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
    """Real profile, JS blocked -> busy, and it must retry before giving up."""
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
    """Untouched real profile -> ok."""
    ctx = p.chromium.launch_persistent_context(
        browser_post.PROFILE, headless=True,
        viewport={"width": 1280, "height": 900})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    v, d = g.classify_session(page, tries=2)
    print(f"   detail: {json.dumps(d)}")
    ctx.close()
    check("real profile -> ok", v, "ok")


def main():
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
