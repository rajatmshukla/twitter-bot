#!/usr/bin/env python3
"""Reply-guy engine for First Sauce Labs: scrape big AI accounts, post takes.

Modes:
  scrape                     -> print JSON candidates (tweets not yet seen/replied)
  reply <tweet_id> -f f.txt  -> post file text as a reply to that tweet
  state                      -> show replied/seen counts

State file: logs/reply_guy_state.json  ({"seen": {handle: [ids]}, "replied": [ids]})
Read-only scrape; the only write action is reply (posts to X).
"""
import os, re, sys, json, time, random, argparse, datetime
from urllib.parse import quote

from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")
STATE_FILE = os.path.join(BOT, "logs", "reply_guy_state.json")
REPLY_LOG = os.path.join(BOT, "logs", "replies.log")

# Accounts + influential people to monitor. Verified 2026-08-10 via reply_guy.py verify.
# Labs/leaders first, then individual voices (OpenAI/Anthropic + strong AI commentators).
TARGETS = [
    # lab accounts + top leaders
    # Culled 2026-09-11 (Rajat's call, Antigravity's reach audit): the
    # >1M-follower accounts. A reply at position 400 of a @sama thread is
    # invisible; the account's reach has to come from threads small enough to
    # put the reply on the first screen. Re-add only with evidence it worked.
    "AnthropicAI",
    # OpenAI / ex-OpenAI people
    "karpathy", "gdb", "miramurati", "ilyasut", "kevinweil", "markchen90",
    # Anthropic people
    "jackclarkSF", "janleike",
    # influential AI voice (Toby Walsh — @TobyWalsh, UNSW AI professor)
    "TobyWalsh",
    # added by Rajat 2026-08-11: @thsottiaux
    "thsottiaux",
    # added 2026-08-13 growth pass (verify before first use)
    "ylecun", "simonw", "goodside", "hwchase17", "_philschmid", "AndrewYNg", "sundarpichai",
    # added by Rajat 2026-08-15: @jun_song (verified live via reply_guy.py verify — DeepSeek)
    "jun_song",
    # suraj_sharma14 45-account list 2026-09-04: all followed same day; live
    # confirmed by successful follow profile loads 2026-09-04 12:57-12:58
    "swyx", "emollick", "chipro", "rasbt", "fchollet", "DrJimFan", "natolambert",
    "jerryjliu0", "alexalbert__", "AmandaAskell", "DarioAmodei", "demishassabis",
    "claudeai", "AIatMeta", "huggingface", "LangChain", "llama_index", "cursor_ai",
    "vercel", "shadcn", "levelsio", "amasad", "jxnlco", "eugeneyan", "HamelHusain",
    "lateinteraction", "mckaywrigley", "rowancheung", "TheRundownAI",
    "ClementDelangue", "yoheinakajima", "mattshumer_", "AIHighlight",
    # added 2026-09-11 (Rajat's call, Antigravity's reach audit): builders in
    # the 15k-150k range whose threads are small enough that a good reply is
    # actually read. Previously the engine replied under accounts too big to
    # be seen at all.
    "victormustar", "Teknium1",
]
MAX_PER_TARGET = 8
# 2026-09-11: was 48h. A reply on a 2-day-old post lands where nobody is
# looking; the audience has moved on, so the effort is wasted. 6h keeps the
# engine in threads that are still being read.
FRESH_HOURS = 6

# AI keywords for the trending search scrape (Top-sorted = high engagement).
TRENDING_QUERIES = ["AI", "GPT", "Claude", "OpenAI", "Anthropic", "AGI", "LLM", "Gemini", "Grok"]
MAX_TRENDING = 10

def normalize_dashes(t):
    """Rajat's rule: no em or en dashes in published prose.

    Rewrite rather than reject. Found 2026-09-10 by scripts/test_agy_path.py:
    Antigravity (agy) writes em dashes by habit, and reply_guy_direct.gate()
    hard-rejected them, so every keyless-fallback take was silently discarded
    and the path never posted. Normalising keeps a good take and still ships
    clean text.
    """
    if not t:
        return t
    t = re.sub(r"\s*[\u2014\u2013]\s*", ", ", t)   # em/en dash -> comma break
    t = re.sub(r",\s*(?=[,.;:!?])", "", t)          # ", ." -> "."
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


# ---- dead-move ban (Rajat, 2026-09-12) ----
# The voice skill banned "X is doing a lot of work / heavy lifting" verbally on
# 2026-09-02, and it still shipped 12 times in the 9 days after that: a rule the
# drafter never re-reads does not hold. So the ban lives here, in code, shared by
# both reply paths (reply_guy.cmd_reply and reply_guy_direct.gate).
#
# Why it is dead: it points at the tweet's wording instead of its claim, so the
# reader learns nothing. Any account can write it without knowing the topic,
# which is why it now reads as the universal reply-guy tell.
DEAD_MOVE_RE = re.compile(
    r"\bheavy lifting\b"
    r"|\b(?:is|are|was|were|be|been|being|keeps?|kept|has|have|had)\s+doing\s+"
    r"(?:a lot of |all of the |most of the |the |some |too much )?(?:work|the work)\b"
    r"|\b(?:do|does|doing|done)\s+(?:a lot of |most of the |the |some )?(?:work|the work)\b",
    re.I)

# The same move without the idiom: open by quoting a phrase out of the tweet,
# then pass judgment on the wording ("could actually help humanity" is a bold
# claim). Sometimes the rest of the take carries real substance, so the direct
# path caps this shape per run instead of banning it.
PACKAGING_RE = re.compile(
    r"^\s*[\"'\u2018\u201c\u2019\u201d].{1,70}[\"'\u2018\u201c\u2019\u201d]\s*(?:is|are|was|were|keeps?|has)\b",
    re.I)


def human_delay(a=1.0, b=2.5):
    time.sleep(random.uniform(a, b))

# ---- single-instance guard for the shared X profile ----
# Every engine here launches the SAME persistent Chromium profile
# (twitter-bot/browser-profile). Two runs at once trample each other and the
# second one lands on a logged-out page, which reads as "run browser_login.py"
# and looks like an auth failure (observed 2026-09-10 when the reply engine
# and the mentions engine overlapped). Timestamp-based, because a process-liveness
# check on Windows needs PowerShell and is not worth it: a lock older than
# LOCK_STALE_SECONDS is abandoned and taken over.
LOCK_FILE = os.path.join(BOT, "logs", "browser.lock")
LOCK_STALE_SECONDS = 2700  # 45 min; no engine run legitimately exceeds this


def acquire_browser_lock(name="reply_guy"):
    """True if this run may use the browser profile, False if another run holds it.

    Delegates to browser_guard (single implementation for every engine).
    """
    import browser_guard
    return browser_guard.acquire(name)


def release_browser_lock():
    import browser_guard
    browser_guard.release()

def log(line):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with open(REPLY_LOG, "a", encoding="utf-8") as f:
        f.write(f"{ts} {line}\n")
    print(f"[{ts}] {line}", file=sys.stderr)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            return {"seen": {}, "replied": []}
    return {"seen": {}, "replied": []}

def save_state(st):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)

def check_session(page):
    """True only if the page shows real logged-in UI.

    Delegates to browser_guard so there is ONE implementation. Before
    2026-09-11 this function, browser_post.check_session and
    browser_thread.check_session were three separate copies, and hardening one
    left the others broken (mentions_guy used this one and kept reporting
    NOT LOGGED IN for a perfectly healthy session).
    """
    import browser_guard
    return browser_guard.check_session(page)

def scrape_profile(page, handle):
    """Return list of {id, text, time} for the handle's own recent tweets."""
    out = []
    page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded", timeout=60_000)
    human_delay(2.5, 4.5)
    try:
        page.wait_for_selector('article[data-testid="tweet"]', timeout=20_000)
    except Exception:
        log(f"WARN: no tweets visible for @{handle} (maybe blocked or empty)")
        return out
    human_delay(1, 2)
    arts = page.locator('article[data-testid="tweet"]')
    n = min(arts.count(), 12)
    for i in range(n):
        art = arts.nth(i)
        try:
            link = art.locator('a[href*="/status/"]').first
            if not link.count():
                continue
            href = link.get_attribute("href") or ""
            if f"/{handle}/status/" not in href:
                continue  # retweet or promo from another account
            tid = href.split("/status/")[1].split("?")[0]
            txt_el = art.locator('[data-testid="tweetText"]')
            txt = txt_el.first.inner_text() if txt_el.count() else ""
            t_el = art.locator("time").first
            t = t_el.get_attribute("datetime") if t_el.count() else ""
            out.append({"id": tid, "text": txt[:280], "time": t})
        except Exception:
            continue
    return out

def scrape_search(page, query, live=False):
    """Return list of {author, id, text, time} from search results.

    live=False -> Top-sorted (high engagement, for the standing trending list).
    live=True  -> Latest-sorted (what broke in the last hour, for news topics).
    """
    out = []
    f = "live" if live else "top"
    url = f"https://x.com/search?q={quote(query)}&f={f}"
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    human_delay(2.5, 4.5)
    try:
        page.wait_for_selector('article[data-testid="tweet"]', timeout=20_000)
    except Exception:
        log(f"WARN: no results for search {query!r}")
        return out
    # scroll a bit to load more results
    for _ in range(2):
        page.mouse.wheel(0, 2500)
        time.sleep(1.5)
    arts = page.locator('article[data-testid="tweet"]')
    n = min(arts.count(), 12)
    for i in range(n):
        art = arts.nth(i)
        try:
            link = art.locator('a[href*="/status/"]').first
            if not link.count():
                continue
            href = link.get_attribute("href") or ""
            if "/status/" not in href:
                continue
            parts = href.split("/")
            ai = parts.index("status")
            author = parts[ai - 1] if ai >= 1 else "?"
            tid = parts[ai + 1].split("?")[0]
            txt_el = art.locator('[data-testid="tweetText"]')
            txt = txt_el.first.inner_text() if txt_el.count() else ""
            if not txt:
                continue
            t_el = art.locator("time").first
            t = t_el.get_attribute("datetime") if t_el.count() else ""
            out.append({"author": author, "id": tid, "text": txt[:280], "time": t})
        except Exception:
            continue
    return out

def cmd_trending():
    st = load_state()
    replied = set(st.get("replied", []))
    seen_trending = set(st.get("seen", {}).get("_trending", []))
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True, viewport={"width":1280,"height":900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not check_session(page):
            ctx.close()
            print(json.dumps({"error": "not logged in", "candidates": []}))
            return 2
        found = {}
        for q in TRENDING_QUERIES:
            for t in scrape_search(page, q):
                tid = t["id"]
                if tid in replied or tid in seen_trending or tid in found:
                    continue
                if t["author"].lower() == "first_sauce_lab":
                    continue
                if not t["time"] or _is_fresh(t["time"]):
                    found[tid] = t
            human_delay(2, 4)
        ctx.close()
    candidates = list(found.values())[:MAX_TRENDING]
    # mark seen so skipped trending posts aren't re-suggested every run
    st.setdefault("seen", {})
    st["seen"]["_trending"] = (list(seen_trending) + [t["id"] for t in candidates])[-40:]
    save_state(st)
    print(json.dumps({"candidates": candidates}, indent=1))
    return 0

def cmd_scrape():
    st = load_state()
    seen = st.get("seen", {})
    replied = set(st.get("replied", []))
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True, viewport={"width":1280,"height":900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not check_session(page):
            ctx.close()
            print(json.dumps({"error": "not logged in", "candidates": []}))
            return 2
        candidates = []
        for handle in TARGETS:
            try:
                tweets = scrape_profile(page, handle)
            except Exception as e:
                log(f"scrape {handle} failed, skipping: {e}")
                continue
            fresh = [t for t in tweets if not t["id"] in replied
                     and not t["id"] in seen.get(handle, [])
                     and (not t["time"] or _is_fresh(t["time"]))]
            # keep newest MAX_PER_TARGET
            fresh.sort(key=lambda t: t["time"], reverse=True)
            for t in fresh[:MAX_PER_TARGET]:
                candidates.append({"handle": handle, **t})
            seen.setdefault(handle, [])
            seen[handle] = (seen[handle] + [t["id"] for t in tweets])[-20:]
            human_delay(2, 4)
        ctx.close()
    st["seen"] = seen
    save_state(st)
    print(json.dumps({"candidates": candidates}, indent=1))
    return 0

def _is_fresh(dt):
    try:
        t = datetime.datetime.fromisoformat(dt.replace("Z", "+00:00"))
        age = datetime.datetime.now(datetime.timezone.utc) - t
        return age.total_seconds() < FRESH_HOURS * 3600
    except Exception:
        return True

def cmd_reply(tid, text_file):
    text = open(text_file, encoding="utf-8").read().strip()
    if not text:
        log("ERROR: empty reply text")
        return 4
    if len(text) > 280:
        log(f"ERROR: reply too long ({len(text)})")
        return 4
    # Dead-move gate. Refuse rather than rewrite: the agent path drafts one take
    # at a time and can fix it immediately, and a take that survives because the
    # gate was lenient is the exact failure this is here to stop.
    if DEAD_MOVE_RE.search(text):
        log(f"REFUSED dead move (wording critique / heavy-lifting idiom) "
            f"for {tid}: {text[:90]}")
        return 4
    if PACKAGING_RE.search(text):
        log(f"REFUSED quote-then-judgment opener for {tid}: {text[:90]}")
        return 4
    import browser_thread  # reuses the proven reply+post flow
    browser_thread.USER = "first_sauce_lab"
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True, viewport={"width":1280,"height":900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not check_session(page):
            log("NOT LOGGED IN — run browser_login.py")
            ctx.close()
            return 2
        new_id = browser_thread.post_one(page, text, reply_to=tid)
        ctx.close()
    if new_id is None:
        log(f"REPLY FAILED to {tid}")
        return 3
    st = load_state()
    st.setdefault("replied", [])
    if tid not in st["replied"]:
        st["replied"].append(tid)
    save_state(st)
    log(f"REPLIED to {tid} -> {new_id}")
    print(f"REPLIED {tid} -> {new_id}")
    return 0

def cmd_state():
    st = load_state()
    print(f"replied: {len(st.get('replied', []))}")
    for h, ids in st.get("seen", {}).items():
        print(f"seen {h}: {len(ids)}")

def cmd_verify(handles):
    """Check which handles exist and are active (own tweets visible)."""
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True, viewport={"width":1280,"height":900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not check_session(page):
            ctx.close()
            print("NOT LOGGED IN")
            return 2
        for handle in handles:
            tweets = scrape_profile(page, handle)
            if tweets:
                first = tweets[0]["text"][:80].replace("\n", " ")
                print(f"OK   @{handle:16s} {len(tweets):2d} tweets | {first}")
            else:
                print(f"EMPTY @{handle:16s} no own tweets found (bad handle / no posts / blocked)")
            human_delay(1.5, 3)
        ctx.close()
    return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scrape", "reply", "state", "verify", "trending"])
    ap.add_argument("tweet_id", nargs="?", default=None)
    ap.add_argument("-f", "--file", default=None)
    ap.add_argument("handles", nargs="*")
    args = ap.parse_args()
    if args.cmd == "scrape":
        return cmd_scrape()
    if args.cmd == "trending":
        return cmd_trending()
    if args.cmd == "reply":
        if not args.tweet_id or not args.file:
            print("usage: reply_guy.py reply <tweet_id> -f take.txt", file=sys.stderr)
            return 4
        return cmd_reply(args.tweet_id, args.file)
    if args.cmd == "state":
        cmd_state()
        return 0
    if args.cmd == "verify":
        handles = ([args.tweet_id] if args.tweet_id else []) + args.handles
        if not handles:
            print("usage: reply_guy.py verify <handle> [handle...]", file=sys.stderr)
            return 4
        return cmd_verify(handles)

if __name__ == "__main__":
    sys.exit(main())
