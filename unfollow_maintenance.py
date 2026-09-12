#!/usr/bin/env python3
"""Unfollow maintenance for @first_sauce_lab.

Why this exists (2026-09-11, Rajat's call after Antigravity's reach audit):
the account followed 250 accounts while having 26 followers. A visitor sees
that ratio before they read a single post, and it reads as a follow-back
account. Follow-spread is now paused; this walks the following list down to a
floor, keeping the builders the reply engine actually engages with.

Rules:
  - keep every handle in reply_guy.TARGETS (that is who we reply to)
  - keep anyone the account follows who is NOT in the pool we spread into,
    unless we run short of candidates -- deliberately conservative
  - 18 unfollows a day by default, 15-45s randomized gap between each
  - click, then CONFIRM the button flipped to "Follow" before counting it
  - stop early once the following count reaches the floor

Silent when it has nothing to do or nothing worked, so a no-op cron run sends
no message. Prints one line only when it actually unfollowed someone.

  python3 unfollow_maintenance.py [--max 18] [--floor 80] [--dry]
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
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {line}\n")


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"unfollowed": {}}


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)


def read_following_count(page):
    """'250 Following' -> 250, from the profile page."""
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
    """[{handle, can_unfollow}] for the rows currently rendered."""
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=18)
    ap.add_argument("--floor", type=int, default=80)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    if not browser_guard.hold("unfollow_maintenance"):
        log(f"stand down: profile busy ({browser_guard.busy_reason()})")
        return 0

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

        budget = min(a.max, n - a.floor)
        page.goto(f"https://x.com/{HANDLE}/following", wait_until="domcontentloaded",
                  timeout=60_000)
        rg.human_delay(3, 5)

        tried = set()
        rounds = 0
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

            rg.human_delay(15, 45)  # deliberate, human-paced

        ctx.close()

    if unfollowed:
        print(f"unfollow maintenance: {len(unfollowed)} unfollowed "
              f"(following was {n}, floor {a.floor})")
    return 0 if unfollowed or not st else 1


if __name__ == "__main__":
    sys.exit(main())
