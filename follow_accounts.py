#!/usr/bin/env python3
"""Follow a curated list of real AI accounts for @first_sauce_lab.

Discovery lever: a new account following 14 accounts is invisible. Following
the real AI ecosystem (labs + the people the reply-guy engine already
engages) puts the profile in front of that audience and unlocks normal X
discovery. NOT follow-for-follow farming: these are accounts the bot
genuinely engages with every day via reply_guy.py.

Invocation:
    Run manually via CLI:
        python3 follow_accounts.py [handle...]
    Defaults to DEFAULT_HANDLES if no arguments are passed. Whether an
    automated scheduler or cron invokes this script is not evident from
    this file.

Inputs and Outputs:
    Reads: browser session cookies from browser_post.PROFILE, optional handle
        arguments from CLI (sys.argv).
    Writes: appends run logs to logs/follows.log, prints to stdout.

Live Account Effects:
    Interacts directly with live X account session: navigates to target user
    profiles, clicks follow buttons and confirmation modals, and sleeps to
    simulate human cadence. Consumes daily follow rate-limit budget on X.
"""
import os, sys, time, random, datetime

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)  # append, never insert: bot dir's queue.py must not shadow stdlib
from playwright.sync_api import sync_playwright
import browser_post

FOLLOW_LOG = os.path.join(BOT, "logs", "follows.log")

DEFAULT_HANDLES = [
    # labs + leaders (same list the reply-guy engine watches)
    "sama", "OpenAI", "AnthropicAI", "GoogleDeepMind", "xai", "elonmusk",
    "karpathy", "gdb", "miramurati", "ilyasut", "kevinweil", "markchen90",
    "jackclarkSF", "janleike", "TobyWalsh", "thsottiaux",
    "ylecun", "simonw", "goodside", "hwchase17", "_philschmid", "AndrewYNg",
    "sundarpichai", "jun_song",
]

def human_delay(a=1.5, b=3.5):
    """Sleep for a randomized duration between a and b seconds.

    Args:
        a: Minimum sleep duration in seconds (default 1.5).
        b: Maximum sleep duration in seconds (default 3.5).

    Side effects:
        Sleeps the executing thread to emulate human pause timing.
    """
    time.sleep(random.uniform(a, b))

def log(line):
    """Write timestamped message to follows log file and stdout.

    Args:
        line: String message to record.

    Side effects:
        Appends to FOLLOW_LOG and prints to stdout.
    """
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with open(FOLLOW_LOG, "a", encoding="utf-8") as f:
        f.write(f"{ts} {line}\n")
    print(f"{ts} {line}")

def main():
    """Follow target handles sequentially via Playwright browser automation.

    Resolves target handles from CLI arguments or DEFAULT_HANDLES. Launches a
    persistent Chromium browser context using browser_post.PROFILE, verifies an
    active authenticated session, navigates to each profile, and clicks the follow
    button in the primary column. Verifies the button state flips to unfollow.

    Returns:
        0 on completion, 2 if browser session is not logged in.

    Side effects:
        Interacts with live X profile pages, clicks follow buttons, sleeps
        between actions, consumes follow rate limits, and appends to FOLLOW_LOG.
    """
    handles = sys.argv[1:] or DEFAULT_HANDLES
    followed, skipped, failed = [], [], []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            browser_post.PROFILE, headless=True,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not browser_post.check_session(page):
            ctx.close()
            log("NOT LOGGED IN — aborting follows")
            return 2
        for h in handles:
            try:
                page.goto(f"https://x.com/{h}", wait_until="domcontentloaded", timeout=60_000)
                human_delay()
                # The header follow button lives in the primary column; the
                # right sidebar ("Who to follow") ALSO renders buttons whose
                # testid ends in "-follow": those are suggestions and must
                # never be clicked. Scope to the primary column.
                btn = page.locator(
                    '[data-testid="primaryColumn"] button[data-testid$="-follow"]').first
                if btn.count() == 0:
                    skipped.append(h)
                    log(f"SKIP {h} (no header follow button / already following)")
                    continue
                btn.click()
                # Verify the flip: the SAME button becomes "-unfollow". Poll up to
                # 8 iterations (~8s total delay) to give the DOM time to update.
                flipped = False
                for _ in range(8):
                    human_delay(0.6, 1.2)
                    if page.locator(
                            '[data-testid="primaryColumn"] button[data-testid$="-unfollow"]').count() > 0:
                        flipped = True
                        break
                if flipped:
                    followed.append(h)
                    log(f"FOLLOWED {h}")
                else:
                    # maybe a confirm sheet appeared (X asks "Follow @x?")
                    sheet = page.locator('[data-testid="confirmationSheetConfirm"]')
                    if sheet.count():
                        sheet.first.click()
                        human_delay(1.0, 2.0)
                        if page.locator(
                                '[data-testid="primaryColumn"] button[data-testid$="-unfollow"]').count() > 0:
                            followed.append(h)
                            log(f"FOLLOWED {h} (via confirm)")
                        else:
                            failed.append(h)
                            log(f"FAIL {h} (confirm did not take)")
                    else:
                        failed.append(h)
                        log(f"FAIL {h} (no flip to Following)")
            except Exception as e:
                failed.append(h)
                log(f"FAIL {h}: {type(e).__name__}: {e}")
        ctx.close()
    log(f"SUMMARY followed={len(followed)} skipped={len(skipped)} failed={len(failed)}")
    if failed:
        log("failed: " + ", ".join(failed))
    return 0

if __name__ == "__main__":
    sys.exit(main())
