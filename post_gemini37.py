#!/usr/bin/env python3
"""Publish the Gemini 3.7 Flash package: media main tweet plus chained thread.

Automates the publishing of an announcement package using a single persistent
Playwright Chromium browser session. Posts the main tweet with an attached image,
resolves its status ID from the account profile timeline, and sequentially
posts each thread segment as a reply to the preceding tweet.

Invocation:
- CLI:
    python post_gemini37.py --main <main.txt> --thread <thread.txt> --image <img.png>
    python post_gemini37.py --main-id <id> --thread <thread.txt>
    python post_gemini37.py --read-main-id
- Typically invoked manually by an operator or called by campaign launch scripts.

Inputs / Reads:
- Main tweet text file (--main).
- Thread parts file (--thread) with segments delimited by '<<<BREAK>>>'.
- Image file path (--image).
- Browser session data in browser-profile/.
- Account profile timeline on X to scrape published tweet IDs.

Outputs / Writes:
- Appends operational records to logs/posts.log and logs/threads.log.
- Mutates browser profile cache and cookies in browser-profile/.

Live X Account Impact:
- Publishes a public main tweet with image attachment.
- Publishes multiple public reply tweets forming a connected thread.
- If thread posting fails halfway through, earlier parts remain live on X.

Exit codes:
- 0: All tweets and thread parts successfully published.
- 2: Browser session is not logged in.
- 3: Failure mid-run (main post failed, tweet ID lookup failed, or reply failed).
- 4: Bad or missing command-line arguments / text validation error.
"""
import os, sys, time, random, argparse, datetime

from playwright.sync_api import sync_playwright

import browser_post
import browser_thread

BOT = browser_post.BOT
PROFILE = browser_post.PROFILE

def log(line, target="posts.log"):
    """Format and append a timestamped log entry to file and stdout.

    Args:
        line: Message string to log.
        target: Filename inside logs/ directory (defaults to 'posts.log').
    Side effects:
        Prints to standard output and appends line to logs/<target>.
    """
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {line}")
    path = os.path.join(BOT, "logs", target)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{ts} {line}\n")

def latest_tweet_id(page, expect_substring, retries=3):
    """Retrieve newest tweet status ID from user profile matching a text marker.

    Loads the user profile page and checks the newest article element. If the
    tweet text contains expect_substring, extracts and returns the status ID
    from the article status link. Retries up to retries times with random backoff.

    Args:
        page: Playwright Page instance with active session.
        expect_substring: Text marker required in the tweet body to confirm match.
        retries: Maximum number of page reloads to attempt (defaults to 3).
    Returns:
        Numeric status ID string if found and confirmed, else None.
    Side effects:
        Navigates browser to user profile URL and sleeps 2 to 3 seconds per attempt.
    """
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
    """CLI entry point to publish an image tweet and chained reply thread.

    Validates character limits (<= 280 chars per part), launches headless
    Chromium with persistent profile, posts main tweet with optional image,
    reads back its ID from the timeline, and chains thread parts as replies.

    Returns:
        Exit code: 0 on success, 2 not logged in, 3 failure mid-run, 4 bad input.
    Side effects:
        Publishes multiple live tweets and media to X. Modifies account state.
        Sleeps 3 to 5 seconds between thread parts to avoid rate limiting.
    """
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
