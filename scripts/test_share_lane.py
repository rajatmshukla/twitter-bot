#!/usr/bin/env python3
"""Checks for the news share lane (2026-09-11).

Part 1 (offline): the 10% budget, comment hygiene, query extraction, freshness.
Part 2 (live, publishes NOTHING): take a real story off the feeds, find the
source's own X post, generate the comment, open the quote composer and stop one
click short of posting (browser_share.quote(dry=True)).

Run: python3 scripts/test_share_lane.py           # both parts
     python3 scripts/test_share_lane.py offline   # no browser
"""
import os
import sys

sys.path.append(r"C:\Users\Rajat\AppData\Local\hermes\scripts")
sys.path.append(r"C:\Users\Rajat\twitter-bot")

import news_monitor as nm  # noqa: E402
import reply_guy  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name}: got {got!r} want {want!r}")
    if not ok:
        fails.append(name)


def offline():
    print("== 10% share budget ==")
    # The first share needs 9 plain posts behind it, then 19, then 29.
    check("0 posts, 0 shares -> no share", nm.share_allowed({}), False)
    check("8 posts, 0 shares -> no share", nm.share_allowed({"news_posts": 8}), False)
    check("9 posts, 0 shares -> share", nm.share_allowed({"news_posts": 9}), True)
    check("10 posts, 1 share -> no share",
          nm.share_allowed({"news_posts": 10, "shares": 1}), False)
    check("19 posts, 1 share -> share",
          nm.share_allowed({"news_posts": 19, "shares": 1}), True)
    check("20 posts, 2 shares -> no share",
          nm.share_allowed({"news_posts": 20, "shares": 2}), False)
    check("29 posts, 2 shares -> share",
          nm.share_allowed({"news_posts": 29, "shares": 2}), True)
    # never more than 10% at any size: simulate posts accruing one at a time
    # exactly as production does (check the budget, then post).
    shares = 0
    worst = 0.0
    for posts in range(1, 400):
        if nm.share_allowed({"news_posts": posts - 1, "shares": shares}):
            shares += 1
        worst = max(worst, shares / posts)
    check("ratio never exceeds 10%", worst <= 0.10, True)

    print("\n== comment hygiene ==")
    c = nm.clean_comment("the api got cheaper — my bill did not")
    check("em dash removed from comment", "—" in (c or ""), False)
    check("long comment trimmed to cap",
          len(nm.clean_comment("x" * 400 + " and some real words here")), nm.SHARE_COMMENT_MAX)
    check("hashtag rejected", nm.clean_comment("shipping fast #ai #agents today"), None)
    check("url rejected", nm.clean_comment("read this at https://example.com/x now"), None)
    check("too short rejected", nm.clean_comment("nice"), None)
    check("newline flattened to one line",
          nm.clean_comment("first line is the real comment\nsecond line should never ship"),
          "first line is the real comment")
    check("plain comment passes",
          nm.clean_comment("the free tier is the whole product, the paid one is the receipt."),
          "the free tier is the whole product, the paid one is the receipt.")

    print("\n== search query extraction ==")
    q1 = nm.news_query("OpenAI brings back Paul Christiano to its board")
    print(f"   -> {q1!r}")
    check("query is not empty", bool(q1), True)
    q2 = nm.news_query("Anthropic details distillation campaigns from Alibaba, Moonshot AI, and DeepSeek")
    print(f"   -> {q2!r}")
    check("tech headline yields a query", bool(q2), True)
    check("query drops stopwords", "the" in q2.split(), False)

    print("\n== source freshness window ==")
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    fresh = (now - datetime.timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    stale = (now - datetime.timedelta(days=9)).isoformat().replace("+00:00", "Z")
    check("5h old source ok", nm._fresh_enough(fresh), True)
    check("9 day old source rejected", nm._fresh_enough(stale), False)
    check("unparsable timestamp rejected", nm._fresh_enough("last tuesday"), False)
    check("missing timestamp rejected", nm._fresh_enough(None), False)

    print("\n== source allowlist ==")
    check("openai allowlisted", "openai" in nm.SHARE_SOURCES, True)
    check("a random handle is not", "some_guy_2024" in nm.SHARE_SOURCES, False)


def live_dry():
    """Real feeds -> real X search -> real composer, without publishing."""
    print("\n== live dry run (nothing is posted) ==")
    items = []
    for name, url in nm.FEEDS.items():
        try:
            for title, link, desc in nm.parse_items(nm.fetch(url)):
                if nm.is_notable(title):
                    items.append((name, title, link, desc))
        except Exception as e:
            print(f"   feed {name} failed: {type(e).__name__}")
    print(f"   notable feed items available: {len(items)}")
    if not items:
        print("FAIL no feed items to test with")
        fails.append("live: no feed items")
        return

    if not reply_guy.acquire_browser_lock():
        print("   browser busy (cron job holds the lock) — skipping live part")
        return
    try:
        from playwright.sync_api import sync_playwright
        import browser_post
        import browser_share
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                browser_post.PROFILE, headless=True,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if not browser_post.check_session(page):
                print("   NOT LOGGED IN — skipping live part")
                ctx.close()
                return

            found = None
            for name, title, link, desc in items[:8]:
                q = nm.news_query(title)
                src = desc or ""
                if len(src) < 80 and link:
                    src = nm.fetch_article_text(link)
                cand = nm.find_source_post(page, title)
                print(f"   [{name}] {title[:70]}")
                print(f"      query={q!r} source={cand and cand['handle']}")
                if cand:
                    found = (name, title, src, cand)
                    break
            if not found:
                print("   no allowlisted source post in the 8 newest stories "
                      "(the lane falls back to a normal post when this happens)")
            else:
                name, title, src, cand = found
                comment = nm.write_share_comment(title, src or title)
                print(f"   source post : @{cand['handle']} {cand['url']} ({cand['when']})")
                print(f"   comment     : {comment!r}")
                check("comment generated", bool(comment), True)
                if comment:
                    ok, note, _ = browser_share.quote(page, cand["url"], comment, dry=True)
                    print(f"   dry quote   : {'OK' if ok else 'FAIL'} — {note}")
                    check("dry quote composer ready", ok, True)
            ctx.close()
    finally:
        reply_guy.release_browser_lock()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    offline()
    if mode != "offline":
        live_dry()
    print()
    if fails:
        print(f"{len(fails)} FAILED: {fails}")
        sys.exit(1)
    print("ALL PASS")
