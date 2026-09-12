#!/usr/bin/env python3
"""Probe 3: geometry of the quote composer — which twin is real, and where the
quoted card lives (2026-09-11). Opens the Quote flow on an own post and dumps
element boxes. Posts nothing.
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

OUT = os.path.join(BOT, "tmp", "quote_geometry_probe.json")

JS = """(sid) => {
  const info = el => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const dlg = el.closest('[role="dialog"]');
    return {
      testid: el.getAttribute('data-testid'),
      box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      display: cs.display, visibility: cs.visibility, opacity: cs.opacity,
      in_viewport: r.width > 0 && r.height > 0 && r.top < innerHeight && r.bottom > 0
                   && r.left < innerWidth && r.right > 0,
      aria_hidden: el.getAttribute('aria-hidden'),
      dialog: dlg ? {aria_hidden: dlg.getAttribute('aria-hidden'),
                     box: (() => {const q = dlg.getBoundingClientRect();
                                  return [Math.round(q.x), Math.round(q.y), Math.round(q.width), Math.round(q.height)];})()}
                  : null,
    };
  };
  const quoted_links = Array.from(document.querySelectorAll('a[href*="/status/' + sid + '"]'))
      .map(a => ({href: a.getAttribute('href'),
                  parent_testid: (a.closest('[data-testid]') || {}).getAttribute
                                 ? a.closest('[data-testid]').getAttribute('data-testid') : null,
                  ancestor_chain: (() => {const out=[]; let e=a;
                      while (e && out.length < 8) { if (e.getAttribute && e.getAttribute('data-testid')) out.push(e.getAttribute('data-testid')); e = e.parentElement; }
                      return out;})()}));
  const card = document.querySelector('[data-testid="tweetTextarea_0"]');
  return {
    viewport: [innerWidth, innerHeight],
    textareas: Array.from(document.querySelectorAll('[data-testid="tweetTextarea_0"]')).map(info),
    containers: Array.from(document.querySelectorAll('[data-testid="tweetTextarea_0RichTextInputContainer"]')).map(info),
    post_buttons: Array.from(document.querySelectorAll('[data-testid="tweetButton"]')).map(info),
    inline_buttons: Array.from(document.querySelectorAll('[data-testid="tweetButtonInline"]')).map(info),
    dialogs: Array.from(document.querySelectorAll('[role="dialog"]')).map(info),
    quoted_links: quoted_links,
    placeholder: card ? card.getAttribute('data-text') : null,
  };
}"""


def main():
    """Inspect bounding box geometry and visibility of quote composer elements on x.com.

    Drives headless Chromium to open the Quote dialog on an account post, extracts
    DOM element coordinates and styles, writes them to tmp/quote_geometry_probe.json, and exits.
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
                time.sleep(3)
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
            bs.open_status(page, url)
            bs.open_repost_menu(page)
            bs.click_menu_item(page, "Quote")
            time.sleep(5)
            rep["url"] = page.url
            rep["geom"] = page.evaluate(JS, sid)
            print(json.dumps(rep, indent=1)[:6000])
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
