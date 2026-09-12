#!/usr/bin/env python3
"""READ-ONLY probe: what does the mentions tab actually expose?

No posting, no state writes. Prints one line per article so we can see whether
author / tweet id / text / timestamp / "Replying to" context are all scrapeable
before building an engine on top of them.
"""
import os, sys, time, random, json

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")
ME = "first_sauce_lab"

from playwright.sync_api import sync_playwright

def main():
    """Launch headless Chromium to inspect x.com mentions for an authenticated session.

    Loads the persistent browser profile, scrolls the mentions tab, and scrapes up to 15
    tweet articles. Prints each extracted record as a JSON string to stdout.
    """
    out = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True, viewport={"width": 1280, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60_000)
        time.sleep(random.uniform(2, 4))
        print("URL after home:", page.url)
        if "login" in page.url:
            print("NOT LOGGED IN")
            ctx.close()
            return 2

        page.goto("https://x.com/notifications/mentions", wait_until="domcontentloaded", timeout=60_000)
        time.sleep(random.uniform(4, 6))
        for _ in range(3):
            page.mouse.wheel(0, 1400)
            time.sleep(random.uniform(1.5, 2.5))

        arts = page.locator('article[data-testid="tweet"]')
        n = arts.count()
        print(f"articles on mentions page: {n}")
        for i in range(min(n, 15)):
            a = arts.nth(i)
            try:
                rec = {"i": i}
                # author + id from the FIRST status link inside the article
                link = a.locator('a[href*="/status/"]').first
                href = link.get_attribute("href") if link.count() else ""
                rec["href"] = href
                if "/status/" in href:
                    parts = href.split("/")
                    ai = parts.index("status")
                    rec["author"] = parts[ai - 1]
                    rec["id"] = parts[ai + 1].split("?")[0]
                # text
                t = a.locator('[data-testid="tweetText"]')
                rec["text"] = (t.first.inner_text() if t.count() else "")[:220]
                # time
                te = a.locator("time").first
                rec["time"] = te.get_attribute("datetime") if te.count() else ""
                # "Replying to @x" context
                sc = a.locator('[data-testid="socialContext"]')
                rec["ctx"] = sc.first.inner_text() if sc.count() else ""
                # is there a "Replying to @first_sauce_lab" hint in raw text?
                raw = a.inner_text()[:400].replace("\n", " | ")
                rec["has_replyto_me"] = f"@{ME}" in raw
                out.append(rec)
            except Exception as e:
                out.append({"i": i, "err": f"{type(e).__name__}: {e}"})
        ctx.close()

    for r in out:
        print(json.dumps(r, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    sys.exit(main())
