#!/usr/bin/env python3
"""Audit probe 4: avatar, bio link, header details."""
import sys
sys.path.append(r"C:\Users\Rajat\twitter-bot")
from playwright.sync_api import sync_playwright

PROFILE = r"C:\Users\Rajat\twitter-bot\browser-profile"

def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, headless=True,
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://x.com/first_sauce_lab", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3500)
        # avatar
        av = page.locator('img[src*="profile_images"]').count()
        print("profile_images imgs:", av)
        # banner
        ban = page.locator('img[src*="profile_banners"]').count()
        print("banner imgs:", ban)
        # external links in header area
        links = page.locator('[data-testid="primaryColumn"] a[href*="http"]').evaluate_all(
            "els => els.map(e => e.href)") if False else None
        # simpler: text of the header region below bio
        bio_link = page.locator('[data-testid="primaryColumn"] span:has-text("http"), [data-testid="primaryColumn"] a[dir="auto"]').all()
        hrefs = set()
        for a in page.locator('[data-testid="primaryColumn"] a[href^="http"]').all():
            try:
                hrefs.add(a.get_attribute("href"))
            except Exception:
                pass
        print("external hrefs in primary column:", list(hrefs)[:10])
        # name/joined
        hdr = page.locator('[data-testid="UserName"]').inner_text(timeout=8000)
        print("header:", hdr.replace("\n", " | "))
        ctx.close()

if __name__ == "__main__":
    main()
