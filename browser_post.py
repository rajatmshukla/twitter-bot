#!/usr/bin/env python3
"""Post a tweet via the X web UI (browser automation), using the persistent
session created by browser_login.py. No API keys needed.

Usage:
  python3 browser_post.py "tweet text"
  python3 browser_post.py --file drafts/xxx.txt
  python3 browser_post.py --check          # verify session, post nothing

Exit codes: 0 posted, 2 not logged in, 3 blocked/captcha, 4 other error
"""
import os, sys, time, random, argparse, datetime

from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")

def human_delay(a=1.0, b=2.5):
    time.sleep(random.uniform(a, b))

def log(line):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {line}")
    with open(os.path.join(BOT, "logs", "posts.log"), "a", encoding="utf-8") as f:
        f.write(f"{ts} {line}\n")

def check_session(page):
    """True only if the page shows real logged-in UI, not just a shell.

    One implementation for the whole bot lives in browser_guard. This wrapper
    exists because a lot of scripts import browser_post.check_session, and it
    replaces the local copy that had to be patched separately on 2026-09-11
    (the fix did not reach reply_guy.check_session, which is what the mentions
    cron actually called).
    """
    import browser_guard
    return browser_guard.check_session(page)

def post_text(page, text, image_path=None):
    # X's /compose/post page is unstable (2026-08-24: hidden duplicate dialog +
    # mask overlay + unsent-draft restore sheets). The home inline composer is
    # reliable; Ctrl+Enter submits without needing a button click.
    page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60_000)
    human_delay(2, 4)
    if "login" in page.url:
        return 2
    try:
        page.wait_for_selector('[data-testid="tweetTextarea_0"]', timeout=20_000)
    except Exception:
        log("ERROR: composer textarea not found (page change or bot check)")
        return 3
    box = page.locator('[data-testid="tweetTextarea_0"]').last
    if image_path:
        try:
            # hidden file input on the compose page
            fi = page.locator('input[data-testid="fileInput"]').first
            fi.set_input_files(image_path, timeout=20_000)
            log(f"image attached: {image_path}")
        except Exception as e:
            log(f"ERROR: could not attach image: {e}")
            return 3
        human_delay(3, 5)  # upload + preview render
        # sanity: media preview present (attachments container with a rendered image)
        try:
            page.wait_for_selector('[data-testid="attachments"] img', timeout=20_000)
            log("media preview confirmed")
        except Exception:
            log("WARN: media preview not confirmed, posting anyway")
    box.click()
    human_delay(0.4, 1.0)
    box.type(text, delay=random.randint(25, 60))
    human_delay(0.8, 1.8)
    # submit via the inline Post button (tweetButtonInline on home); Ctrl+Enter fallback
    btn = page.locator('[data-testid="tweetButtonInline"]').last
    try:
        if btn.count():
            btn.click(timeout=10_000)
        else:
            page.keyboard.press('Control+Enter')
    except Exception:
        page.keyboard.press('Control+Enter')
    human_delay(2, 4)
    # verify: composer should be gone / toast appeared
    try:
        page.wait_for_selector('[data-testid="toast"]', timeout=10_000)
        log("POSTED (toast confirmed)")
        return 0
    except Exception:
        # home inline composer stays in the DOM after posting — check it cleared
        try:
            page.wait_for_function(
                '() => { const el = document.querySelector(\'[data-testid="tweetTextarea_0"]\'); return !el || el.innerText.trim() === ""; }',
                timeout=8_000)
            log("POSTED (composer cleared)")
            return 0
        except Exception:
            log("UNSURE: no toast, composer still has text — manual check needed")
            return 4

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default=None)
    ap.add_argument("--file", default=None)
    ap.add_argument("--image", default=None)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    # Every posting path goes through here, so this is the right place to hold
    # the profile. Without it, a script that shells out to browser_post.py can
    # collide with an engine that already has the profile open, and the loser
    # reports a login failure it did not have (2026-09-11).
    import browser_guard
    if not browser_guard.acquire("browser_post"):
        # Silent on a real post (nothing was attempted); distinct word for the
        # --check path so callers can tell "busy" from "logged out".
        print("PROFILE BUSY" if args.check
              else f"browser_post: standing down, {browser_guard.busy_reason()}")
        return 5
    import atexit
    atexit.register(browser_guard.release)

    if args.check:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(PROFILE, headless=True,
                                                       viewport={"width":1280,"height":900})
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            ok = check_session(page)
            ctx.close()
        print("SESSION OK" if ok else "NOT LOGGED IN")
        return 0 if ok else 2

    if args.file:
        text = open(args.file, encoding="utf-8").read().strip()
    elif args.text:
        text = args.text.strip()
    else:
        text = sys.stdin.read().strip()

    if not text:
        log("ERROR: empty tweet")
        return 4
    if len(text) > 280:
        log(f"ERROR: too long ({len(text)} chars)")
        return 4

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True, viewport={"width":1280,"height":900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not check_session(page):
            log("NOT LOGGED IN — run browser_login.py")
            ctx.close()
            return 2
        rc = post_text(page, text, image_path=args.image)
        ctx.close()
        return rc

if __name__ == "__main__":
    sys.exit(main())
