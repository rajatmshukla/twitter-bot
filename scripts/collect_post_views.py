#!/usr/bin/env python3
"""Per-post view counts for @first_sauce_lab, read from the live pages.

The 7-day impressions figure in logs/impressions.csv is a sum over a handful of
posts, so it cannot say WHICH post got the views. This reads each recent post's
own page and pulls its view count plus the engagement row, then writes a JSON
and prints a table.

Read-only. Posts nothing.

  python3 scripts/collect_post_views.py [--max 8]
"""
import argparse, json, os, re, sys, time

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
import reply_guy as rg
import browser_guard
from playwright.sync_api import sync_playwright

HANDLE = "first_sauce_lab"


def parse_count(s):
    """'1.2K' -> 1200, '3' -> 3, '' -> None."""
    if not s:
        return None
    s = s.strip().replace(",", "")
    m = re.match(r"^([\d.]+)\s*([KMB]?)$", s, re.I)
    if not m:
        return None
    v = float(m.group(1))
    return int(v * {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[m.group(2).upper()])


def main():
    """Scrape view counts and engagement rows from recent posts of the configured handle.

    Drives headless Chromium via Playwright against live profile and post pages.
    Saves extracted post statistics to tmp/post_views.json and prints a summary to stdout.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=8)
    a = ap.parse_args()

    if not browser_guard.hold("collect_post_views"):
        print(f"profile busy: {browser_guard.busy_reason()}")
        return 0

    out = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            rg.PROFILE, headless=True, viewport={"width": 1280, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        verdict, _ = browser_guard.classify_session(page)
        print(f"session: {verdict}")
        if verdict != "ok":
            ctx.close()
            return 2

        page.goto(f"https://x.com/{HANDLE}", wait_until="domcontentloaded", timeout=60_000)
        rg.human_delay(4, 6)
        for _ in range(2):
            page.mouse.wheel(0, 1800)
            time.sleep(1.5)

        posts = page.evaluate("""(h) => {
            const arts = [...document.querySelectorAll('article[data-testid="tweet"]')];
            const seen = new Set(); const out = [];
            for (const art of arts) {
                const link = [...art.querySelectorAll('a[href*="/status/"]')]
                    .map(x => x.getAttribute('href'))
                    .find(x => x && x.startsWith('/' + h + '/status/'));
                if (!link) continue;
                const id = link.split('/status/')[1].split('?')[0];
                if (seen.has(id)) continue;
                seen.add(id);
                const t = art.querySelector('[data-testid="tweetText"]');
                const tm = art.querySelector('time');
                out.push({id, url: 'https://x.com' + link,
                          text: t ? t.innerText : '',
                          when: tm ? tm.getAttribute('datetime') : ''});
            }
            return out;
        }""", HANDLE)
        print(f"posts found on profile: {len(posts)}")

        for post in posts[:a.max]:
            page.goto(post["url"], wait_until="domcontentloaded", timeout=60_000)
            rg.human_delay(2.5, 4)
            info = page.evaluate("""() => {
                const art = document.querySelector('article[data-testid="tweet"]');
                if (!art) return {};
                const res = {};
                const ana = art.querySelector('a[href$="/analytics"]');
                if (ana) {
                    res.analytics_aria = ana.getAttribute('aria-label') || '';
                    const t = ana.innerText || '';
                    const m = t.match(/([\\d.,]+\\s*[KMB]?)\\s*Views?/i);
                    if (m) res.views_text = m[1].trim();
                }
                const row = art.querySelector('[role="group"]');
                if (row) res.row_aria = row.innerText.replace(/\\n/g, ' | ').slice(0, 200);
                res.article_text = art.innerText.slice(0, 400);
                return res;
            }""")
            views = None
            if info.get("views_text"):
                views = parse_count(info["views_text"])
            elif info.get("analytics_aria"):
                m = re.search(r"([\d.,]+\s*[KMB]?)\s*view", info["analytics_aria"], re.I)
                if m:
                    views = parse_count(m.group(1))
            post["views"] = views
            post["views_raw"] = info.get("views_text") or info.get("analytics_aria", "")[:60]
            post["row"] = info.get("row_aria", "")
            out.append(post)
            print(f"  {post['id']}  views={views}  raw={post['views_raw']!r}  "
                  f"{(post['text'] or '')[:60]!r}")
            rg.human_delay(1, 2)
        ctx.close()

    path = os.path.join(BOT, "tmp", "post_views.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"), indent=1)

    known = [p["views"] for p in out if p.get("views") is not None]
    print(f"\n=== summary ===")
    print(f"posts with a readable view count: {len(known)}/{len(out)}")
    if known:
        print(f"total views across those posts: {sum(known)}")
        print(f"median: {sorted(known)[len(known)//2]}   max: {max(known)}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
