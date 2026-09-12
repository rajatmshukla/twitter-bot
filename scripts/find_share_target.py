#!/usr/bin/env python3
"""Collect live quote-post candidates from the share allowlist.

Reads the recent own-post from each allowlisted account and keeps the fresh
ones, so a real quote-post can be aimed at a real news post instead of
whatever happened to be in a search cache.

Read-only: scrapes, prints, posts nothing.

  python3 scripts/find_share_target.py [--hours N] [--limit N]
"""
import argparse, datetime, json, os, sys

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
import reply_guy as rg
import browser_guard
from playwright.sync_api import sync_playwright

# High-signal announcement accounts first: a quote of these reads as news.
HANDLES = [
    "OpenAI", "AnthropicAI", "GoogleDeepMind", "huggingface", "nvidia",
    "deepseek_ai", "Alibaba_Qwen", "MistralAI", "AIatMeta",
    "arstechnica", "TechCrunch", "TheVerge", "WiredUK", "engadget",
    "theneuron", "simonw",
]


def age_hours(dt):
    try:
        t = datetime.datetime.fromisoformat(dt.replace("Z", "+00:00"))
        return (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds() / 3600
    except Exception:
        return None


def main():
    """Scrape allowlisted accounts via headless Chromium to find fresh quote-post candidates.

    Drives Playwright across target profile pages, filters posts by age threshold, and prints
    the top candidates to stdout. Writes all collected candidate records to tmp/share_targets.json.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=72)
    ap.add_argument("--limit", type=int, default=6)
    a = ap.parse_args()

    if not browser_guard.hold("find_share_target"):
        print(f"profile busy: {browser_guard.busy_reason()}")
        return 0

    out = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            rg.PROFILE, headless=True, viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        verdict, detail = browser_guard.classify_session(page)
        print(f"session: {verdict} {detail}")
        if verdict != "ok":
            ctx.close()
            return 2
        for h in HANDLES:
            try:
                tw = rg.scrape_profile(page, h)
            except Exception as e:
                print(f"  {h}: scrape failed {type(e).__name__}")
                continue
            for t in tw[:2]:
                ah = age_hours(t.get("time") or "")
                if ah is None or ah > a.hours or not t["text"].strip():
                    continue
                out.append({"handle": h, "id": t["id"], "age_h": round(ah, 1),
                            "text": t["text"].strip(),
                            "url": f"https://x.com/{h}/status/{t['id']}"})
            rg.human_delay(1.2, 2.4)
        ctx.close()

    out.sort(key=lambda x: x["age_h"])
    print(f"\n=== {len(out)} fresh candidates (<= {a.hours}h, newest first) ===")
    for c in out[:12]:
        print(f"\n@ {c['handle']}  {c['age_h']}h  {c['url']}")
        print(f"   {c['text'][:220]}")

    path = os.path.join(BOT, "tmp", "share_targets.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"), indent=1)
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
