#!/usr/bin/env python3
"""Post ONE quote-post for real, then verify it landed. Not a test.

  python3 scripts/post_share_now.py --url <status_url> --comment "one line"
  python3 scripts/post_share_now.py --url ... --comment ... --dry

--dry exercises the composer and stops before Post.
"""
import argparse, json, os, sys, time

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
import reply_guy as rg
import browser_share
import browser_guard
from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--comment", required=True)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    if not browser_guard.hold("post_share_now"):
        print(f"profile busy: {browser_guard.busy_reason()}")
        return 0

    comment = a.comment.strip()
    print(f"target  : {a.url}")
    print(f"comment : ({len(comment)} chars) {comment}")

    result = None
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            rg.PROFILE, headless=True, viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        verdict, detail = browser_guard.classify_session(page)
        print(f"session : {verdict}")
        if verdict != "ok":
            ctx.close()
            return 2
        result = browser_share.quote(page, a.url, comment, dry=a.dry)
        print(f"result  : {result!r}")
        # quote() returns (ok, note, new_id); tolerate a dict if that changes.
        if isinstance(result, dict):
            ok = result.get("ok")
            note = result.get("note")
            new_id = result.get("new_id")
        else:
            parts = list(result) + [None, None, None]
            ok, note, new_id = parts[0], parts[1], parts[2]
        if not ok:
            print(f"NOT POSTED: {note}")

        if new_id and not a.dry:
            time.sleep(4)
            # Verify from the account's own profile, not the composer.
            page.goto(f"https://x.com/first_sauce_lab/status/{new_id}",
                      wait_until="domcontentloaded", timeout=60_000)
            rg.human_delay(3, 5)
            arts = page.locator('article[data-testid="tweet"]')
            seen_text, quoted = "", False
            for i in range(min(arts.count(), 3)):
                art = arts.nth(i)
                t = art.locator('[data-testid="tweetText"]')
                if t.count() and not seen_text:
                    seen_text = t.first.inner_text().strip()
                if art.locator('[data-testid="tweet"]').count() or \
                   art.locator('div[role="link"] a[href*="/status/"]').count():
                    quoted = True
            print(f"verify  : comment_present={bool(seen_text)} "
                  f"quoted_card={quoted}")
            print(f"verify  : rendered text = {seen_text[:200]!r}")
            print(f"permalink: https://x.com/first_sauce_lab/status/{new_id}")
        ctx.close()

    json.dump({"url": a.url, "comment": comment, "dry": a.dry,
               "result": result,
               "when": time.strftime("%Y-%m-%dT%H:%M:%S")},
              open(os.path.join(BOT, "tmp", "share_posted.json"), "w",
                   encoding="utf-8"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
