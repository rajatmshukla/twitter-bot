#!/usr/bin/env python3
"""Repost and quote-tweet via the X web UI without API keys.

Automates reposting, undoing reposts, quote-tweeting, checking repost status,
and searching posts via Playwright on a persistent Chromium browser profile.

Invocation:
- CLI:
    python browser_share.py repost <status_url>
    python browser_share.py undo <status_url>
    python browser_share.py quote <status_url> [--file <path> | --text <str>]
    python browser_share.py state <status_url>
    python browser_share.py search <query>
    python browser_share.py check
- Imported by other automation scripts or queue runners that need repost or
  quote capabilities (e.g. interactive workflows or scheduled tools).

Inputs / Reads:
- Reads X web pages (status URLs, profile feeds, search results).
- Reads comment text from CLI flags or UTF-8 text files on disk.
- Reads browser session state via browser_post.check_session().

Outputs / Writes:
- Appends execution log entries to logs/browser_post.log via browser_post.log().
- Mutates browser profile cookies, cache, and session data under browser-profile/.

Live X Account Impact:
- 'repost': Publishes a public repost of the target tweet from the account.
- 'undo': Deletes the account's existing repost of the target tweet.
- 'quote': Publishes a new public quote-tweet on the account containing the comment
  and referencing the target tweet.

DOM and geometry notes (learned from live probes on 2026-09-11; raw dumps in tmp/):
- Action bar: [data-testid="retweet"] aria-label "<N> reposts. Repost".
  After reposting, the SAME control flips to [data-testid="unretweet"] and
  aria-label "<N> reposts. Reposted". This flip is the only reliable confirmation;
  toasts failed to render in 3 of 4 successful repost probes.
- Menu: [role="menu"] with [role="menuitem"] rows:
    "Repost" -> data-testid="retweetConfirm"
    "Undo repost" -> data-testid="retweetConfirm" (same testid)
    "Quote" -> NO data-testid; matched by innerText == "Quote"
- Quote page: x.com/compose/post, quoted post inside [data-testid="attachments"].
  The quoted card has no <a> link with status ID. Key markers:
    * Comment label reads "Add a comment"
    * Attachments chip text begins with "Quote" and mentions @author
  WARNING: The page renders TWO composers. Both pass Playwright's ':visible'
  selector (the hidden twin sits below the fold at y=951 in a 900px viewport
  with a disabled button). Use in_viewport() to target the active composer.
- After post: Toast "Your post was sent." appears, dialog closes, and URL returns
  to the status page.

CLI exit codes: 0 ok, 2 not logged in, 3 selector/page problem, 4 unsure.
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
    """Sleep for a randomized interval between a and b seconds.

    Paces browser interactions to mimic human typing and browsing timing.
    Args:
        a: Minimum sleep duration in seconds.
        b: Maximum sleep duration in seconds.
    Side effects:
        Blocks current thread for the duration.
    """
    time.sleep(random.uniform(a, b))


def log(line):
    """Write an execution record to the browser log file via browser_post.

    Args:
        line: Message string to append.
    Side effects:
        Appends a timestamped line to logs/browser_post.log.
    """
    browser_post.log(line)


def in_viewport(page, selector):
    """Find first element matching selector whose bounding box is inside viewport.

    Playwright's ':visible' check is insufficient on the compose page because
    a hidden secondary composer sits below the fold (y=951 in a 900px viewport)
    with non-zero dimensions. Geometry comparison is required.

    Args:
        page: Playwright Page instance.
        selector: CSS selector string to query.
    Returns:
        Playwright ElementHandle inside the viewport, or None if none found.
    Side effects:
        Executes JavaScript in the browser page context.
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
    """Determine whether the current status page is already reposted.

    Queries the DOM for the retweet or unretweet testid buttons.

    Args:
        page: Playwright Page loaded on an X status URL.
    Returns:
        "reposted" if unretweet is present, "not" if retweet is present,
        or None when neither control renders.
    Side effects:
        Executes JavaScript in the browser page context.
    """
    return page.evaluate("""() => {
      if (document.querySelector('[data-testid="unretweet"]')) return "reposted";
      if (document.querySelector('[data-testid="retweet"]')) return "not";
      return null;
    }""")


def retweet_aria(page):
    """Read the aria-label attribute of the retweet or unretweet button.

    Args:
        page: Playwright Page loaded on an X status URL.
    Returns:
        String aria-label (e.g. '12 reposts. Repost'), or None if missing.
    Side effects:
        Executes JavaScript in the browser page context.
    """
    return page.evaluate("""() => {
      const el = document.querySelector('[data-testid="unretweet"]')
              || document.querySelector('[data-testid="retweet"]');
      return el ? (el.getAttribute('aria-label') || '') : null;
    }""")


def wait_state(page, want, timeout=14):
    """Poll the repost control until it reaches want or the timeout expires.

    Args:
        page: Playwright Page instance.
        want: Target state string ("reposted" or "not").
        timeout: Maximum polling duration in seconds.
    Returns:
        Current state string reached, or last observed state on timeout.
    Side effects:
        Polls the DOM every 1.5 seconds, blocking the calling thread.
    """
    end = time.time() + timeout
    st = None
    while time.time() < end:
        st = retweet_state(page)
        if st == want:
            return st
        time.sleep(1.5)
    return st


def open_status(page, url):
    """Load a status page and wait for its action bar.

    Args:
        page: Playwright Page instance.
        url: Full URL or status path (e.g. '/user/status/123').
    Returns:
        True if the action bar rendered with retweet controls, False otherwise.
    Side effects:
        Navigates page, sleeps 2.0 to 3.5 seconds, waits up to 20s for DOM.
    """
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
    """Click the repost action bar button and return available dropdown options.

    Args:
        page: Playwright Page instance with an active status bar.
    Returns:
        List of trimmed text labels for menu items found in the menu dropdown.
    Side effects:
        Clicks the retweet/unretweet button in the DOM, sleeps, waits for menu.
    """
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
    """Dismiss any open popup or dropdown menu by pressing the Escape key.

    Args:
        page: Playwright Page instance.
    Side effects:
        Sends keyboard Escape event to page, sleeps 0.8 seconds.
    """
    try:
        page.keyboard.press("Escape")
        time.sleep(0.8)
    except Exception:
        pass


def click_menu_item(page, label):
    """Click a menu row matching the specified label text case-insensitively.

    Args:
        page: Playwright Page instance with an open menu.
        label: Target label text to match (e.g. "Quote").
    Returns:
        True if matching row was found and clicked, False otherwise.
    Side effects:
        Clicks an element in the browser DOM.
    """
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
    """Click the menu confirm row (Repost or Undo repost).

    Both rows carry data-testid="retweetConfirm", which is exact and immune to
    label drift, so it is tried first before fallback text inspection.

    Args:
        page: Playwright Page instance with open repost menu.
        prefer_testid: If True, attempts data-testid click first.
    Returns:
        True when a click was successfully dispatched, False otherwise.
    Side effects:
        Clicks an element in the browser DOM.
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
    """Wait for a confirmation toast notification and return its text content.

    Args:
        page: Playwright Page instance.
        timeout: Maximum milliseconds to wait for the toast element.
    Returns:
        Extracted toast text string, or None if no toast appears.
    Side effects:
        Waits for DOM selector, blocking execution up to timeout.
    """
    try:
        page.wait_for_selector('[data-testid="toast"]', timeout=timeout)
        return page.evaluate(
            """() => (document.querySelector('[data-testid="toast"]')||{}).innerText || ''""")
    except Exception:
        return None


def new_id_from_toast(page):
    """Extract newly published tweet status ID from the confirmation toast link.

    The toast's View link points directly at the freshly created post.

    Args:
        page: Playwright Page instance displaying a confirmation toast.
    Returns:
        Numeric status ID string if found in toast anchor href, else None.
    Side effects:
        Queries the browser DOM via JavaScript evaluation.
    """
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
    composer dialog disappearing with the URL off /compose/.

    Args:
        page: Playwright Page instance where quote was submitted.
        timeout: Maximum polling duration in seconds.
    Returns:
        State dict with 'toast', 'dialogs', 'url', or None on timeout.
    Side effects:
        Polls DOM every 1.5 seconds, blocking execution up to timeout.
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
    """Fetch recent post IDs and text from the account timeline.

    Used to verify publication after ambiguous toast or dialog outcomes.

    Args:
        page: Playwright Page instance.
        handle: Account username string without '@'.
        limit: Maximum number of recent posts to collect.
    Returns:
        List of dicts [{'id': str, 'text': str}], ordered newest first.
    Side effects:
        Navigates browser to user profile URL and sleeps 4 seconds.
    """
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
    """Collect top-sorted X search results matching query. Read-only.

    Args:
        page: Playwright Page instance.
        query: Search term or query expression string.
        limit: Maximum number of search result articles to return.
    Returns:
        List of dicts [{'handle', 'id', 'url', 'when', 'text'}].
    Side effects:
        Navigates browser to search URL, sleeps 3 to 5 seconds. Does not post.
    """
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
    """Repost a post via the web UI.

    Two attempts: on 2026-09-11 a click that followed a menu-open + Escape in
    the same page silently failed (no toast, no state change), while the same
    call on a freshly loaded page worked. The retry is safe because it only
    fires when the state still reads "not".

    Args:
        page: Playwright Page instance.
        status_url: URL or path of the target post to repost.
        attempts: Maximum number of full repost attempts.
    Returns:
        Tuple of (ok: bool, note: str).
    Side effects:
        Publishes a live repost to X. Modifies account state. Spends no API budget.
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
    """Undo an existing repost via the web UI.

    Args:
        page: Playwright Page instance.
        status_url: URL or path of the target post whose repost is undone.
        attempts: Maximum number of undo attempts.
    Returns:
        Tuple of (ok: bool, note: str).
    Side effects:
        Deletes a repost on X. Modifies account state. Spends no API budget.
    """
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
    """Verify that the in-viewport composer is configured in quote mode for handle.

    Checks the dialog label ('Add a comment') and the attachments card
    referencing the target author handle.

    Args:
        page: Playwright Page instance with active composer.
        handle: Expected tweet author handle (without '@').
    Returns:
        Dict with 'ok' (bool), 'label' (str), and 'attachments' (str).
    Side effects:
        Executes JavaScript DOM inspection in the browser page context.
    """
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
    """Quote-post with a comment via the web UI.

    Publishes a live post on X unless dry=True. The comment is capped at
    MAX_QUOTE_CHARS. The post is NEVER retried on an ambiguous result: a blind
    retry risks a duplicate, so an inconclusive click is resolved by looking for
    a new post on the profile, and only then reported as UNSURE.

    Args:
        page: Playwright Page instance.
        status_url: URL or path of the target post to quote.
        comment: Text string to publish as the quote commentary.
        dry: If True, stages and types the quote without clicking post.
    Returns:
        Tuple of (ok: bool, note: str, new_id: str | None).
    Side effects:
        Publishes a live quote-tweet on the authenticated X account (unless dry).
        Paces input with randomized delays. Spends no API budget.
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
    """CLI entry point to repost, undo, quote, inspect state, or search.

    Parses command line arguments, launches Chromium persistent context,
    checks login status, and executes the specified action.

    Returns:
        Integer exit code: 0 on success, 2 if not logged in, 3 on selector or
        URL error, 4 if action status is inconclusive.
    Side effects:
        Launches browser, queries or publishes to X, and appends log records.
    """
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
