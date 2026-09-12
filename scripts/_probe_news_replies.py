#!/usr/bin/env python3
"""READ-ONLY: show what news-derived reply topics come out, and what X search
returns for them. Posts nothing, writes no state."""
import sys, os, time, json
BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
import reply_guy_direct as rgd
import reply_guy as rg

qs = rgd.news_queries()
print("news queries:", qs)
if not qs:
    print("no news queries derived (feeds empty or gate filtered everything)")
    sys.exit(0)

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        rg.PROFILE, headless=True, viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    if not rg.check_session(page):
        print("NOT LOGGED IN")
        ctx.close()
        sys.exit(2)
    for q in qs:
        try:
            res = rg.scrape_search(page, q, live=True)
        except Exception as e:
            print(f"  {q!r}: search failed {type(e).__name__}: {e}")
            continue
        fresh = [t for t in res if t["time"] and rg._is_fresh(t["time"])]
        print(f"\n== {q!r}: {len(res)} results, {len(fresh)} fresh (<48h)")
        for t in fresh[:4]:
            print(f"   @{t['author']} [{t['time']}] {t['text'][:130]}")
        rg.human_delay(2, 4)
    ctx.close()
