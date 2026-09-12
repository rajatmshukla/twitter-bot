#!/usr/bin/env python3
"""Repost and quote-tweet via the X web UI. No API keys.

Everything below was learned from live probes on 2026-09-11
(scripts/probe_share_dom.py, probe_quote_composer.py, probe_quote_geometry.py,
probe_quote_dialog.py; raw dumps in tmp/*probe*.json):

  action bar   [data-testid="retweet"]   aria-label "<N> reposts. Repost"
               after reposting, the SAME control becomes [data-testid="unretweet"]
               and the aria-label flips to "<N> reposts. Reposted". That flip is
               the only trustworthy confirmation: the toast did not render at all
               on 3 of 4 successful reposts.
  menu         [role="menu"] with [role="menuitem"] rows:
                 "Repost"      -> data-testid="retweetConfirm"
                 "Undo repost" -> data-testid="retweetConfirm" (same testid)
                 "Quote"       -> NO data-testid; match innerText == "Quote"
  quote page   x.com/compose/post, quoted post inside [data-testid="attachments"]
               The quoted card renders WITHOUT an <a href>, so the quoted status
               id never appears in the dialog HTML, and the status page sits
               behind the composer, so a document-wide id search is meaningless.
               Real markers instead:
                 - the comment label reads "Add a comment" (quote mode only)
                 - the attachments chip text starts with "Quote" and names @author
               WARNING: the page carries TWO composers. Both pass Playwright's
               ':visible' (the hidden twin has a real box parked at y=951 in a
               900px viewport), and the hidden twin's button is the DISABLED one.
               Pick by in-viewport geometry only: in_viewport().
  after post   toast "Your post was sent." + the URL returns to the status page
               and the composer dialog disappears.

Exit codes (CLI): 0 ok, 2 not logged in, 3 selector/page problem, 4 unsure.
"""
import argparse
import os
import random
import re
import sys
import time
from urllib.parse import quote as urlquote

from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)  # append, not insert: bot dir's queue.py must not shadow stdlib

import browser_post  # noqa: E402

PROFILE = browser_post.PROFILE
TEXTAREA = '[data-testid="tweetTextarea_0"]'
POST_BUTTON = '[data-testid="tweetButton"]'
RETWEET = '[data-testid="retweet"], [data-testid="unretweet"]'
MAX_QUOTE_CHARS = 250  # X counts punctuation heavier than python len; 250 is the safe cap


def human_delay(a=1.0, b=2.2):
    time.sleep(random.uniform(a, b))


def log(line):
    browser_post.log(line)


def in_viewport(page, selector):
    """First match of `selector` whose box is actually inside the viewport.

    Playwright's ':visible' is NOT enough on the compose page: the hidden twin
    sits below the fold with a real box, so ':visible' matches both. Geometry
    is the only reliable split. Returns an ElementHandle or None.
    """
    h = page.evaluate_handle("""(sel) => {
      for (const el of document.querySelectorAll(sel)) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0 && r.top < innerHeight && r.bottom > 0
            && r.left < innerWidth && r.right > 0) return el;
      }
      return null;
    }""", selector)
    return h.as_element()


# ---------------------------------------------------------------- primitives

def retweet_state(page):
    """"reposted", "not", or None when neither button renders."""
    return page.evaluate("""() => {
      if (document.querySelector('[data-testid="unretweet"]')) return "reposted";
      if (document.querySelector('[data-testid="retweet"]')) return "not";
      return null;
    }""")


def retweet_aria(page):
    return page.evaluate("""() => {
      const el = document.querySelector('[data-testid="unretweet"]')
              || document.querySelector('[data-testid="retweet"]');
      return el ? (el.getAttribute('aria-label') || '') : null;
    }""")


def wait_state(page, want, timeout=14):
    """Poll the repost control until it reaches `want` or the timeout expires."""
    end = time.time() + timeout
    st = None
    while time.time() < end:
        st = retweet_state(page)
        if st == want:
            return st
        time.sleep(1.5)
    return st


def open_status(page, url):
    """Load a status page and wait for its action bar. True when ready."""
    if not url.startswith("http"):
        url = "https://x.com" + url
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    human_delay(2, 3.5)
    try:
        page.wait_for_selector(RETWEET, timeout=20_000)
        return True
    except Exception:
        return False


def open_repost_menu(page):
    """Click the repost control and return the menu's item labels."""
    btn = page.locator(
        '[data-testid="unretweet"]:visible, [data-testid="retweet"]:visible').first
    btn.click()
    human_delay(1.0, 1.8)
    try:
        page.wait_for_selector('[role="menu"] [role="menuitem"]', timeout=10_000)
    except Exception:
        return []
    return page.evaluate("""() => Array.from(
        document.querySelectorAll('[role="menu"] [role="menuitem"]'))
        .map(m => (m.innerText || '').trim())""")


def close_menu(page):
    try:
        page.keyboard.press("Escape")
        time.sleep(0.8)
    except Exception:
        pass


def click_menu_item(page, label):
    """Click a menu row by exact-ish label text. True when clicked."""
    for mi in page.locator('[role="menu"] [role="menuitem"]').all():
        try:
            txt = (mi.inner_text() or "").strip()
        except Exception:
            continue
        if txt.lower() == label.lower():
            mi.click()
            return True
    return False


def click_confirm_row(page, prefer_testid=True):
    """Click the menu's confirm row (Repost or its Undo twin).

    Both rows carry data-testid="retweetConfirm", which is exact and immune to
    label drift, so it is tried first. Returns True when a click was issued.
    """
    if prefer_testid:
        try:
            page.locator('[data-testid="retweetConfirm"]').first.click(timeout=10_000)
            return True
        except Exception:
            pass
    for mi in page.locator('[role="menu"] [role="menuitem"]').all():
        try:
            txt = (mi.inner_text() or "").strip().lower()
        except Exception:
            continue
        if txt in ("repost", "undo repost"):
            mi.click()
            return True
    return False


def toast_text(page, timeout=6_000):
    try:
        page.wait_for_selector('[data-testid="toast"]', timeout=timeout)
        return page.evaluate(
            """() => (document.querySelector('[data-testid="toast"]')||{}).innerText || ''""")
    except Exception:
        return None


def new_id_from_toast(page):
    """The toast's View link points at the freshly created post."""
    try:
        href = page.evaluate("""() => {
          const a = document.querySelector('[data-testid="toast"] a[href*="/status/"]');
          return a ? a.getAttribute('href') : null;
        }""")
        if href:
            m = re.search(r"/status/(\d+)", href)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def wait_quote_landed(page, timeout=16):
    """Poll for the settled signals that a quote was published.

    Two independent markers, both observed 2026-09-11: the toast, and the
    composer dialog disappearing with the URL off /compose/. Returns the state
    dict, or None when neither appeared.
    """
    end = time.time() + timeout
    while time.time() < end:
        st = page.evaluate("""() => {
          const t = document.querySelector('[data-testid="toast"]');
          const dlgs = Array.from(document.querySelectorAll('[role="dialog"]'))
                .filter(e => e.getBoundingClientRect().height > 50);
          return {toast: t ? (t.innerText || '').trim() : null,
                  dialogs: dlgs.length, url: location.href};
        }""")
        if st["toast"]:
            return st
        if st["dialogs"] == 0 and "/compose/" not in st["url"]:
            return st
        time.sleep(1.5)
    return None


def own_newest_posts(page, handle, limit=6):
    """[{id, text}] for the account's own newest posts, newest first."""
    page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded", timeout=60_000)
    time.sleep(4)
    return page.evaluate("""([h, limit]) => {
      const re = new RegExp('^/' + h + '/status/\\\\d+$');
      const out = [];
      for (const art of document.querySelectorAll('article')) {
        let id = null;
        for (const a of art.querySelectorAll('a[href*="/status/"]')) {
          const href = a.getAttribute('href');
          if (re.test(href)) { id = href.split('/').pop(); break; }
        }
        if (id && !out.some(o => o.id === id)) {
          out.push({id: id, text: (art.innerText || '').replace(/\\s+/g, ' ')});
        }
        if (out.length >= limit) break;
      }
      return out;
    }""", [handle, limit])


def search_posts(page, query, limit=10):
    """Top-sorted X search results: [{handle, id, url, when, text}]. Read-only."""
    page.goto(f"https://x.com/search?q={urlquote(query)}&f=top",
              wait_until="domcontentloaded", timeout=60_000)
    human_delay(3, 5)
    try:
        page.wait_for_selector('article', timeout=20_000)
    except Exception:
        return []
    return page.evaluate("""(limit) => {
      const out = [];
      for (const art of document.querySelectorAll('article')) {
        let id = null, handle = null;
        for (const a of art.querySelectorAll('a[href*="/status/"]')) {
          const href = a.getAttribute('href') || '';
          const m = href.match(/^\\/([A-Za-z0-9_]+)\\/status\\/(\\d+)$/);
          if (m) { handle = m[1]; id = m[2]; break; }
        }
        if (!id) continue;
        const t = art.querySelector('time');
        out.push({handle: handle, id: id, url: '/' + handle + '/status/' + id,
                  when: t ? t.getAttribute('datetime') : null,
                  text: (art.innerText || '').replace(/\\s+/g, ' ').slice(0, 400)});
        if (out.length >= limit) break;
      }
      return out;
    }""", limit)


# ------------------------------------------------------------------ actions

def repost(page, status_url, attempts=2):
    """Repost a post. Returns (ok, note).

    Two attempts: on 2026-09-11 a click that followed a menu-open + Escape in
    the same page silently failed (no toast, no state change), while the same
    call on a freshly loaded page worked. The retry is safe because it only
    fires when the state still reads "not".
    """
    notes = []
    for i in range(1, attempts + 1):
        if not open_status(page, status_url):
            notes.append("status page did not render an action bar")
            continue
        st = retweet_state(page)
        if st == "reposted":
            return True, f"already reposted (attempt {i})"
        if st is None:
            notes.append("no repost control on the page")
            continue
        labels = open_repost_menu(page)
        if not any(l.lower() == "repost" for l in labels):
            notes.append(f"menu has no Repost row (labels={labels})")
            close_menu(page)
            continue
        if not click_confirm_row(page):
            notes.append("could not click the Repost row")
            close_menu(page)
            continue
        st = wait_state(page, "reposted")
        if st == "reposted":
            return True, f"reposted, state flip confirmed (attempt {i})"
        notes.append(f"click did not flip the state (still {st!r})")
        close_menu(page)
    return False, "; ".join(notes)


def undo_repost(page, status_url, attempts=2):
    """Undo an existing repost. Returns (ok, note)."""
    notes = []
    for i in range(1, attempts + 1):
        if not open_status(page, status_url):
            notes.append("status page did not render an action bar")
            continue
        st = retweet_state(page)
        if st == "not":
            return True, "not reposted, nothing to undo"
        if st is None:
            notes.append("no repost control on the page")
            continue
        labels = open_repost_menu(page)
        if not any("undo" in l.lower() for l in labels):
            notes.append(f"menu has no Undo row (labels={labels})")
            close_menu(page)
            continue
        if not click_confirm_row(page):
            notes.append("could not click the Undo row")
            close_menu(page)
            continue
        st = wait_state(page, "not")
        if st == "not":
            return True, f"repost undone (attempt {i})"
        notes.append(f"undo did not flip the state (still {st!r})")
        close_menu(page)
    return False, "; ".join(notes)


def quote_attached(page, handle):
    """Evidence that the in-viewport composer is in QUOTE mode for `handle`."""
    return page.evaluate("""(handle) => {
      const inView = el => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.top < innerHeight && r.bottom > 0;
      };
      const dlgs = Array.from(document.querySelectorAll('[role="dialog"]')).filter(inView);
      if (!dlgs.length) return {ok: false, why: "no in-viewport composer dialog"};
      const dlg = dlgs[0];
      const label = dlg.querySelector('[data-testid="tweetTextarea_0_label"]');
      const label_txt = label ? (label.innerText || '').trim() : '';
      const att = dlg.querySelector('[data-testid="attachments"]');
      const att_txt = att ? (att.innerText || '').trim() : '';
      const ok = label_txt.toLowerCase().includes('add a comment')
                 && att !== null
                 && att_txt.toLowerCase().startsWith('quote')
                 && att_txt.toLowerCase().includes('@' + handle.toLowerCase());
      return {ok: ok, label: label_txt, attachments: att_txt.slice(0, 120)};
    }""", handle)


def quote(page, status_url, comment, dry=False):
    """Quote-post with a comment. Returns (ok, note, new_id).

    Publishes a real post unless dry=True. The comment is capped at
    MAX_QUOTE_CHARS. The post is NEVER retried on an ambiguous result: a blind
    retry risks a duplicate, so an inconclusive click is resolved by looking for
    a new post on the profile, and only then reported as UNSURE.
    """
    comment = (comment or "").strip()
    if not comment:
        return False, "empty comment", None
    if len(comment) > MAX_QUOTE_CHARS:
        return False, f"comment too long ({len(comment)} > {MAX_QUOTE_CHARS})", None
    full = status_url if status_url.startswith("http") else "https://x.com" + status_url
    m = re.search(r"(?:x\.com|twitter\.com)/([^/]+)/status/", full)
    if not m:
        return False, f"cannot read a handle out of {status_url!r}", None
    handle = m.group(1)
    if not open_status(page, status_url):
        return False, "status page did not render an action bar", None
    labels = open_repost_menu(page)
    if not click_menu_item(page, "Quote"):
        return False, f"menu has no Quote row (labels={labels})", None
    # the composer is a NEW page load (x.com/compose/post), not a modal
    human_delay(2.5, 4.0)
    try:
        page.wait_for_selector('[data-testid="attachments"]', timeout=25_000)
    except Exception:
        return False, "quote composer never rendered (no attachments node)", None
    hit = quote_attached(page, handle)
    if not hit.get("ok"):
        return False, f"composer is not in quote mode for @{handle}: {hit}", None
    box = in_viewport(page, TEXTAREA)
    if box is None:
        return False, "no in-viewport comment box on the quote composer", None
    box.click()
    human_delay(0.4, 1.0)
    box.type(comment, delay=random.randint(25, 60))
    human_delay(1.0, 2.0)
    typed = page.evaluate("""() => {
      const dlg = Array.from(document.querySelectorAll('[role="dialog"]'))
            .filter(e => e.getBoundingClientRect().height > 50)[0];
      const el = dlg ? dlg.querySelector('[data-testid="tweetTextarea_0"]') : null;
      return el ? (el.innerText || '').trim() : null;
    }""")
    if not typed or comment[:20].lower() not in typed.lower():
        return False, f"comment did not land in the composer (got {typed!r})", None
    btn = in_viewport(page, POST_BUTTON)
    if btn is None:
        return False, "no in-viewport Post button on the quote composer", None
    if dry:
        # Pre-flight mode: everything is verified except the publish, so the
        # caller can prove discovery + comment + composer wiring without posting.
        close_menu(page)
        return True, "DRY RUN: quote composer ready, comment typed, nothing posted", None
    try:
        btn.click(timeout=15_000)
    except Exception as e:
        return False, f"Post click failed: {type(e).__name__}: {e}", None
    landed = wait_quote_landed(page)
    new_id = new_id_from_toast(page)
    if landed is not None:
        return True, f"quoted (toast={landed['toast']!r}, id={new_id})", new_id
    # Inconclusive. Resolve it from the account's own timeline, never by retrying.
    try:
        for p in own_newest_posts(page, handle, limit=4):
            if comment[:25].lower() in p["text"].lower():
                return True, f"quoted (found on profile, id={p['id']})", p["id"]
    except Exception as e:
        return False, f"UNSURE and profile check failed: {type(e).__name__}: {e}", None
    return False, "UNSURE: no toast, composer state unclear, no matching new post", None


# ---------------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["repost", "undo", "quote", "state", "search", "check"])
    ap.add_argument("url", nargs="?", default=None, help="status url (or search query)")
    ap.add_argument("--file", default=None, help="comment file for quote")
    ap.add_argument("--text", default=None, help="comment text for quote")
    args = ap.parse_args()

    if args.action == "check":
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                PROFILE, headless=True, viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            ok = browser_post.check_session(page)
            ctx.close()
        print("SESSION OK" if ok else "NOT LOGGED IN")
        return 0 if ok else 2

    if not args.url:
        print("need a status url", file=sys.stderr)
        return 3

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True, viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not browser_post.check_session(page):
            log("NOT LOGGED IN — run browser_login.py")
            ctx.close()
            return 2

        if args.action == "state":
            if not open_status(page, args.url):
                ctx.close()
                return 3
            print(f"state={retweet_state(page)} aria={retweet_aria(page)!r}")
            ctx.close()
            return 0

        if args.action == "search":
            for r in search_posts(page, args.url, limit=10):
                print(f"@{r['handle']:20s} {r['when']} {r['id']}  {r['text'][:90]}")
            ctx.close()
            return 0

        if args.action == "repost":
            ok, note = repost(page, args.url)
        elif args.action == "undo":
            ok, note = undo_repost(page, args.url)
        else:
            comment = args.text or (
                open(args.file, encoding="utf-8").read() if args.file else "")
            ok, note, new_id = quote(page, args.url, comment)
            if new_id:
                note += f" new_id={new_id}"
        log(f"{args.action}: {'OK' if ok else 'FAIL'} — {note}")
        print(f"{'OK' if ok else 'FAIL'} — {note}")
        ctx.close()
        return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
