#!/usr/bin/env python3
"""Post a threaded post (tweet thread) via the X web UI (browser automation).

Entered as a CLI script:
  python3 browser_thread.py --file thread.txt    -> post thread from file
  python3 browser_thread.py --check              -> verify login session
Also imported as a library by reply_guy, reply_guy_direct, mentions_guy, and
thread_engine for its post_one subroutine.

Thread file format: plain text, tweet parts separated by a line containing
exactly <<<BREAK>>>. Example:

  part one of the thread.
  <<<BREAK>>>
  part two, a reply to part one.
  <<<BREAK>>>
  part three.

Side effects:
- Launches Chromium browser with persistent profile under browser-profile/.
- Navigates and submits form data on X web endpoints.
- Appends execution records to logs/threads.log.
- Saves diagnostic screenshots to logs/unsure_*.png on unconfirmed submissions.
- Publishes tweets, reply threads, or standalone updates to X.

Exit codes: 0 posted fully, 2 not logged in, 3 blocked/error, 4 file issue
"""
import os, sys, time, random, argparse, datetime

from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")

def human_delay(a=1.0, b=2.5):
    """Pause execution for a random duration between a and b seconds."""
    time.sleep(random.uniform(a, b))

def log(line):
    """Append timestamped message to threads.log and print to stdout."""
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {line}")
    with open(os.path.join(BOT, "logs", "threads.log"), "a", encoding="utf-8") as f:
        f.write(f"{ts} {line}\n")

def check_session(page):
    """Delegate to browser_guard: one implementation, not a fourth copy."""
    import browser_guard
    return browser_guard.check_session(page)

def composer_box(page):
    """Return the composer textarea locator (dialog-first)."""
    dialog = page.locator('[role="dialog"]')
    if dialog.count():
        return dialog.locator('[data-testid="tweetTextarea_0"]').first
    return page.locator('[data-testid="tweetTextarea_0"]').last

def post_button(page):
    """Return locator for the post submit button (dialog-first)."""
    dialog = page.locator('[role="dialog"]')
    if dialog.count():
        return dialog.locator('[data-testid="tweetButton"]')
    return page.locator('[data-testid="tweetButton"]').last

def post_one(page, text, reply_to=None):
    """Post a single tweet; reply_to = tweet id to reply to (thread)."""
    if reply_to:
        page.goto(f"https://x.com/{USER}/status/{reply_to}", wait_until="domcontentloaded", timeout=60_000)
        human_delay(2, 4)
        try:
            page.wait_for_selector('[data-testid="reply"]', timeout=15_000)
            page.locator('[data-testid="reply"]').first.click()
        except Exception:
            log("ERROR: reply button not found")
            return None
        human_delay(1, 2)
    else:
        page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=60_000)
        human_delay(2, 4)
    if "login" in page.url:
        return None
    try:
        page.wait_for_selector('[data-testid="tweetTextarea_0"]', timeout=20_000)
    except Exception:
        log("ERROR: composer textarea not found")
        return None
    box = composer_box(page)
    box.click()
    human_delay(0.4, 1.0)
    box.type(text, delay=random.randint(25, 60))
    human_delay(0.8, 1.8)
    btn = post_button(page)
    if not btn.count():
        log("ERROR: Post button not found")
        return None
    btn.click()
    def _poll_confirm(max_s):
        """Poll for post completion via toast link or cleared composer for up to max_s seconds.

        Returns (confirmed, tweet_id, signal) tuple where confirmed is True on success.
        """
        start = time.time()
        while time.time() - start < max_s:
            try:
                view = page.locator('[data-testid="toast"] a[href*="/status/"]').first
                if view.count():
                    href = view.get_attribute("href")
                    if href and "/status/" in href:
                        t = href.split("/status/")[1].split("?")[0]
                        return True, t, "toast"
            except Exception:
                pass
            try:
                cleared = page.evaluate('''() => {
                    const dialog = document.querySelector('[role="dialog"]');
                    let el = dialog ? dialog.querySelector('[data-testid="tweetTextarea_0"]') : null;
                    if (!el) {
                        const els = document.querySelectorAll('[data-testid="tweetTextarea_0"]');
                        el = els.length ? els[els.length - 1] : null;
                    }
                    return !el || el.innerText.trim() === "";
                }''')
                if cleared:
                    return True, None, "composer cleared"
            except Exception:
                pass
            time.sleep(0.4)
        return False, None, None

    def _composer_text(page):
        """Whatever the composer now holds. The retry below must not resubmit a
        tweet that already went out: an empty composer means it did."""
        try:
            return page.evaluate('''() => {
                const dialog = document.querySelector('[role="dialog"]');
                let el = dialog ? dialog.querySelector('[data-testid="tweetTextarea_0"]') : null;
                if (!el) {
                    const els = document.querySelectorAll('[data-testid="tweetTextarea_0"]');
                    el = els.length ? els[els.length - 1] : null;
                }
                return el ? el.innerText.trim() : "";
            }''')
        except Exception:
            return ""

    confirmed, tid, signal = _poll_confirm(20)
    if confirmed:
        log(f"POSTED ({signal})")
    elif _composer_text(page) == "":
        # the last poll tick may have raced the clear; never resubmit on this
        confirmed, signal = True, "composer cleared (late)"
        log(f"POSTED ({signal})")
    else:
        log("RETRY: post not confirmed, resubmitting once")
        btn = post_button(page)
        clicked = False
        try:
            if btn.count() and btn.get_attribute("aria-disabled") != "true":
                btn.click()
                clicked = True
        except Exception:
            pass
        if not clicked:
            try:
                page.keyboard.press("Control+Enter")
            except Exception:
                pass
        confirmed, tid, signal = _poll_confirm(15)
        if confirmed:
            log("POSTED (retried)")

    if not confirmed:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        shot_path = os.path.join(BOT, "logs", f"unsure_{stamp}.png")
        try:
            os.makedirs(os.path.join(BOT, "logs"), exist_ok=True)
            page.screenshot(path=shot_path)
        except Exception:
            pass
        log("UNSURE: no toast, no composer clear — aborting (screenshot saved)")
        return None
    if tid is None:
        human_delay(2, 3)
        page.goto(f"https://x.com/{USER}", wait_until="domcontentloaded", timeout=60_000)
        human_delay(2, 3)
        try:
            page.wait_for_selector('a[href*="/status/"]', timeout=15_000)
            href = page.locator('a[href*="/status/"]').first.get_attribute("href")
            tid = href.split("/status/")[1].split("?")[0]
        except Exception:
            log("WARNING: could not read tweet id from profile")
            return "unknown"
    return tid

def main():
    """Parse CLI arguments, verify session or post thread from file."""
    global USER
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=None)
    ap.add_argument("--user", default="first_sauce_lab")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    USER = args.user

    if args.check:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(PROFILE, headless=True,
                                                       viewport={"width":1280,"height":900})
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            ok = check_session(page)
            ctx.close()
        print("SESSION OK" if ok else "NOT LOGGED IN")
        return 0 if ok else 2

    if not args.file or not os.path.exists(args.file):
        log(f"ERROR: no thread file: {args.file}")
        return 4
    raw = open(args.file, encoding="utf-8").read().strip()
    parts = [p.strip() for p in raw.split("<<<BREAK>>>") if p.strip()]
    if not parts:
        log("ERROR: empty thread")
        return 4
    for i, p in enumerate(parts):
        if len(p) > 280:
            log(f"ERROR: part {i+1} too long ({len(p)} chars)")
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
        posted = 0
        last_id = None
        for i, part in enumerate(parts):
            tid = post_one(page, part, reply_to=last_id)
            if tid is None:
                log(f"FAILED at part {i+1} of {len(parts)} — thread incomplete")
                ctx.close()
                return 3
            posted += 1
            last_id = tid
            log(f"part {i+1}/{len(parts)} posted (id={tid})")
            human_delay(3, 5)
        ctx.close()

    log(f"THREAD POSTED: {posted} parts")
    return 0

if __name__ == "__main__":
    sys.exit(main())
