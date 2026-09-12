#!/usr/bin/env python3
"""Follow a curated list of real AI accounts for @first_sauce_lab.

Discovery lever: a new account following 14 accounts is invisible. Following
the real AI ecosystem (labs + the people the reply-guy engine already
engages) puts the profile in front of that audience and unlocks normal X
discovery. NOT follow-for-follow farming — these are accounts the bot
genuinely engages with every day via reply_guy.py.

Usage: python3 follow_accounts.py [handle...]  (defaults to the reply-guy list)
Run with python3 (WindowsApps). Logs to logs/follows.log.
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
    time.sleep(random.uniform(a, b))

def log(line):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with open(FOLLOW_LOG, "a", encoding="utf-8") as f:
        f.write(f"{ts} {line}\n")
    print(f"{ts} {line}")

def main():
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
                # testid ends in "-follow" — those are suggestions and must
                # never be clicked. Scope to the primary column.
                btn = page.locator(
                    '[data-testid="primaryColumn"] button[data-testid$="-follow"]').first
                if btn.count() == 0:
                    skipped.append(h)
                    log(f"SKIP {h} (no header follow button / already following)")
                    continue
                btn.click()
                # verify the flip: the SAME button becomes "-unfollow"
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
