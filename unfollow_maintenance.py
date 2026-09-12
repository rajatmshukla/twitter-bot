#!/usr/bin/env python3
"""Unfollow maintenance for @first_sauce_lab.

Why this exists (2026-09-11, Rajat's call after Antigravity reach audit):
the account followed 250 accounts while having 26 followers. A visitor sees
that ratio before reading a single post, and it reads as a follow-back
account. Follow-spread is now paused; this walks the following list down to a
floor, keeping the builders the reply engine actually engages with.

Operational rules:
    - Keep every handle in reply_guy.TARGETS (the accounts the bot replies to).
    - Unfollow at most 18 accounts per run by default.
    - Sleep 15-45s between unfollows to emulate human cadence.
    - Click unfollow, confirm modal if prompted, and verify the button flipped.
    - Stop early once the following count reaches the configured floor.
    - Silent when idle or unsuccessful so scheduled runs do not create noise.
      Prints a summary line only when accounts are unfollowed.

Invocation:
    CLI or cron job:
        python3 unfollow_maintenance.py [--max 18] [--floor 80] [--dry]
    Specific cron schedule is not defined within this file.

Inputs and Outputs:
    Reads:
        - logs/unfollow_state.json (history of previously unfollowed handles).
        - reply_guy.TARGETS (protected account handles).
        - browser_guard lock and session status.
        - Live X following page DOM elements.
    Writes:
        - logs/unfollow_state.json (records newly unfollowed handles).
        - logs/unfollow.log (detailed execution and error log).
        - stdout: prints run summary on changes or dry run.

Live Account Effects:
    Acquires browser lock via browser_guard, launches persistent browser context,
    navigates to following page, clicks unfollow buttons, handles confirmation
    prompts, sleeps between requests, and decrements following count.
"""
import argparse
import datetime
import json
import os
import random
import sys
import time

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
import reply_guy as rg
import browser_guard
from playwright.sync_api import sync_playwright

HANDLE = "first_sauce_lab"
STATE = os.path.join(BOT, "logs", "unfollow_state.json")
LOG = os.path.join(BOT, "logs", "unfollow.log")


def log(line):
    """Write timestamped log message to unfollow log file.

    Args:
        line: Message string to write.

    Side effects:
        Ensures directory exists and appends timestamped line to LOG.
    """
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {line}\n")


def load_state():
    """Load previously unfollowed accounts state from disk.

    Returns:
        Dict loaded from unfollow_state.json, or {'unfollowed': {}} on error.

    Side effects:
        Reads STATE file from disk if present.
    """
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"unfollowed": {}}


def save_state(st):
    """Persist unfollowed accounts state to disk as JSON.

    Args:
        st: Dict containing unfollow tracking state.

    Side effects:
        Creates parent directory if missing and overwrites STATE file.
    """
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)


def read_following_count(page):
    """Extract numeric Following count from user profile page.

    Navigates to the profile page for HANDLE, waits for DOM content, and
    parses the text of the following count anchor (handling K/M suffixes).

    Args:
        page: Playwright Page instance with active session.

    Returns:
        Integer count of accounts followed, or None if extraction failed.

    Side effects:
        Navigates page to profile URL, sleeps 3-5 seconds.
    """
    page.goto(f"https://x.com/{HANDLE}", wait_until="domcontentloaded", timeout=60_000)
    rg.human_delay(3, 5)
    n = page.evaluate("""(h) => {
        const a = document.querySelector(`a[href="/${h}/following"]`);
        if (!a) return null;
        const m = (a.innerText || '').match(/([\\d.,]+)\\s*[KMB]?/i);
        if (!m) return null;
        const raw = m[0].replace(/,/g, '');
        const mult = /K/i.test(raw) ? 1e3 : /M/i.test(raw) ? 1e6 : 1;
        return Math.round(parseFloat(raw) * mult);
    }""", HANDLE)
    return n


def visible_following(page):
    """Extract list of visible user handles and unfollow status from DOM.

    Inspects rendered UserCell elements in the current DOM view.

    Args:
        page: Playwright Page instance loaded on following page.

    Returns:
        List of dicts: [{'handle': str, 'can_unfollow': bool}, ...].

    Side effects:
        Evaluates JavaScript in browser DOM without mutating page state.
    """
    return page.evaluate("""() => {
        const out = [];
        for (const cell of document.querySelectorAll('[data-testid="UserCell"]')) {
            const a = cell.querySelector('a[href^="/"]');
            if (!a) continue;
            const h = a.getAttribute('href').replace(/^\\//, '').split('/')[0];
            if (!h || h.includes('?')) continue;
            const btn = cell.querySelector('button[data-testid$="-unfollow"]');
            out.push({handle: h, can_unfollow: !!btn});
        }
        return out;
    }""")


def main():
    """Execute unfollow maintenance pass to gradually lower following count.

    Parses CLI arguments (--max, --floor, --dry), acquires browser mutex via
    browser_guard, inspects current following count, and unfollows non-whitelisted
    accounts until budget or floor is reached. Verifies each unfollow button flip.

    Returns:
        0 on success, dry run, or when following count is at or below floor.
        1 on extraction failure or candidate exhaustion without unfollows.
        2 on non-busy session classification failure.

    Side effects:
        Acquires and releases browser mutex, mutates live account following list,
        sleeps 15-45 seconds between unfollow clicks, updates JSON state file,
        appends to log file.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=18)
    ap.add_argument("--floor", type=int, default=80)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    # Prevent concurrent browser instances from colliding on user profile data.
    if not browser_guard.hold("unfollow_maintenance"):
        log(f"stand down: profile busy ({browser_guard.busy_reason()})")
        return 0

    # Whitelist reply targets so the bot maintains engagement with key accounts.
    keep = {h.lower() for h in rg.TARGETS}
    st = load_state()
    done = st.setdefault("unfollowed", {})
    unfollowed = []

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            rg.PROFILE, headless=True, viewport={"width": 1280, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        verdict, detail = browser_guard.classify_session(page)
        if verdict != "ok":
            log(f"stand down: session {verdict} ({detail})")
            ctx.close()
            return 0 if verdict == "busy" else 2

        n = read_following_count(page)
        if n is None:
            log("could not read the following count")
            ctx.close()
            return 1
        log(f"following={n} floor={a.floor} max={a.max} dry={a.dry}")
        if n <= a.floor:
            print(f"following {n} is at or below the floor of {a.floor}; nothing to do")
            ctx.close()
            return 0

        # Bound unfollows to avoid sudden drops while respecting the configured floor.
        budget = min(a.max, n - a.floor)
        page.goto(f"https://x.com/{HANDLE}/following", wait_until="domcontentloaded",
                  timeout=60_000)
        rg.human_delay(3, 5)

        tried = set()
        rounds = 0
        # Cap scroll pagination at 12 rounds to avoid looping if list end is reached.
        while len(unfollowed) < budget and rounds < 12:
            rounds += 1
            rows = visible_following(page)
            candidates = [r for r in rows
                          if r["can_unfollow"]
                          and r["handle"].lower() not in keep
                          and r["handle"].lower() not in done
                          and r["handle"].lower() not in tried
                          and r["handle"].lower() != HANDLE]
            if not candidates:
                # Scroll down ~2-3 viewports to trigger dynamic list item rendering.
                page.mouse.wheel(0, 2200)
                rg.human_delay(2, 3.5)
                more = [r for r in visible_following(page)
                        if r["handle"].lower() not in tried]
                if not more:
                    break
                continue

            row = candidates[0]
            h = row["handle"]
            tried.add(h.lower())
            btn = page.locator(
                f'[data-testid="UserCell"]:has(a[href="/{h}"]) '
                f'button[data-testid$="-unfollow"]').first

            if a.dry:
                print(f"DRY: would unfollow @{h}")
                unfollowed.append(h)
                rg.human_delay(0.4, 0.9)
                continue

            try:
                btn.scroll_into_view_if_needed(timeout=10_000)
                btn.click(timeout=15_000)
            except Exception as e:
                log(f"FAIL click @{h}: {type(e).__name__}")
                continue

            rg.human_delay(2, 3.5)
            # X sometimes asks to confirm.
            try:
                sheet = page.locator('[data-testid="confirmationSheetConfirm"]')
                if sheet.count() > 0 and sheet.first.is_visible():
                    sheet.first.click(timeout=8_000)
                    rg.human_delay(1.5, 2.5)
            except Exception:
                pass

            flipped = page.evaluate("""(h) => {
                const cell = [...document.querySelectorAll('[data-testid="UserCell"]')]
                    .find(c => {
                        const a = c.querySelector('a[href^="/"]');
                        return a && a.getAttribute('href') === '/' + h;
                    });
                if (!cell) return null;
                return !cell.querySelector('button[data-testid$="-unfollow"]');
            }""", h)

            if flipped:
                unfollowed.append(h)
                done[h.lower()] = datetime.datetime.now().isoformat(timespec="seconds")
                save_state(st)
                log(f"UNFOLLOWED @{h} ({len(unfollowed)}/{budget})")
            else:
                log(f"FAIL @{h} (button did not flip)")

            # Deliberate 15-45s human-paced delay prevents triggering X bot detection.
            rg.human_delay(15, 45)

        ctx.close()

    if unfollowed:
        print(f"unfollow maintenance: {len(unfollowed)} unfollowed "
              f"(following was {n}, floor {a.floor})")
    return 0 if unfollowed or not st else 1


if __name__ == "__main__":
    sys.exit(main())
