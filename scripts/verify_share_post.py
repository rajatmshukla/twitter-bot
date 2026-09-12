#!/usr/bin/env python3
"""Verify a first_sauce_lab post exists and carries an attached quoted post.

Reads the status page, checks structure, and saves a screenshot so the layout
can be seen rather than inferred.

  python3 scripts/verify_share_post.py <status_id>
"""
import json, os, sys, time

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
import reply_guy as rg
import browser_guard
from playwright.sync_api import sync_playwright


def main():
    sid = sys.argv[1] if len(sys.argv) > 1 else None
    if not sid:
        print("usage: verify_share_post.py <status_id>")
        return 4

    shot = os.path.join(BOT, "tmp", f"verify_{sid}.png")
    os.makedirs(os.path.dirname(shot), exist_ok=True)
    if not browser_guard.hold("verify_share_post"):
        print(f"profile busy: {browser_guard.busy_reason()}")
        return 0

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            rg.PROFILE, headless=True, viewport={"width": 1100, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        verdict, _ = browser_guard.classify_session(page)
        if verdict != "ok":
            print(f"session: {verdict}")
            ctx.close()
            return 2

        url = f"https://x.com/first_sauce_lab/status/{sid}"
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        rg.human_delay(4, 6)
        print(f"url  : {page.url}")

        quoted = sys.argv[2].lstrip("@") if len(sys.argv) > 2 else None
        info = page.evaluate("""(quotedHandle) => {
            const arts = [...document.querySelectorAll('article[data-testid="tweet"]')];
            const focal = arts[0];
            if (!focal) return {articles: 0};
            const txt = focal.querySelector('[data-testid="tweetText"]');
            const full = focal.innerText || '';
            const hrefs = [...focal.querySelectorAll('a[href]')]
                .map(a => a.getAttribute('href'))
                .filter(h => /^\\/[A-Za-z0-9_]+\\/status\\/\\d+$/.test(h));
            const other = [...new Set(hrefs.filter(h => !h.startsWith('/first_sauce_lab/')))];
            return {
              articles: arts.length,
              nested_articles: focal.querySelectorAll('article[data-testid="tweet"]').length,
              focal_text: txt ? txt.innerText : '',
              other_status_links: other,
              quoted_handle_in_text: quotedHandle
                  ? full.toLowerCase().includes('@' + quotedHandle.toLowerCase())
                  : null,
            };
        }""", quoted)
        print(json.dumps(info, indent=1))
        # The quoted card does not always expose a /status/ anchor inside the
        # focal article, and it is NOT a nested article. Presence of the quoted
        # account's handle in the focal element is the reliable DOM signal;
        # the screenshot is the final check.
        attached = bool(info.get("quoted_handle_in_text")) or bool(info.get("other_status_links"))
        print(f"quoted card attached: {attached}")
        page.screenshot(path=shot, full_page=False)
        print(f"screenshot: {shot}")
        ctx.close()
    return 0 if attached else 1


if __name__ == "__main__":
    sys.exit(main())
