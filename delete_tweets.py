#!/usr/bin/env python3
"""Delete specific tweets via the X web UI (browser automation).

Navigates to target tweet permalinks for @first_sauce_lab, opens the tweet
caret options menu, clicks the 'Delete' action, and confirms the deletion dialog.

Invocation:
    Run manually via CLI:
        python3 delete_tweets.py <tweet_id> [<tweet_id> ...]
    Whether an automated scheduler or pipeline invokes this script is not evident
    from this file.

Inputs and Outputs:
    Reads:
        - Tweet ID arguments from command line (sys.argv).
        - Persistent browser cookies and session state from browser-profile.
        - Tweet page DOM elements on x.com.
    Writes:
        - Appends deletion results and timestamps to logs/delete.log.
        - Prints status messages to stdout.

Live Account Effects:
    Permanently deletes tweets from the live @first_sauce_lab account by clicking
    through the web UI delete confirmation flow. Pauses with randomized delays
    between interactions. Does not acquire browser_guard lock.
"""
import os, sys, time, random, datetime

from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")

def human_delay(a=0.8, b=1.8):
    """Sleep for a randomized duration between a and b seconds.

    Args:
        a: Minimum sleep duration in seconds (default 0.8).
        b: Maximum sleep duration in seconds (default 1.8).

    Side effects:
        Sleeps the executing thread to emulate human reaction timing.
    """
    time.sleep(random.uniform(a, b))

def log(line):
    """Write timestamped log message to stdout and delete log file.

    Args:
        line: Message string to log.

    Side effects:
        Prints to stdout and appends timestamped line to logs/delete.log.
    """
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {line}")
    with open(os.path.join(BOT, "logs", "delete.log"), "a", encoding="utf-8") as f:
        f.write(f"{ts} {line}\n")

def delete_one(page, tid):
    """Delete a single tweet through the X web interface.

    Loads the tweet status URL, clicks the tweet caret menu, selects the
    'Delete' option, and confirms the deletion modal.

    Args:
        page: Playwright Page instance with authenticated browser session.
        tid: Tweet status ID string.

    Returns:
        True if the deletion flow completed without error, False otherwise.

    Side effects:
        Navigates page to tweet URL, clicks live UI elements to permanently
        delete the tweet, sleeps between interactions, and appends to delete log.
    """
    url = f"https://x.com/first_sauce_lab/status/{tid}"
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    # Wait 2-4 seconds for the tweet article and action caret to render.
    human_delay(2, 4)
    if "login" in page.url:
        log(f"{tid}: NOT LOGGED IN")
        return False
    try:
        page.wait_for_selector('[data-testid="caret"]', timeout=20_000)
    except Exception:
        log(f"{tid}: caret not found")
        return False
    # Click caret menu button at top right of the tweet article.
    page.locator('[data-testid="caret"]').first.click()
    human_delay(1, 2)
    # Menu item "Delete"
    try:
        page.wait_for_selector('text=Delete', timeout=10_000)
        # Click Delete option from the dropdown menu.
        page.locator('text=Delete').first.click()
    except Exception:
        log(f"{tid}: Delete menu item not found")
        return False
    human_delay(1, 2)
    # Confirm dialog: the confirm button is inside a dialog with text Delete.
    try:
        page.wait_for_selector('[data-testid="confirmationSheetConfirm"]', timeout=10_000)
        # Confirm permanent deletion in the confirmation modal sheet.
        page.locator('[data-testid="confirmationSheetConfirm"]').click()
    except Exception:
        log(f"{tid}: confirm button not found")
        return False
    # Wait 2-3s for deletion API request to complete before proceeding.
    human_delay(2, 3)
    # Note: deletion was submitted; code does not verify 404 response.
    log(f"{tid}: delete submitted")
    return True

def main():
    """Parse tweet IDs from CLI and execute deletions sequentially.

    Launches persistent Playwright browser context, processes each tweet ID
    passed via sys.argv, logs individual outcomes, and reports a final summary.

    Returns:
        0 if all specified tweets were successfully submitted for deletion.
        1 if any tweet deletion failed.
        2 if no tweet IDs were provided in command-line arguments.

    Side effects:
        Opens persistent Chromium context, permanently deletes tweets via
        delete_one, pauses between deletion requests, and appends to delete log.
    """
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
            # Pause 2-3s between consecutive tweet deletions to pace web requests.
            human_delay(2, 3)
        ctx.close()
    log(f"DELETED {ok}/{len(tids)}")
    return 0 if ok == len(tids) else 1

if __name__ == "__main__":
    sys.exit(main())
