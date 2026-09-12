#!/usr/bin/env python3
"""Mentions engine for @first_sauce_lab: answer the people who reply to us.

Why this exists (Rajat 2026-09-10: "reply to people replying to you
appropriately"): reply_guy_direct.py answers OTHER people's tweets. Nothing
answered the people who reply to THIS account. This engine closes that loop.

Flow:
  1. open x.com/notifications/mentions, collect {author, id, text, time}
  2. filter: right author? fresh? not already answered? not spam/abuse?
  3. draft ONE reply with the same voice system + model chain as reply_guy_direct
  4. gate it hard (length, banned tells, no invented facts)
  5. post via the proven browser_thread.post_one reply flow
  6. record answered ids in logs/mentions_state.json

Volume note: this account gets ~1-2 genuine outside mentions a WEEK, so the
engine is mostly silent. That is expected, not a bug. It prints nothing when
there is nothing to answer, which is what a no_agent cron needs.

Env:
  MENTIONS_DRY_RUN=1   print candidates + drafts, post nothing, write no state
  MENTIONS_MAX=3       replies per run (default 3)
Exit codes: 0 ok (including "stood down, profile busy"), 1 crash, 2 dead
session. Only a genuinely dead login prints NOT LOGGED IN and alerts; profile
contention stays silent by design (see session_verdict below).
"""
import os, re, sys, json, time, random, datetime

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)  # append, NOT insert: bot dir's queue.py must not shadow stdlib

import reply_guy as rg          # state helpers, session check, delays, PROFILE
import reply_guy_direct as rgd  # voice SYSTEM, model chain, gate()
from reply_guy_direct import QUOTE_OPENER_CAP

ME = "first_sauce_lab"
# The operator's own handle is excluded: replying to yourself is noise, and a
# wrong take aimed at your own account owner is worse than silence.
OWNER_HANDLES = {"rajatmshukla"}

STATE_FILE = os.path.join(BOT, "logs", "mentions_state.json")
MENTIONS_LOG = os.path.join(BOT, "logs", "mentions.log")
MENTIONS_URL = "https://x.com/notifications/mentions"

FRESH_HOURS = 72        # mentions are rare; 3 days still reads as timely
MAX_PER_RUN = int(os.environ.get("MENTIONS_MAX", "3"))
MAX_DRAFT_ATTEMPTS = 8
MAX_CHARS = 250         # same X-counter margin as reply_guy_direct

DRY_RUN = os.environ.get("MENTIONS_DRY_RUN") == "1"

# Never answer these: engagement farming, shilling, abuse, bot-speak.
SPAM_PATTERNS = [
    r"\bcheck (your )?dm", r"\bdm me\b", r"t\.me/", r"\bonlyfans\b",
    r"\bfollow (me )?back\b", r"\bpromo\b", r"\bgiveaway\b", r"\bairdrop\b",
    r"\bclick (the )?link", r"\bbuy now\b", r"\bsign ?up\b", r"\bcrypto\b",
    r"\btrading\b", r"\bforex\b", r"\b100x\b", r"\bpump\b",
]
ABUSE_PATTERNS = [
    r"\bidiot\b", r"\bstupid\b", r"\bmoron\b", r"\bshut up\b", r"\bf+u+c+k\b",
    r"\bgarbage bot\b", r"\bbot account\b", r"\bkill yourself\b", r"\bpathetic\b",
]

THANKS_OPENERS = re.compile(
    r"^\W*(thanks|thank you|thx|ty|appreciate it|appreciate that|appreciated|"
    r"means a lot|cheers|glad you|nice one|great point|good point|well said)\b",
    re.I,
)

MENTIONS_TASK = """You are answering a reply that someone sent to @first_sauce_lab on X.

YOUR OWN TWEET that they replied to is given below when available. Use it. A reply that only makes sense if you ignore the parent tweet is a failed reply.

Write ONE reply, as the account, to their tweet. Rules:

- Read what they actually said first. If they asked a question, ANSWER IT in the first line, plainly. Do not deflect into a take.
- If they agreed or added a point, build on THEIR point with something specific and new. Never just agree back.
- If they disagreed, engage the strongest version of their argument. Concede the part that is right, then say where you land and why. Never defensive, never "well actually", never a pile-on.
- A compliment, a joke or a one-word reply gets one short line that lands with substance. NEVER open with thanks, "appreciate it", "means a lot", "cheers", "glad you liked", or "great point" - that reads as an account farming goodwill, which is the opposite of the voice.
- NEVER invent a fact, number, version, benchmark, date, or quote. If the only good answer needs a fact you do not have, output exactly: SKIP
- No questions back as a growth trick. No hashtags. No links. No em dashes or en dashes. Do not open with their @handle, X already shows the thread.
- Max {maxc} characters. Normal sentence case. Never lowercase-everything as a uniform.

YOUR TWEET THEY REPLIED TO:
{context}

THEIR REPLY by @{author}:
{text}
"""


def log(line):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        with open(MENTIONS_LOG, "a", encoding="utf-8") as f:
            f.write(f"{ts} {line}\n")
    except Exception:
        pass
    print(f"[{ts}] {line}", file=sys.stderr)


def load_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {"answered": [], "failed": {}}


def save_state(st):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)


def is_junk(text):
    low = text.lower()
    # A mention that is nothing but our own handle has no content to answer;
    # tagging the account with no words is not a conversation opener.
    if len(re.sub(r"@[\w_]+", "", low).strip()) < 3:
        return "bare-handle"
    if any(re.search(p, low) for p in SPAM_PATTERNS):
        return "spam"
    if any(re.search(p, low) for p in ABUSE_PATTERNS):
        return "abuse"
    return None


def scrape_mentions(page):
    """Return [{author, id, text, time}] from the mentions tab."""
    page.goto(MENTIONS_URL, wait_until="domcontentloaded", timeout=60_000)
    rg.human_delay(4, 6)
    for _ in range(3):
        page.mouse.wheel(0, 1400)
        time.sleep(random.uniform(1.5, 2.5))
    out = []
    arts = page.locator('article[data-testid="tweet"]')
    n = min(arts.count(), 25)
    for i in range(n):
        a = arts.nth(i)
        try:
            link = a.locator('a[href*="/status/"]').first
            if not link.count():
                continue
            href = link.get_attribute("href") or ""
            if "/status/" not in href:
                continue
            parts = href.split("/")
            ai = parts.index("status")
            author = parts[ai - 1]
            tid = parts[ai + 1].split("?")[0]
            t = a.locator('[data-testid="tweetText"]')
            txt = t.first.inner_text().strip() if t.count() else ""
            te = a.locator("time").first
            when = te.get_attribute("datetime") if te.count() else ""
            out.append({"author": author, "id": tid, "text": txt, "time": when})
        except Exception:
            continue
    return out


def fetch_parent_text(page, author, tid):
    """Return the tweet this mention replied to (usually ours), or "".

    On a status page X renders the focal tweet as the first article that links
    to itself, with the parent tweet in the article just above it. If the
    layout differs we return "" and the prompt degrades to no-context rather
    than inventing a parent.
    """
    try:
        page.goto(f"https://x.com/{author}/status/{tid}",
                  wait_until="domcontentloaded", timeout=60_000)
        rg.human_delay(3, 5)
        arts = page.locator('article[data-testid="tweet"]')
        seen = []
        for i in range(min(arts.count(), 4)):
            a = arts.nth(i)
            link = a.locator('a[href*="/status/"]').first
            href = link.get_attribute("href") if link.count() else ""
            t = a.locator('[data-testid="tweetText"]')
            txt = t.first.inner_text().strip() if t.count() else ""
            seen.append((href or "", txt))
        for i, (href, _txt) in enumerate(seen):
            if tid in href:
                for j in range(i - 1, -1, -1):
                    if seen[j][1]:
                        return seen[j][1][:600]
                return ""
        # focal article did not match the id: take the first non-empty text
        for _href, txt in seen:
            if txt:
                return txt[:600]
        return ""
    except Exception as e:
        log(f"parent-context fetch failed for {tid}: {type(e).__name__}: {e}")
        return ""


def fresh(dt):
    """True when a timestamp is inside the answer window. Unparseable = False."""
    try:
        t = datetime.datetime.fromisoformat(dt.replace("Z", "+00:00"))
        age = datetime.datetime.now(datetime.timezone.utc) - t
        return 0 <= age.total_seconds() < FRESH_HOURS * 3600
    except Exception:
        return False


def session_verdict(page):
    """'ok' | 'busy' | 'dead' via the shared guard.

    busy = the login is intact, the UI just did not hydrate. On 2026-09-11
    13:41 this engine called a stale check_session, saw a logged-out shell
    because a one-shot verify_x_account.py run held the same Chromium profile,
    and exited 2. That sent a red cron alert to Discord reading "NOT LOGGED
    IN - run browser_login.py" for a session that was fine the whole time.
    Contention is not an auth failure and must never be reported as one.
    """
    import browser_guard
    verdict, detail = browser_guard.classify_session(page, verbose=DRY_RUN)
    log(f"session verdict: {verdict} ({detail})")
    return verdict


def post_batch(items):
    """Post accepted replies in one browser context. -> (posted, failed, err)."""
    import browser_thread
    browser_thread.USER = ME
    posted, failed = [], []
    with __import__("playwright").sync_api.sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            rg.PROFILE, headless=True, viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        verdict = session_verdict(page)
        if verdict != "ok":
            ctx.close()
            if verdict in ("dead", "stalled"):
                return posted, failed, "NOT_LOGGED_IN"
            return posted, failed, "PROFILE_BUSY"
        for tid, take in items:
            try:
                new_id = browser_thread.post_one(page, take, reply_to=tid)
                if new_id:
                    posted.append((tid, new_id))
                    log(f"ANSWERED {tid} -> {new_id}")
                else:
                    failed.append(tid)
                    log(f"ANSWER FAILED {tid}")
            except Exception as e:
                failed.append(tid)
                log(f"ANSWER FAILED {tid}: {type(e).__name__}: {e}")
            time.sleep(random.uniform(4, 8))
        ctx.close()
    return posted, failed, None


def main():
    # Another engine may be using the shared X profile; retry on the next slot.
    if not rg.acquire_browser_lock("mentions_guy"):
        import browser_guard
        log(f"standing down: {browser_guard.busy_reason()}")
        return 0  # silent on stdout: the profile is busy, not an error
    import atexit
    atexit.register(rg.release_browser_lock)

    st = load_state()
    st.setdefault("answered", [])
    st.setdefault("failed", {})
    answered = set(st["answered"])
    black = {tid for tid, n in st["failed"].items() if n >= 2}

    with __import__("playwright").sync_api.sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            rg.PROFILE, headless=True, viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        verdict = session_verdict(page)
        if verdict == "dead":
            ctx.close()
            print("mentions guy: NOT LOGGED IN - run browser_login.py")
            return 2
        if verdict == "stalled":
            ctx.close()
            stre = __import__("browser_guard").health().get("consecutive_unusable")
            print(f"mentions guy: no usable X session for {stre} runs in a row "
                  f"(profile contention or an expired login). "
                  f"Run browser_login.py if this persists.")
            return 2
        if verdict == "busy":
            ctx.close()
            # Another engine holds the profile. Nothing is wrong with the
            # login, so say nothing and let the next hourly slot try again.
            return 0
        raw = scrape_mentions(page)

        cands, skipped = [], []
        for m in raw:
            h = m["author"].lower()
            if h == ME:
                continue
            if h in OWNER_HANDLES:
                skipped.append((m["id"], "owner"))
                continue
            if m["id"] in answered or m["id"] in black:
                continue
            if not m["text"]:
                skip = "no-text"
            elif not fresh(m["time"]):
                skip = "stale"
            else:
                skip = is_junk(m["text"])
            if skip:
                skipped.append((m["id"], skip))
                continue
            cands.append(m)

        # Parent context: a reply to "underrated tweet" is only answerable if
        # you know WHICH tweet. One extra page load per candidate, and the
        # volume here is ~1-2 a week, so it is always worth it.
        for m in cands[:MAX_PER_RUN]:
            m["context"] = fetch_parent_text(page, m["author"], m["id"])
            rg.human_delay(1.5, 3)
        ctx.close()

    if DRY_RUN:
        print(f"mentions guy DRY RUN: {len(raw)} on page, {len(cands)} answerable")
        for m in cands:
            print(f"  @{m['author']} [{m['time']}] {m['text'][:160]}")
            print(f"     parent: {(m.get('context') or '(none)')[:160]}")
        for tid, why in skipped:
            print(f"  SKIP {tid} ({why})")
    if not cands:
        return 0  # nothing to answer: silent

    takes, attempts = [], 0
    for m in cands:
        if len(takes) >= MAX_PER_RUN or attempts >= MAX_DRAFT_ATTEMPTS:
            break
        attempts += 1
        prompt = MENTIONS_TASK.format(
            maxc=MAX_CHARS, author=m["author"], text=m["text"][:600],
            context=m.get("context") or "(not available - do not guess what it said)")
        txt, mdl = rgd.llm(prompt)
        if txt == rgd.KEY_DEAD:
            print("mentions guy: OPENROUTER key rejected (401) - check hermes .env")
            return 1
        if txt is None:
            continue
        take = rgd.gate(txt)
        if not take:
            continue
        # extra gates that only matter when the target is a person, not a feed
        if take.lstrip().startswith("@"):
            continue
        if "http://" in take or "https://" in take:
            continue
        if THANKS_OPENERS.match(take):
            continue  # gratitude openers read as goodwill farming
        # one quote-opener per run, then the drafter has to find another shape
        if (rg.PACKAGING_RE.search(take)
                and sum(1 for _, p in takes if rg.PACKAGING_RE.search(p))
                >= QUOTE_OPENER_CAP):
            log(f"DROPPED (quote-opener quota spent): {m['id']}")
            continue
        if any(take[:40].lower() == prev[:40].lower() for _, prev in takes):
            continue
        log(f"DRAFT via {mdl}: {m['id']} ({take[:80]})")
        takes.append((m["id"], take))

    if not takes:
        return 0
    if DRY_RUN:
        print("mentions guy DRY RUN drafts (NOT posted):")
        for tid, take in takes:
            print(f"  -> {tid}: {take}")
        return 0

    posted, failed, err = post_batch(takes)
    if err == "NOT_LOGGED_IN":
        print("mentions guy: NOT LOGGED IN - run browser_login.py")
        return 2
    if err == "PROFILE_BUSY":
        # Lost the profile between the session check and posting. The drafts
        # are NOT consumed and no state is written, so the next slot re-derives
        # them. Silent by design.
        log("post_batch: profile went busy mid-run, standing down")
        return 0
    for tid, _new in posted:
        if tid not in st["answered"]:
            st["answered"].append(tid)
        st["failed"].pop(tid, None)
    for tid in failed:
        st["failed"][tid] = st["failed"].get(tid, 0) + 1
    save_state(st)  # persist after EVERY batch so a crash cannot double-answer
    if posted:
        print(f"mentions guy: answered {len(posted)}"
              + (f", {len(failed)} failed" if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
