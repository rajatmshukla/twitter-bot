#!/usr/bin/env python3
"""Probe 4: what marks the quoted tweet inside the composer dialog (2026-09-11).

The status page sits BEHIND the composer, so a document-wide search for the
quoted status id always hits the background page and proves nothing. This
scopes the search to the in-viewport composer dialog. Posts nothing.
"""
import json
import os
import sys
import time

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)

from playwright.sync_api import sync_playwright  # noqa: E402
import browser_post  # noqa: E402
import browser_share as bs  # noqa: E402
import reply_guy  # noqa: E402

OUT = os.path.join(BOT, "tmp", "quote_dialog_probe.json")

JS = """(sid) => {
  const inView = el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.top < innerHeight && r.bottom > 0
           && r.left < innerWidth && r.right > 0;
  };
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"]')).filter(inView);
  const dlg = dialogs[0] || null;
  const out = {dialogs_in_view: dialogs.length, has_sid_in_dialog: null,
               dialog_testids: [], attachments_text: null, card_testids: [],
               quote_chip: null};
  if (!dlg) return out;
  out.has_sid_in_dialog = dlg.innerHTML.includes(sid);
  out.dialog_testids = Array.from(dlg.querySelectorAll('[data-testid]'))
        .map(e => e.getAttribute('data-testid'))
        .filter((v, i, a) => a.indexOf(v) === i);
  const att = dlg.querySelector('[data-testid="attachments"]');
  out.attachments_text = att ? (att.innerText || '').slice(0, 200) : null;
  out.attachments_htmls = att ? att.outerHTML.slice(0, 1200) : null;
  // the quoted card: any descendant anchor pointing at the quoted status
  out.card_links = Array.from(dlg.querySelectorAll('a[href*="' + sid + '"]'))
        .map(a => a.getAttribute('href'));
  out.textarea_text = (dlg.querySelector('[data-testid="tweetTextarea_0"]') || {}).innerText || null;
  out.placeholder_text = (() => {
     const l = dlg.querySelector('[data-testid="tweetTextarea_0_label"]');
     return l ? (l.innerText || null) : null;
  })();
  return out;
}"""


def main():
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

            page.goto("https://x.com/first_sauce_lab", wait_until="domcontentloaded", timeout=60_000)
            time.sleep(3)
            url = page.evaluate("""() => {
              const re = /^\\/first_sauce_lab\\/status\\/\\d+$/;
              for (const a of document.querySelectorAll('article a[href*="/status/"]')) {
                if (re.test(a.getAttribute('href'))) return a.getAttribute('href');
              }
              return null;
            }""")
            sid = url.rsplit("/", 1)[-1]
            rep["target_sid"] = sid
            bs.open_status(page, url)
            bs.open_repost_menu(page)
            bs.click_menu_item(page, "Quote")
            time.sleep(5)
            rep["probe"] = page.evaluate(JS, sid)
            print(json.dumps(rep, indent=1)[:5000])
            page.keyboard.press("Escape")
            time.sleep(2)
            ctx.close()
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
        print("saved:", OUT)
        return 0
    finally:
        reply_guy.release_browser_lock()


if __name__ == "__main__":
    sys.exit(main())
