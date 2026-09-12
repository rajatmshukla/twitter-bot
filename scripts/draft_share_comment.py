#!/usr/bin/env python3
"""Draft first-sauce comments for one quote-post target. Posts NOTHING.

Uses the same voice rules and the same hygiene filter as the news share lane
(news_monitor.clean_comment), so what you approve here is what the lane would
accept.

  python3 scripts/draft_share_comment.py --url <status_url>
"""
import argparse, json, os, sys, urllib.request

BOT = r"C:\Users\Rajat\twitter-bot"
SCRIPTS = os.path.join(os.environ["LOCALAPPDATA"], "hermes", "scripts")
sys.path.append(BOT)
sys.path.append(SCRIPTS)

import reply_guy as rg
import browser_guard
from playwright.sync_api import sync_playwright

SHARE_TASK = """You are writing ONE line for @first_sauce_lab to post as a QUOTE TWEET of the post below.

The account is one real person who ships and breaks technology for a living. Dry, specific, unimpressed by marketing. Never a hype man.

Rules:
- One line. Max 180 characters. Sentence case.
- Take a POSITION on the thing quoted. Add the thing the post does not say. Never summarize it back.
- Ground every claim in the quoted post itself. Do not add facts, numbers, dates, versions or comparisons it does not contain. No predictions dressed as facts.
- No em dashes or en dashes. No hashtags. No links. No @handles. No "this". No questions.
- Never open with: "Interesting", "This is huge", "Love this", "Great", "So", "Here's why".
- No corporate words: game-changer, revolutionize, unlock, empower, seamless, leverage, landscape.
- Output the single line only. Nothing else.

QUOTED POST by @{handle}:
{text}
"""


def draft(handle, text, n=3):
    """Query DeepSeek to generate quote comments and return those passing news_monitor hygiene."""
    import news_monitor as nm
    out = []
    for i in range(n):
        prompt = SHARE_TASK.format(handle=handle, text=text[:800])
        raw = nm._deepseek(prompt, max_tokens=300)
        if not raw:
            print(f"  attempt {i+1}: no model output")
            continue
        c = nm.clean_comment(raw)
        if not c:
            print(f"  attempt {i+1}: rejected by hygiene filter -> {raw[:120]!r}")
            continue
        out.append(c)
    return out


def main():
    """Scrape a tweet and draft quote-tweet candidate comments without posting.

    Runs against a target tweet URL using a logged-in Playwright session under browser guard.
    Generates n comment variations via DeepSeek and filters them through news hygiene checks.
    Prints comment options to stdout and saves them to tmp/share_comment_options.json.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--n", type=int, default=3)
    a = ap.parse_args()

    parts = a.url.rstrip("/").split("/")
    handle = parts[-3]
    tid = parts[-1]

    if not browser_guard.hold("draft_share_comment"):
        print(f"profile busy: {browser_guard.busy_reason()}")
        return 0

    text = ""
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            rg.PROFILE, headless=True, viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        verdict, detail = browser_guard.classify_session(page)
        print(f"session: {verdict}")
        if verdict != "ok":
            ctx.close()
            return 2
        page.goto(f"https://x.com/{handle}/status/{tid}",
                  wait_until="domcontentloaded", timeout=60_000)
        rg.human_delay(3, 5)
        arts = page.locator('article[data-testid="tweet"]')
        for i in range(min(arts.count(), 4)):
            t = arts.nth(i).locator('[data-testid="tweetText"]')
            if t.count():
                cand = t.first.inner_text().strip()
                if cand and len(cand) > 20:
                    text = cand
                    break
        ctx.close()

    if not text:
        print("could not read the quoted post text")
        return 1

    print(f"\ntarget: @{handle}  {a.url}")
    print(f"quoted text:\n{text}\n")
    print("=== comment options (nothing posted) ===")
    opts = draft(handle, text, a.n)
    for i, c in enumerate(opts, 1):
        print(f"\n[{i}] ({len(c)} chars)\n{c}")
    if not opts:
        print("no usable comment produced")
        return 1

    json.dump({"handle": handle, "id": tid, "url": a.url, "text": text,
               "options": opts},
              open(os.path.join(BOT, "tmp", "share_comment_options.json"), "w",
                   encoding="utf-8"), indent=1)
    print("\nwrote tmp/share_comment_options.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
