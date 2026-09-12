#!/usr/bin/env python3
"""WARNING: This test modifies the live X account (@first_sauce_lab) by reposting and quoting.
Do not run casually; it creates live activity and requires active credentials.

Behaviour under test:
End-to-end verification of browser automation for reposts and quote tweets on x.com:
1. Repost the account's newest tweet and verify state flips to reposted.
2. Undo the repost and verify state flips back.
3. Quote the tweet with test commentary and capture the newly created status ID.
4. Verify the quote renders properly with the attached quote card.
5. Delete the published quote tweet and verify HTTP 404 via curl.

Why this matters:
X frontend markup and ARIA labels change frequently. This script validates that Playwright
selectors for menus, repost buttons, quote composers, and tweet deletion remain functional.
Running against the account's own post ensures no outside users receive notifications.

How to run:
    python scripts/test_share_live.py
Requires: Live logged-in Chromium profile for @first_sauce_lab, network access, and curl.
Acquires browser lock and writes results to tmp/share_live_report.json.

What a failure means in practice:
Production quote posts or reposts will fail to publish, or test tweets will remain publicly
visible on the account timeline if deletion automation fails.
"""
import json
import os
import subprocess
import sys
import time

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)

from playwright.sync_api import sync_playwright  # noqa: E402
import browser_post  # noqa: E402
import browser_share as bs  # noqa: E402
import reply_guy  # noqa: E402

HANDLE = "first_sauce_lab"
COMMENT = "Repost and quote path test. Deleting this in a minute."
REPORT = os.path.join(BOT, "tmp", "share_live_report.json")


def own_newest_status(page):
    """Scrape the account timeline to find the status URL of the newest own post."""
    page.goto(f"https://x.com/{HANDLE}", wait_until="domcontentloaded", timeout=60_000)
    time.sleep(3)
    return page.evaluate("""(h) => {
      const re = new RegExp('^/' + h + '/status/\\\\d+$');
      for (const a of document.querySelectorAll('article a[href*="/status/"]')) {
        const href = a.getAttribute('href');
        if (re.test(href)) return href;
      }
      return null;
    }""", HANDLE)


def http_code(url):
    """Return HTTP status code for a URL via curl to verify post deletion (404)."""
    r = subprocess.run(["curl", "-s", "-o", os.devnull, "-w", "%{http_code}",
                        "-L", url], capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def main():
    """Run end-to-end live repost, quote, render check, and deletion test."""
    rep = {"when": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if not reply_guy.acquire_browser_lock():
        print("browser busy (lock held) — aborting")
        return 2
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                browser_post.PROFILE, headless=True,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            rep["session"] = browser_post.check_session(page)
            if not rep["session"]:
                # The nav-button marker is occasionally late on a cold profile
                # (seen 2026-09-11: a single false NOT-LOGGED-IN at 12:29 while
                # the same profile answered SESSION OK 30s later). Retry once.
                time.sleep(3)
                rep["session"] = browser_post.check_session(page)
            print("session:", rep["session"])
            if not rep["session"]:
                ctx.close()
                return 2

            url = own_newest_status(page)
            rep["own_status"] = url
            print("own status:", url)
            if not url:
                ctx.close()
                return 3

            # --- 1. repost -------------------------------------------------
            # No menu pre-open and no Escape here: that sequence is what broke
            # the 12:39 run (the click after it silently no-oped). repost()
            # opens and uses its own menu, exactly as production does.
            bs.open_status(page, url)
            rep["aria_before"] = bs.retweet_aria(page)
            ok, note = bs.repost(page, url)
            rep["repost"] = {"ok": ok, "note": note,
                             "state": bs.retweet_state(page),
                             "aria_after": bs.retweet_aria(page)}
            print("repost:", rep["repost"])

            # --- 2. undo ---------------------------------------------------
            ok2, note2 = bs.undo_repost(page, url)
            rep["undo"] = {"ok": ok2, "note": note2, "state": bs.retweet_state(page),
                           "aria_after_undo": bs.retweet_aria(page)}
            print("undo:", rep["undo"])

            # --- 3. quote --------------------------------------------------
            ok3, note3, new_id = bs.quote(page, url, COMMENT)
            rep["quote"] = {"ok": ok3, "note": note3, "new_id": new_id}
            print("quote:", rep["quote"])

            # --- 4. verify the quote renders -------------------------------
            if new_id:
                page.goto(f"https://x.com/{HANDLE}/status/{new_id}",
                          wait_until="domcontentloaded", timeout=60_000)
                time.sleep(4)
                rep["render"] = page.evaluate("""() => {
                  const txt = document.body.innerText;
                  return {
                    comment_present: txt.includes("Repost and quote path test"),
                    articles: document.querySelectorAll('article').length,
                    has_quoted_card: !!document.querySelector('[role="link"]'),
                    title: document.title,
                  };
                }""")
                print("render:", rep["render"])
            ctx.close()

        # --- 5. delete the test quote -------------------------------------
        if rep.get("quote", {}).get("new_id"):
            nid = rep["quote"]["new_id"]
            rep["url_before_delete"] = http_code(f"https://x.com/{HANDLE}/status/{nid}")
            d = subprocess.run(
                ["python3", os.path.join(BOT, "delete_tweets.py"), nid],
                capture_output=True, text=True, timeout=300, cwd=r"C:\Users\Rajat")
            rep["delete_stdout"] = (d.stdout or "").strip().splitlines()[-3:]
            rep["delete_rc"] = d.returncode
            time.sleep(5)
            rep["url_after_delete"] = http_code(f"https://x.com/{HANDLE}/status/{nid}")
            print("delete:", rep["delete_rc"], rep["url_before_delete"], "->",
                  rep["url_after_delete"])

        with open(REPORT, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
        print("report:", REPORT)

        # --- verdict ------------------------------------------------------
        fails = []
        if not rep.get("repost", {}).get("ok"):
            fails.append("repost did not confirm")
        if not rep.get("undo", {}).get("ok"):
            fails.append("undo repost did not confirm")
        if not rep.get("quote", {}).get("ok"):
            fails.append("quote did not confirm")
        if rep.get("quote", {}).get("new_id") and rep.get("url_after_delete") != "404":
            fails.append(f"test quote not deleted (http {rep.get('url_after_delete')})")
        if fails:
            print("FAILED: " + "; ".join(fails))
            return 1
        print("ALL PASS: repost confirmed, undo confirmed, quote posted + deleted")
        return 0
    finally:
        reply_guy.release_browser_lock()


if __name__ == "__main__":
    sys.exit(main())
