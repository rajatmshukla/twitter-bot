#!/usr/bin/env python3
"""One-shot audit probe of @first_sauce_lab profile. Uses persistent browser profile."""
import json, sys, time, random
sys.path.append(r"C:\Users\Rajat\twitter-bot")
from playwright.sync_api import sync_playwright

PROFILE = r"C:\Users\Rajat\twitter-bot\browser-profile"

def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True,
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # session check: logged-in-only DOM marker
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_selector('[data-testid="SideNav_NewTweet_Button"]', timeout=20000)
        except Exception:
            print("SESSION: NOT LOGGED IN")
            ctx.close(); return
        print("SESSION: OK")
        # profile
        page.goto("https://x.com/first_sauce_lab", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)
        # followers / following from header
        txt = page.locator('[data-testid="primaryColumn"]').inner_text(timeout=15000)
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        # profile header: name, handle, bio, counts
        # find follower/following lines
        idx = {}
        for i, l in enumerate(lines):
            ll = l.lower()
            if "following" in ll and "follower" not in ll and "followers" not in ll:
                idx.setdefault("following", l)
            if "followers" in ll:
                idx.setdefault("followers", l)
        bio_hits = [l for l in lines[:40] if len(l) > 60]
        print("=== HEADER COUNTS ===")
        print("followers:", idx.get("followers", "?"))
        print("following:", idx.get("following", "?"))
        print("=== BIO CANDIDATES (first 40 lines, len>60) ===")
        for b in bio_hits[:8]:
            print("BIO?", repr(b[:200]))
        print("=== PINNED? ===")
        pin = page.locator('article:has-text("Pinned")').first
        try:
            if pin.count() > 0:
                print("PINNED TEXT:", repr(pin.inner_text()[:400]))
            else:
                arts = page.locator('article').all()
                if arts:
                    print("FIRST ARTICLE (no pinned flag):", repr(arts[0].inner_text()[:300]))
        except Exception as e:
            print("pin err", e)
        ctx.close()

if __name__ == "__main__":
    main()
