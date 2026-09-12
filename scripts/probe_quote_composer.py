#!/usr/bin/env python3
"""Focused probe: what does the quote composer actually attach? (2026-09-11)

Opens the Quote flow for ONE of the account's own posts, dumps what the
composer holds, then aborts without posting. Nothing is published.
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

OUT = os.path.join(BOT, "tmp", "quote_composer_probe.json")


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
                time.sleep(3)
            if not browser_post.check_session(page):
                print("NOT LOGGED IN")
                ctx.close()
                return 2

            page.goto("https://x.com/first_sauce_lab", wait_until="domcontentloaded",
                      timeout=60_000)
            time.sleep(3)
            url = page.evaluate("""() => {
              const re = /^\\/first_sauce_lab\\/status\\/\\d+$/;
              for (const a of document.querySelectorAll('article a[href*="/status/"]')) {
                if (re.test(a.getAttribute('href'))) return a.getAttribute('href');
              }
              return null;
            }""")
            rep["target"] = url
            if not url:
                ctx.close()
                return 3
            sid = url.rsplit("/", 1)[-1]

            bs.open_status(page, url)
            labels = bs.open_repost_menu(page)
            rep["menu"] = labels
            clicked = bs.click_menu_item(page, "Quote")
            rep["quote_clicked"] = clicked
            time.sleep(4)
            rep["url_after_quote"] = page.url
            rep["composer"] = page.evaluate("""(sid) => {
              const vis = el => !!(el && el.offsetParent !== null);
              const tas = Array.from(document.querySelectorAll('[data-testid^="tweetTextarea_"]'));
              const att = document.querySelector('[data-testid="attachments"]');
              const links = att ? Array.from(att.querySelectorAll('a')).map(
                  a => a.getAttribute('href')) : [];
              return {
                textareas: tas.map(t => ({id: t.getAttribute('data-testid'),
                                          visible: vis(t)})),
                attachments_present: !!att,
                attachments_visible: vis(att),
                attachments_links: links,
                attachments_html_head: att ? att.innerHTML.slice(0, 700) : null,
                body_has_sid: document.body.innerHTML.includes(sid),
                dialog_count: document.querySelectorAll('[role="dialog"]').length,
                quoted_marker_count: document.querySelectorAll('[data-testid*="uoted"]').length,
                buttons: Array.from(document.querySelectorAll('[data-testid="tweetButton"], [data-testid="tweetButtonInline"]'))
                    .map(b => ({t: b.getAttribute('data-testid'), vis: vis(b),
                                dis: b.getAttribute('aria-disabled'),
                                txt: (b.innerText || '').trim()})),
              };
            }""", sid)

            # abort: leave the composer without posting
            page.keyboard.press("Escape")
            time.sleep(2)
            rep["after_escape_url"] = page.url
            ctx.close()

        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
        print(json.dumps(rep, indent=1)[:4000])
        print("saved:", OUT)
        return 0
    finally:
        reply_guy.release_browser_lock()


if __name__ == "__main__":
    sys.exit(main())
