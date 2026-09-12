#!/usr/bin/env python3
"""Post the Gemini 3.7 Flash package: main tweet with infographic + chained thread.

One browser context, one session: main image tweet, read its id from the profile,
then each thread part as a reply to the previous id. Logs to posts.log/threads.log.
Exit codes: 0 all posted, 2 not logged in, 3 failure mid-run, 4 bad input.
"""
import os, sys, time, random, argparse, datetime

from playwright.sync_api import sync_playwright

import browser_post
import browser_thread

BOT = browser_post.BOT
PROFILE = browser_post.PROFILE

def log(line, target="posts.log"):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {line}")
    path = os.path.join(BOT, "logs", target)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{ts} {line}\n")

def latest_tweet_id(page, expect_substring, retries=3):
    """Read the newest tweet id from the profile, verifying its text contains a marker."""
    for attempt in range(retries):
        page.goto(f"https://x.com/{browser_thread.USER}", wait_until="domcontentloaded", timeout=60_000)
        time.sleep(random.uniform(2, 3))
        try:
            page.wait_for_selector('article', timeout=20_000)
        except Exception:
            continue
        first = page.locator('article').first
        text = first.inner_text()
        if expect_substring in text:
            href = first.locator('a[href*="/status/"]').first.get_attribute("href")
            if href and "/status/" in href:
                return href.split("/status/")[1].split("?")[0]
        log(f"retry {attempt+1}: newest tweet does not contain marker, got: {text[:80]!r}")
        time.sleep(random.uniform(2, 3))
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", default=None, help="file with main tweet text")
    ap.add_argument("--image", default=None, help="image to attach to main tweet")
    ap.add_argument("--thread", default=None, help="thread parts file (<<<BREAK>>> separated)")
    ap.add_argument("--user", default="first_sauce_lab")
    ap.add_argument("--main-id", default=None,
                    help="skip posting the main tweet; chain thread off this existing id")
    ap.add_argument("--read-main-id", action="store_true",
                    help="print the newest tweet id (with marker check) and exit")
    args = ap.parse_args()

    browser_thread.USER = args.user
    browser_post.USER = args.user  # not used by post_text, harmless

    if args.read_main_id:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                PROFILE, headless=True, viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if not browser_post.check_session(page):
                print("MAIN_ID=NONE (not logged in)")
                ctx.close()
                return 2
            tid = latest_tweet_id(page, "Gemini 3.7 Flash")
            print(f"MAIN_ID={tid}" if tid else "MAIN_ID=NONE")
            ctx.close()
            return 0 if tid else 3

    if not (args.main and args.thread):
        log("ERROR: --main and --thread required", "posts.log")
        return 4

    main_text = open(args.main, encoding="utf-8").read().strip()
    parts = [p.strip() for p in open(args.thread, encoding="utf-8").read().split("<<<BREAK>>>") if p.strip()]
    if not main_text or not parts:
        log("ERROR: empty main or thread", "posts.log")
        return 4
    if len(main_text) > 280:
        log(f"ERROR: main too long ({len(main_text)})", "posts.log")
        return 4
    for i, p in enumerate(parts):
        if len(p) > 280:
            log(f"ERROR: part {i+1} too long ({len(p)})", "posts.log")
            return 4

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True, viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not browser_post.check_session(page):
            log("NOT LOGGED IN — run browser_login.py", "posts.log")
            ctx.close()
            return 2

        if args.read_main_id:
            tid = latest_tweet_id(page, "Gemini 3.7 Flash")
            print(f"MAIN_ID={tid}" if tid else "MAIN_ID=NONE")
            ctx.close()
            return 0 if tid else 3

        if args.main_id:
            main_id = args.main_id
            log(f"resuming thread from existing main id={main_id}", "threads.log")
        else:
            rc = browser_post.post_text(page, main_text, image_path=args.image)
            if rc != 0:
                log(f"MAIN FAILED rc={rc} — nothing posted", "posts.log")
                ctx.close()
                return 3
            log("MAIN POSTED (image attached)", "posts.log")
            main_id = latest_tweet_id(page, "Gemini 3.7 Flash")
            if not main_id:
                log("ERROR: could not read main tweet id — thread NOT posted, main is live", "threads.log")
                ctx.close()
                return 3
            log(f"main id={main_id}", "threads.log")

        last_id = main_id
        for i, part in enumerate(parts):
            tid = browser_thread.post_one(page, part, reply_to=last_id)
            if tid is None:
                log(f"FAILED at part {i+1} of {len(parts)} — thread incomplete", "threads.log")
                ctx.close()
                return 3
            last_id = tid
            log(f"part {i+1}/{len(parts)} posted (id={tid})", "threads.log")
            time.sleep(random.uniform(3, 5))
        ctx.close()

    log(f"ALL POSTED: main + {len(parts)} parts", "posts.log")
    return 0

if __name__ == "__main__":
    sys.exit(main())
