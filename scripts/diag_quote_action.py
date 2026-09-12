#!/usr/bin/env python3
"""Instrumented diagnostic for the QUOTE path (2026-09-11).

Runs the real quote flow on one of the account's OWN posts with a test comment,
dumps why the Post click did or did not land, and deletes the resulting post if
one was created. Leaves the account clean.
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

OWN = "/first_sauce_lab/status/2087993179127599252"
COMMENT = "Repost and quote path test. Deleting this in a minute."
OUT = os.path.join(BOT, "tmp", "quote_diag.json")

LIST_JS = """(h) => {
  const re = new RegExp('^/' + h + '/status/\\\\d+$');
  const ids = [];
  for (const a of document.querySelectorAll('article a[href*="/status/"]')) {
    const href = a.getAttribute('href');
    if (re.test(href)) { const id = href.split('/').pop(); if (!ids.includes(id)) ids.push(id); }
  }
  return ids.slice(0, 5);
}"""


def newest_ids(page):
    """Navigate to the profile page and scrape recent tweet IDs via JavaScript evaluation."""
    page.goto("https://x.com/first_sauce_lab", wait_until="domcontentloaded", timeout=60_000)
    time.sleep(4)
    return page.evaluate(LIST_JS, "first_sauce_lab")


def main():
    """Drive an instrumented quote-post action to diagnose composer click behavior.

    Runs against a logged-in Playwright session on an owned tweet while holding browser lock.
    Captures pre/post screenshots to tmp/ and writes diagnostic JSON to tmp/quote_diag.json.
    Any resulting quote tweet is deleted immediately to leave the account clean.
    """
    rep = {}
    if not reply_guy.acquire_browser_lock():
        print("browser busy")
        return 2
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                browser_post.PROFILE, headless=True,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if not browser_post.check_session(page):
                print("NOT LOGGED IN")
                ctx.close()
                return 2

            rep["before_ids"] = newest_ids(page)
            bs.open_status(page, OWN)
            labels = bs.open_repost_menu(page)
            rep["menu"] = labels
            rep["quote_clicked"] = bs.click_menu_item(page, "Quote")
            time.sleep(5)
            rep["composer_url"] = page.url
            rep["quote_mode"] = bs.quote_attached(page, "first_sauce_lab")
            box = bs.in_viewport(page, '[data-testid="tweetTextarea_0"]')
            rep["box_found"] = box is not None
            box.click()
            time.sleep(0.6)
            box.type(COMMENT, delay=30)
            time.sleep(1.5)
            rep["typed"] = page.evaluate("""() => {
              const dlg = Array.from(document.querySelectorAll('[role="dialog"]'))
                    .filter(e => e.getBoundingClientRect().height > 50)[0];
              const tas = dlg ? Array.from(dlg.querySelectorAll('[data-testid="tweetTextarea_0"]')) : [];
              return tas.map(t => ({text: (t.innerText||'').slice(0,60), len: (t.innerText||'').length}));
            }""")
            rep["button_state"] = page.evaluate("""() => {
              const r = [];
              for (const b of document.querySelectorAll('[data-testid="tweetButton"]')) {
                const q = b.getBoundingClientRect();
                r.push({box: [Math.round(q.x), Math.round(q.y), Math.round(q.width), Math.round(q.height)],
                        aria: b.getAttribute('aria-disabled'), text: (b.innerText||'').trim()});
              }
              return r;
            }""")
            page.screenshot(path=os.path.join(BOT, "tmp", "diag_quote_before_click.png"))

            btn = bs.in_viewport(page, '[data-testid="tweetButton"]')
            rep["click"] = "no in-viewport button"
            if btn is not None:
                try:
                    btn.click(timeout=15_000)
                    rep["click"] = "clicked in-viewport tweetButton"
                except Exception as e:
                    rep["click"] = f"{type(e).__name__}: {e}"
            time.sleep(6)
            rep["after_click"] = page.evaluate("""() => {
              const t = document.querySelector('[data-testid="toast"]');
              const dlgs = Array.from(document.querySelectorAll('[role="dialog"]'))
                    .filter(e => e.getBoundingClientRect().height > 50);
              return {url: location.href,
                      toast: t ? (t.innerText||'').trim() : null,
                      dialogs_in_view: dlgs.length,
                      textarea_lens: Array.from(document.querySelectorAll('[data-testid="tweetTextarea_0"]'))
                                     .map(e => (e.innerText||'').length),
                      body_head: document.body.innerText.slice(0, 200).replace(/\\n+/g, ' | ')};
            }""")
            page.screenshot(path=os.path.join(BOT, "tmp", "diag_quote_after_click.png"))
            rep["after_ids"] = newest_ids(page)
            ctx.close()

        rep["new_ids"] = [i for i in rep.get("after_ids", []) if i not in rep.get("before_ids", [])]
        print(json.dumps(rep, indent=1)[:4000])
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)

        if rep["new_ids"]:
            print("CLEANUP: deleting", rep["new_ids"])
            d = subprocess.run(["python3", os.path.join(BOT, "delete_tweets.py")] + rep["new_ids"],
                               capture_output=True, text=True, timeout=300, cwd=r"C:\Users\Rajat")
            print((d.stdout or "").strip().splitlines()[-2:])
            time.sleep(4)
            for i in rep["new_ids"]:
                code = subprocess.run(
                    ["curl", "-s", "-o", os.devnull, "-w", "%{http_code}", "-L",
                     f"https://x.com/first_sauce_lab/status/{i}"],
                    capture_output=True, text=True, timeout=60).stdout.strip()
                print(f"  {i} -> http {code}")
        return 0
    finally:
        reply_guy.release_browser_lock()


if __name__ == "__main__":
    sys.exit(main())
