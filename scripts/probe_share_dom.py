#!/usr/bin/env python3
"""Read-only probe of X's repost / quote DOM (2026-09-11).

Learns the exact selectors for:
  - the repost button in a post's action bar
  - the menu that opens (Repost / Quote items) and their data-testids
  - the quote composer and its quoted-tweet card
  - whether the menu closes with Escape

It NEVER clicks a confirm button. Nothing is posted, reposted, or quoted.
Run from outside twitter-bot so the bot's queue.py cannot shadow stdlib queue.
"""
import os, sys, time, json

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)  # append, not insert

from playwright.sync_api import sync_playwright
import browser_post

OUT = os.path.join(BOT, "tmp", "share_dom_probe.json")


def dump_menu(page, tag):
    """Every menuitem in the open menu: text + data-testid, plus nested testids."""
    return page.evaluate("""(tag) => {
      const out = {tag: tag, items: [], nested: []};
      const menu = document.querySelector('[role="menu"], [data-testid="Dropdown"]');
      out.menu_found = !!menu;
      if (menu) {
        for (const mi of menu.querySelectorAll('[role="menuitem"]')) {
          out.items.push({
            text: (mi.innerText || '').trim(),
            testid: mi.getAttribute('data-testid') || null,
            child_testids: Array.from(mi.querySelectorAll('[data-testid]'))
                              .map(e => e.getAttribute('data-testid')),
          });
        }
        out.nested = Array.from(menu.querySelectorAll('[data-testid]'))
                          .map(e => e.getAttribute('data-testid'));
      }
      return out;
    }""", tag)


def main():
    result = {"when": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            browser_post.PROFILE, headless=True,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        ok = browser_post.check_session(page)
        result["session"] = ok
        print("SESSION OK" if ok else "NOT LOGGED IN")
        if not ok:
            ctx.close()
            return 2

        # 1. Find a real status id from a source account's profile page.
        page.goto("https://x.com/OpenAI", wait_until="domcontentloaded", timeout=60_000)
        time.sleep(4)
        href = page.evaluate("""() => {
          for (const a of document.querySelectorAll('article a[href*="/status/"]')) {
            if (/\\/OpenAI\\/status\\/\\d+$/.test(a.getAttribute('href'))) return a.getAttribute('href');
          }
          return null;
        }""")
        result["sample_status"] = href
        print("sample status:", href)
        if not href:
            ctx.close()
            return 3

        # 2. Open the status page and inspect the action bar.
        page.goto("https://x.com" + href, wait_until="domcontentloaded", timeout=60_000)
        time.sleep(4)
        result["action_bar"] = page.evaluate("""() => {
          const bar = document.querySelector('[role="group"]');
          return {
            bar_testids: bar ? Array.from(bar.querySelectorAll('[data-testid]')).map(e => e.getAttribute('data-testid')) : [],
            retweet_btn: !!document.querySelector('[data-testid="retweet"]'),
            retweet_label: (document.querySelector('[data-testid="retweet"]')||{}).ariaLabel || null,
          };
        }""")
        print("action bar:", json.dumps(result["action_bar"]))

        # 3. Click the repost button -> menu.
        rt = page.locator('[data-testid="retweet"]').first
        rt.click()
        time.sleep(2)
        result["menu_closed_state"] = dump_menu(page, "on_status_page")
        print("menu (status page):", json.dumps(result["menu_closed_state"], indent=1)[:2000])

        # 4. Find the Quote menu item and click it (opens a composer, posts nothing).
        quote_item = None
        for mi in page.locator('[role="menu"] [role="menuitem"]').all():
            try:
                txt = (mi.inner_text() or "").strip().lower()
            except Exception:
                continue
            if "quote" in txt:
                quote_item = mi
                break
        result["quote_item_found"] = quote_item is not None
        if quote_item is not None:
            quote_item.click()
            time.sleep(4)
            result["quote_composer"] = page.evaluate("""() => {
              const tas = Array.from(document.querySelectorAll('[data-testid^="tweetTextarea_"]'));
              return {
                url: location.href,
                textareas: tas.map(t => t.getAttribute('data-testid')),
                quoted_card: !!document.querySelector('[data-testid="tweetTextarea_0"]')
                              && !!document.querySelector('[role="dialog"], main'),
                testids_with_quoted: Array.from(document.querySelectorAll('[data-testid]'))
                    .map(e => e.getAttribute('data-testid'))
                    .filter(t => t && /quote|Quote|attachments|tweetButton|tweetTextarea/.test(t)),
                buttons: Array.from(document.querySelectorAll('[data-testid$="Button"], [data-testid="tweetButton"], [data-testid="tweetButtonInline"]'))
                    .map(e => ({testid: e.getAttribute('data-testid'), disabled: e.getAttribute('aria-disabled'), text: (e.innerText||'').trim().slice(0,20)})),
              };
            }""")
            print("quote composer:", json.dumps(result["quote_composer"], indent=1)[:2500])
            # escape the composer without posting
            page.keyboard.press("Escape")
            time.sleep(2)
            for label in ("Discard", "Save"):
                b = page.locator(f'[role="button"]:has-text("{label}")')
                if b.count():
                    print(f"draft dialog button present: {label} ({b.count()})")
            result["after_escape"] = page.evaluate("() => ({url: location.href, textareas: document.querySelectorAll('[data-testid^=\"tweetTextarea_\"]').length})")
            print("after escape:", result["after_escape"])

        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1)
        print("saved:", OUT)
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
