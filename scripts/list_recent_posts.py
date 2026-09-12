#!/usr/bin/env python3
"""List the account's newest posts (id, time, text head) — used to find a stray
test post that must be deleted. Read-only."""
import json
import sys
import time

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
from playwright.sync_api import sync_playwright  # noqa: E402
import browser_post  # noqa: E402

HANDLE = "first_sauce_lab"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        browser_post.PROFILE, headless=True,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    if not browser_post.check_session(page):
        print("NOT LOGGED IN")
        sys.exit(2)
    page.goto(f"https://x.com/{HANDLE}", wait_until="domcontentloaded", timeout=60_000)
    time.sleep(5)
    out = page.evaluate("""(h) => {
      const rows = [];
      const re = new RegExp('^/' + h + '/status/\\\\d+$');
      for (const art of document.querySelectorAll('article')) {
        let id = null;
        for (const a of art.querySelectorAll('a[href*="/status/"]')) {
          const href = a.getAttribute('href');
          if (re.test(href)) { id = href.split('/').pop(); break; }
        }
        if (!id) continue;
        const t = art.querySelector('time');
        rows.push({id: id,
                   when: t ? t.getAttribute('datetime') : null,
                   text: (art.innerText || '').replace(/\\s+/g, ' ').slice(0, 160)});
      }
      return rows;
    }""", HANDLE)
    for r in out:
        print(json.dumps(r, ensure_ascii=False))
    ctx.close()
