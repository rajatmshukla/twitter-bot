#!/usr/bin/env python3
"""Instrumented single-action diagnostic for the repost path (2026-09-11).

Clicks Repost on one own post by data-testid (retweetConfirm), then dumps
toast / aria-label / screenshots. Undoes the repost afterwards so the account
is left unchanged.
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

OWN = "/first_sauce_lab/status/2087993179127599252"
OUT = os.path.join(BOT, "tmp", "repost_diag.json")


def shots(page, tag):
    path = os.path.join(BOT, "tmp", f"diag_{tag}.png")
    page.screenshot(path=path)
    return path


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

            bs.open_status(page, OWN)
            rep["state_0"] = bs.retweet_state(page)
            rep["aria_0"] = bs.retweet_aria(page)
            labels = bs.open_repost_menu(page)
            rep["menu_labels"] = labels
            rep["shots_menu"] = shots(page, "menu")
            rep["menu_html"] = page.evaluate("""() => {
              const m = document.querySelector('[role="menu"]');
              return m ? Array.from(m.querySelectorAll('[role="menuitem"]')).map(
                  e => ({text: (e.innerText||'').trim(), testid: e.getAttribute('data-testid')})) : null;
            }""")
            # click the Repost row by testid, fall back to text
            clicked = None
            try:
                page.locator('[data-testid="retweetConfirm"]').first.click(timeout=10_000)
                clicked = "testid"
            except Exception as e:
                rep["testid_click_err"] = f"{type(e).__name__}: {e}"
                if bs.click_menu_item(page, "Repost"):
                    clicked = "text"
            rep["clicked"] = clicked
            time.sleep(5)
            rep["state_1"] = bs.retweet_state(page)
            rep["aria_1"] = bs.retweet_aria(page)
            rep["toast"] = page.evaluate("""() => {
              const t = document.querySelector('[data-testid="toast"]');
              return t ? (t.innerText||'').trim() : null;
            }""")
            rep["shots_after_click"] = shots(page, "after_click")
            rep["body_head"] = page.evaluate(
                "() => document.body.innerText.slice(0, 400).replace(/\\n+/g, ' | ')")

            # leave no trace: undo if it landed
            if bs.retweet_state(page) == "reposted":
                ok, note = bs.undo_repost(page, OWN)
                rep["cleanup"] = {"ok": ok, "note": note,
                                  "state_after": bs.retweet_state(page)}
            else:
                rep["cleanup"] = "nothing to undo"
            ctx.close()
        print(json.dumps(rep, indent=1)[:3500])
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
        return 0
    finally:
        reply_guy.release_browser_lock()


if __name__ == "__main__":
    sys.exit(main())
