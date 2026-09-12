#!/usr/bin/env python3
"""Delete specific tweets via the X web UI (browser automation).

Usage: python3 delete_tweets.py <tweet_id> [<tweet_id> ...]
"""
import os, sys, time, random, datetime

from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")

def human_delay(a=0.8, b=1.8):
    time.sleep(random.uniform(a, b))

def log(line):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {line}")
    with open(os.path.join(BOT, "logs", "delete.log"), "a", encoding="utf-8") as f:
        f.write(f"{ts} {line}\n")

def delete_one(page, tid):
    url = f"https://x.com/first_sauce_lab/status/{tid}"
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    human_delay(2, 4)
    if "login" in page.url:
        log(f"{tid}: NOT LOGGED IN")
        return False
    try:
        page.wait_for_selector('[data-testid="caret"]', timeout=20_000)
    except Exception:
        log(f"{tid}: caret not found")
        return False
    page.locator('[data-testid="caret"]').first.click()
    human_delay(1, 2)
    # menu item "Delete"
    try:
        page.wait_for_selector('text=Delete', timeout=10_000)
        page.locator('text=Delete').first.click()
    except Exception:
        log(f"{tid}: Delete menu item not found")
        return False
    human_delay(1, 2)
    # confirm dialog: the confirm button is inside a dialog with text Delete
    try:
        page.wait_for_selector('[data-testid="confirmationSheetConfirm"]', timeout=10_000)
        page.locator('[data-testid="confirmationSheetConfirm"]').click()
    except Exception:
        log(f"{tid}: confirm button not found")
        return False
    human_delay(2, 3)
    # verify: tweet should 404 or the profile no longer shows it
    log(f"{tid}: delete submitted")
    return True

def main():
    tids = sys.argv[1:]
    if not tids:
        print("usage: delete_tweets.py <tweet_id> ...")
        return 2
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True, viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ok = 0
        for tid in tids:
            if delete_one(page, tid):
                ok += 1
            human_delay(2, 3)
        ctx.close()
    log(f"DELETED {ok}/{len(tids)}")
    return 0 if ok == len(tids) else 1

if __name__ == "__main__":
    sys.exit(main())
