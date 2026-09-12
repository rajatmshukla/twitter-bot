#!/usr/bin/env python3
"""Direct-API reply-guy engine for @first_sauce_lab (no agent loop).

Replaces the agent-driven twitter-reply-guy cron (Rajat 2026-09-04: stop
burning DeepSeek tokens on agent-context re-reads). This script:
  1. scrapes TARGET profiles + trending searches via reply_guy.py helpers
  2. drafts takes with direct LLM calls (Gemini/OpenRouter free chain/agy)
  3. code-gates each take (length, banned machine tells, catchphrases)
  4. posts up to 8 via browser_thread.post_one (proven reply flow)
  5. updates reply_guy_state.json exactly like the old flow

Entered as a CLI script directly or invoked via reply_guy_direct_cron.py wrapper:
  python reply_guy_direct.py

Side effects:
- Launches Chromium browser with persistent profile under browser-profile/.
- Acquires and releases profile lock at logs/browser.lock.
- Queries X network endpoints for profiles, searches, and status pages.
- Makes outbound HTTP requests to LLM APIs (Gemini, OpenRouter) or runs agy CLI.
- Reads and mutates state JSON at logs/reply_guy_state.json.
- Appends execution records to logs/replies.log.
- Publishes reply tweets to X via browser_thread.post_one.

Cost: ~1 small API call per candidate instead of ~800k tokens of agent
loop context per run. Zero $ on :free endpoints. Model override via env
REPLY_MODEL; key via OPENROUTER_API_KEY (process env or hermes .env).

Exit codes: 0 ok (silent stdout when nothing posted), 1 crash, 2 dead
session (prints error). Run under sys.executable from the cron wrapper.
"""
import json, os, re, sys, time, random, datetime, subprocess
import urllib.request, urllib.error

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
import reply_guy as rg  # state, scrapers, constants

KEY = os.environ.get("OPENROUTER_API_KEY") or ""
if not KEY:
    envp = os.path.join(os.environ.get("LOCALAPPDATA", r"C:\Users\Rajat\AppData\Local"), "hermes", ".env")
    try:
        for line in open(envp, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY="):
                KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    except Exception:
        pass

# Rajat's rule: cron engines run the most-available :free model first.
# Probe 2026-09-10: minimax-m3:free 404 DEAD, glm-5.2:free 404 DEAD,
# gemma-4-31b-it:free LIVE. gemma is primary now; the dead ones are gone so
# every draft stops burning a wasted 404 before the real call. Gemini native
# is still the mid-chain fallback (see llm()).
# z-ai/glm-5.2:free dropped 2026-09-10: OpenRouter serves it 404 ("unavailable
# for free"), so it only ever cost a wasted round trip per draft.
# google/gemma-4-31b-it:free was observed working the same day (log: TAKE via
# google/gemma-4-31b-it:free).
MODELS = json.loads(os.environ.get("REPLY_MODELS", '["google/gemma-4-31b-it:free"]'))
MAX_REPLIES = 8        # hard cap per run (same as the agent cron)
MAX_DRAFT_ATTEMPTS = 14
MAX_TAKE_CHARS = 250   # X's counter weights punctuation; 250 python chars is safe
# Rajat, 2026-09-12: "quote a phrase, judge the wording" openers ran at 12.8% of
# posted replies (34 of 266 in logs/replies.log). One per run is fine, a house
# style is not, so the direct path caps the shape instead of banning it.
QUOTE_OPENER_CAP = 1

BANNED = [
    "we move", "we're so back", "we are so back", "lfg", "hot ai summer",
    "accelerate anon", "the invoice lands", "the world won't",
    "let me break this down", "here's the thing", "let's dive in", "real talk",
    "at its core", "one thing that bit me", "want me to", "i hope this helps",
    "exciting times", "game-changer", "game changer", "testament to",
    "delve", "unpack", "harness", "leverage", "pivotal", "landscape",
    "underscores", "showcase", "vibrant", "fostering", "garner",
    "marks a shift", "sets the stage", "the future of ai", "as an ai",
    "as an ai assistant", "i'd be happy to", "sounds like a great",
]

# ---- keyless Google drafting via Antigravity (agy) ----------------------
# Why this exists (2026-09-10): Rajat asked to run the account on Gemini OAuth.
# The gemini-cli OAuth path is DEAD, verified three ways that day:
#   1. the official CLI itself fails with "IneligibleTierError: This client is
#      no longer supported for Gemini Code Assist for individuals ... migrate
#      to the Antigravity suite" - server-side, not a config problem;
#   2. refreshing the stored refresh_token SUCCEEDS, but the public Gemini API
#      rejects the token: HTTP 403 ACCESS_TOKEN_SCOPE_INSUFFICIENT;
#   3. the granted scopes are cloud-platform/userinfo.email/userinfo.profile/
#      openid - nothing that authorises generativelanguage.googleapis.com.
# Google names Antigravity as the replacement, and `agy` runs on the same
# Google account with NO API key. It is SLOW (~40s per call including process
# startup), so it sits LAST in the chain: a seatbelt for when the API key quota
# is gone and the free OpenRouter slugs are throttled.
AGY_BIN = os.path.join(
    os.environ.get("LOCALAPPDATA", r"C:\Users\Rajat\AppData\Local"),
    "agy", "bin", "agy.exe")
AGY_ENABLED = os.environ.get("REPLY_ENGINE_AGY", "1") == "1"
# 2026-09-11: raised from 100s. agy's own --print-timeout must always fire
# BEFORE this kill, so agy exits gracefully with partial output instead of
# being hard-killed by subprocess.run. print-timeout 180s < AGY_TIMEOUT 200s.
AGY_TIMEOUT = int(os.environ.get("REPLY_AGY_TIMEOUT", "200"))
# agy is a seatbelt, so it gets a hard per-run ceiling. On 2026-09-10 it was
# tried for EVERY candidate that fell through, and each cycle cost ~2.5 min
# (3 attempts, 25s sleeps, ~60s per call). The 14:00 run then hit the wrapper's
# 1800s kill, which is the worst outcome: every draft is thrown away and the run
# posts NOTHING while cron records exit 1.
AGY_MAX_PER_RUN = int(os.environ.get("REPLY_AGY_MAX_PER_RUN", "1"))
_agy_calls = 0

# ---- run budget ------------------------------------------------------------
# reply_guy_direct_cron.py kills the engine at 1800s (subprocess timeout), so
# the engine bounds ITSELF and stops cleanly with whatever it has, leaving room
# to actually post. Real shape of a run: scrape ~16 min (paced browser
# navigations), draft, then post. Checked in the scrape loops and before each
# draft attempt.
RUN_BUDGET_S = int(os.environ.get("REPLY_RUN_BUDGET", "1140"))  # 19 min < 30 min kill
_DEADLINE = None


def budget_spent():
    """True once the run must stop scraping/drafting and move on to posting."""
    return _DEADLINE is not None and time.time() >= _DEADLINE


def _antigravity(prompt, timeout=None):
    """Draft via `agy --print`. Returns (text, False) or (None, False).

    Never returns the KEY_DEAD sentinel: a missing or unhappy agy is a skipped
    fallback, not a credential fault worth aborting the run for.
    """
    global _agy_calls
    if not AGY_ENABLED or not os.path.exists(AGY_BIN):
        return None, False
    if _agy_calls >= AGY_MAX_PER_RUN:
        return None, False
    if budget_spent():
        rg.log("agy skipped: run budget spent")
        return None, False
    _agy_calls += 1
    for attempt in (1, 2):
        t0 = time.time()
        try:
            r = subprocess.run(
                # The flag MUST be attached (--print=...): a bare --print consumes
                # the next argument as its prompt and silently ignores the real one.
                # 180s (was 90s): baseline healthy call is ~42s for a SHORT
                # prompt, and a real reply brief is longer, so 90s sat on the
                # edge and timed out under scraper load.
                [AGY_BIN, "--print-timeout", "180s", f"--print={prompt}"],
                capture_output=True, text=True, timeout=timeout or AGY_TIMEOUT,
                cwd=os.path.dirname(BOT) or BOT)
        except Exception as e:
            rg.log(f"agy draft failed (attempt {attempt}): {type(e).__name__}: {e}")
            continue
        secs = round(time.time() - t0, 1)
        raw = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if not raw:
            # ROOT CAUSE (verified 2026-09-11): agy exits rc 0 with EMPTY stdout
            # when its own --print-timeout fires. The only explanation goes to
            # stderr: "[agy] print timeout after 180s with turn in progress;
            # returning partial output". This function used to read stdout only,
            # so a timeout was indistinguishable from a crash. Baseline healthy
            # call is ~42s, so contention or a longer brief can cross the limit
            # and produce exactly this. Never log an empty output without
            # rc+stderr.
            if "print timeout" in err.lower():
                rg.log(f"agy TIMED OUT at its own --print-timeout (attempt {attempt}): "
                       f"rc={r.returncode} secs={secs} stderr={err[:160]}")
            else:
                rg.log(f"agy returned empty output (attempt {attempt}): rc={r.returncode} "
                       f"secs={secs} stderr={err[:200] or '(none)'}")
            if attempt < 2:
                # agy is reliable when calls are spaced and returns empty when
                # they come back to back (observed 2026-09-10). A short pause
                # recovers it, but every second here comes out of the run
                # budget, so the per-run cap does the real limiting.
                time.sleep(8)
            continue
        # agy prints status chatter before the answer; the reply is the last block.
        blocks = [b.strip() for b in raw.split("\n\n") if b.strip()]
        return (blocks[-1] if blocks else raw), False
    return None, False


# ---- news-driven topics (Rajat 2026-09-10: "use more new news") ----------
# TRENDING_QUERIES are generic ("AI", "GPT"), so a reply only lands on today's
# actual news if a target account happens to tweet about it. This pulls the
# same feeds the news monitor posts from, keeps only notable headlines, and
# turns each into a Latest-sorted search. News candidates go FIRST in the pool
# and are capped, so a run mixes fresh-news replies with the standing targets
# instead of letting either one starve the other.
SCRIPTS_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", r"C:\Users\Rajat\AppData\Local"), "hermes", "scripts")
NEWS_QUERIES_ENABLED = os.environ.get("REPLY_NEWS_QUERIES", "1") == "1"
MAX_NEWS_QUERIES = 3
MAX_NEWS_CANDIDATES = 4

_PHRASE_STOP = {
    "the", "a", "an", "and", "or", "for", "with", "from", "into", "your", "this",
    "that", "new", "now", "its", "how", "why", "what", "when", "who", "are",
    "was", "has", "have", "is", "to", "of", "in", "on", "at", "we", "you", "our",
}


def _search_phrase(title, strong_terms):
    """Distinctive X search phrase for a headline, or "" if there is none.

    Prefers a model term plus the token after it ("gpt-6 astra"), else the
    first two capitalised non-stopword words.
    """
    low = title.lower()
    for term in strong_terms:
        i = low.find(term)
        if i == -1:
            continue
        toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-]*", title[i:])
        if not toks:
            continue
        if len(toks) > 1 and toks[1].lower() not in _PHRASE_STOP:
            cand = f"{toks[0]} {toks[1]}"
            if len(cand) >= 4:
                return cand
        if len(toks[0]) >= 4:
            return toks[0]
    # Fallback: the longest run of ADJACENT capitalised words, so a headline
    # like "OpenAI brings back Paul Christiano to its board" searches
    # "Paul Christiano" instead of the nonsense "OpenAI Paul". A sole
    # capitalised word is too broad to be a news query, so it yields "".
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-]*", title)
    runs, cur = [], []
    for w in words:
        if re.match(r"^[A-Z]", w) and w.lower() not in _PHRASE_STOP:
            cur.append(w)
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
    if len(cur) >= 2:
        runs.append(cur)
    return " ".join(max(runs, key=len)[:2]) if runs else ""


def _news_quality_ok(text):
    """News-search results skew multilingual and promo-heavy. An English reply
    under a CJK thread, or a reply to a link drop, is off-voice."""
    if not text:
        return False
    if "http://" in text or "https://" in text or "t.co/" in text:
        return False
    cjk = sum(1 for ch in text
              if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff")
    if cjk and cjk / max(len(text), 1) > 0.12:
        return False
    return True


def news_queries(max_q=MAX_NEWS_QUERIES):
    """Latest-sorted search phrases from today's notable AI headlines.

    Reuses news_monitor's feeds and quality gate so replies and news posts
    agree on what counts as news. Any failure returns [] - news is additive
    and must never break the standing scrape.
    """
    try:
        if SCRIPTS_DIR not in sys.path:
            sys.path.append(SCRIPTS_DIR)
        import news_monitor as nm
    except Exception as e:
        log(f"news queries: news_monitor import failed: {type(e).__name__}: {e}")
        return []
    phrases = []
    for name, url in nm.FEEDS.items():
        try:
            items = nm.parse_items(nm.fetch(url))
        except Exception as e:
            log(f"news queries: feed {name} failed: {type(e).__name__}: {e}")
            continue
        for title, _link, _desc in items[:12]:
            try:
                if not nm.is_notable(title):
                    continue
            except Exception:
                continue
            ph = _search_phrase(title, nm.STRONG_MODEL_TERMS)
            if ph and ph.lower() not in {p.lower() for p in phrases}:
                phrases.append(ph)
            if len(phrases) >= max_q:
                return phrases
    return phrases[:max_q]


SYSTEM = """You write replies for @first_sauce_lab, an X account run by one real AI engineer who works with models daily. Not a brand, not a persona. A smart, slightly tired engineer typing on their phone between meetings.

VOICE RULES (absolute):
- Opinions owned and plain. Normal punctuation, contractions. Never lowercase-everything as a uniform.
- No template shapes, no catchphrases, no signature closers, no bumper-sticker morals. If the point lands by line two, stop. If a line could be pasted under a different tweet, cut it.
- Dry deadpan humor encoding a real observation. Playful, never mean.
- No personified systems ("the quota shrugs", "the meter doesn't do coupons"). Systems behave; you have an experience.
- No symmetrical antithesis ("one of these is X, the other is Y"). State which side you are on.
- Criticize substance, never packaging. Engage the numbers, mechanism, or timeline - never the tweet's framing.
- Concrete nouns over philosophy: a tier, a tool, a duration, an outcome. No vibe without a specific.
- Human tells when natural: something you actually ran into, mild self-deprecation, honest ambivalence.
- Zero em dashes or en dashes. Never use: delve, unpack, harness, leverage, game-changer, testament, pivotal, "here's the thing", "let me break this down", "the future of X", "experts say", "the industry believes", "sources indicate".
- No hashtags. No emoji unless one carries real feeling.
- SHORT BEATS FULL: one line is a complete reply. Match the tweet's register - casual tweet gets a casual short line, a big claim gets substance. Replying to a meme with an essay is the cardinal sin.

FACT GATE (absolute): this is commentary, never reporting. Never state any number, model name, or claim that is not present in the tweet itself. Never invent specifics. If you want to reference a fact, it must be in the tweet text.

QUALITY BAR: if nothing about this specific tweet earns a reply worth a smart person's scroll-stop, output exactly: SKIP"""


KEY_DEAD = "__KEY_DEAD__"  # sentinel: OpenRouter rejected the key
KEY_GEMINI = os.environ.get("GOOGLE_API_KEY") or ""
if not KEY_GEMINI:
    envp = os.path.join(os.environ.get("LOCALAPPDATA", r"C:\Users\Rajat\AppData\Local"), "hermes", ".env")
    try:
        for line in open(envp, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line.startswith("GOOGLE_API_KEY="):
                KEY_GEMINI = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    except Exception:
        pass

GEMINI_MODEL = os.environ.get("REPLY_GEMINI_MODEL", "gemini-2.5-flash")  # free tier; set "" to disable

# reasoning leaked into content = unusable; skip to the next provider
COT_MARKERS = ["here's a thinking process", "thinking process", "let me think",
               "i'll think", "we need to produce", "okay, let's", "ok, let's",
               "reasoning:", "let's work through", "step 1:", "**reasoning**"]


def _openrouter(model, prompt):
    """One OpenRouter call. Returns (text, is_dead_key) with 429 backoff."""
    body = {"model": model,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": prompt}],
            "temperature": 0.95, "max_tokens": 320}
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    d = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return KEY_DEAD, False
            if e.code == 429 and attempt < 2:
                time.sleep(6 * (attempt + 1))
                continue
            time.sleep(3)
            break
        except Exception:
            time.sleep(3)
            break
    if d is None:
        return None, False
    txt = (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return txt.strip(), False


def _gemini(prompt):
    """Google native endpoint, thinking off (free tier). Returns text or None."""
    if not KEY_GEMINI or not GEMINI_MODEL:
        return None
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "generationConfig": {"temperature": 0.95, "maxOutputTokens": 320,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={KEY_GEMINI}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429:  # quota spike: one wait, then give up this candidate
            time.sleep(15)
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    d = json.load(r)
            except Exception:
                return None
        else:
            return None
    except Exception:
        return None
    try:
        parts = d["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except Exception:
        return None


def llm(prompt):
    """Gemini 2.5 flash free is PRIMARY as of 2026-09-10.

    Probe that day: minimax-m3:free 404, glm-5.2:free 404 ("unavailable for
    free"), gemma-4-31b-it:free 429 upstream, ling-3.0-flash:free returns
    empty content (reasoning eats the token budget). The old order tried a
    dead slot first and paid up to 36s of backoff per draft before Gemini
    produced the text. Both paths are still free-first; the OpenRouter :free
    slugs remain the fallback chain.
    Returns (text, model_name), (KEY_DEAD, None) or (None, None)."""
    txt = _gemini(prompt)
    if txt and not any(mark in txt[:140].lower() for mark in COT_MARKERS):
        return txt, f"gemini:{GEMINI_MODEL}"
    for model in MODELS:
        txt, dead = _openrouter(model, prompt)
        if dead:
            return KEY_DEAD, None
        if txt and not any(mark in txt[:140].lower() for mark in COT_MARKERS):
            return txt, model
    # Last resort: Google account, no API key (slow, see docstring).
    txt, _ = _antigravity(prompt)
    if txt and not any(mark in txt[:140].lower() for mark in COT_MARKERS):
        return txt, "antigravity:agy"
    return None, None


def gate(txt):
    """Filter and sanitize candidate draft text through safety and style gates.

    Returns normalized text, or None if rejected by length, banned words,
    or dead-move pattern checks.
    """
    t = txt.strip()
    if not t:
        return None
    if t.upper().startswith("SKIP"):
        return None
    # Em/en dashes: normalize, do NOT discard. Found 2026-09-10 via
    # scripts/test_agy_path.py: agy writes em dashes by habit, so rejecting on
    # sight threw away every otherwise-good take and the keyless fallback could
    # never post. Normalising keeps the take and still ships clean text.
    t = rg.normalize_dashes(t)
    if len(t) > MAX_TAKE_CHARS or len(t) < 3:
        return None
    if "\u2014" in t or "\u2013" in t:
        return None
    low = t.lower()
    if any(b in low for b in BANNED):
        return None
    # Dead move: wording critique or the "heavy lifting" idiom family. Hard
    # refuse, no quota. See rg.DEAD_MOVE_RE for why.
    if rg.DEAD_MOVE_RE.search(t):
        return None
    # quote-wrapped output from the model (straight or curly pairs)
    if len(t) >= 2 and (
        (t[0] == t[-1] and t[0] in "\"'")
        or (t[0] == "\u201c" and t[-1] == "\u201d")
        or (t[0] == "\u2018" and t[-1] == "\u2019")
    ):
        t = t[1:-1].strip()
        if not t:
            return None
    return t


def draft_prompt(cand):
    """Build the LLM prompt for drafting a reply to candidate tweet cand."""
    if cand.get("handle"):
        author = cand["handle"]
    else:
        author = cand.get("author", "?")
    return (f"TARGET TWEET by @{author}:\n\"{cand['text'][:600]}\"\n\n"
            f"Write ONE reply as @first_sauce_lab. Rules: engage THIS tweet specifically. "
            f"Max {MAX_TAKE_CHARS} characters. Output only the reply text, nothing else. "
            f"If nothing about it earns a reply, output exactly: SKIP")


def scrape_pool(page):
    """One browser pass: TARGET profiles + trending searches. State-update
    semantics identical to reply_guy.py cmd_scrape + cmd_trending."""
    st = rg.load_state()
    seen = st.get("seen", {})
    replied = set(st.get("replied", []))
    # two-strike rule: a tweet that failed posting twice is dead (deleted,
    # replies restricted, blocked) - stop drafting and re-failing it forever
    black = {tid for tid, n in (st.get("failed") or {}).items() if n >= 2}
    pool = []  # target items first: {handle, id, text, time}

    def absorb(handle, tweets):
        """Filter fresh, unseen tweets into the pool; update seen. False = nothing."""
        if not tweets:
            return False
        seen.setdefault(handle, [])
        fresh = [t for t in tweets
                 if t["id"] not in replied and t["id"] not in black
                 and t["id"] not in seen[handle]
                 and (not t["time"] or rg._is_fresh(t["time"]))]
        fresh.sort(key=lambda t: t["time"], reverse=True)
        pool.extend({"handle": handle, "source": "target", **t} for t in fresh[:rg.MAX_PER_TARGET])
        seen[handle] = (seen[handle] + [t["id"] for t in tweets])[-20:]
        return True

    emptied = []
    for handle in rg.TARGETS:
        if budget_spent():
            rg.log(f"scrape budget spent - stopping target pass "
                   f"({len(pool)} in pool)")
            break
        try:
            tweets = rg.scrape_profile(page, handle)
        except Exception as e:
            rg.log(f"scrape {handle} failed, skipping: {e}")
            continue
        if not absorb(handle, tweets):
            emptied.append(handle)
        rg.human_delay(2, 4)

    # X transiently serves empty profile pages mid-run: a ~90s window that has
    # hit the same block of profiles every run since 09-04, then recovers on
    # its own (probe 2026-09-07: pages render fine before and after the window).
    # One retry pass after a pause gets the accounts caught in it.
    if emptied and not budget_spent():
        rg.log(f"RETRY pass for {len(emptied)} empty profiles: "
               + ", ".join("@" + h for h in emptied))
        time.sleep(45)
        for handle in emptied:
            if budget_spent():
                rg.log("scrape budget spent - abandoning retry pass")
                break
            try:
                tweets = rg.scrape_profile(page, handle)
            except Exception as e:
                rg.log(f"retry scrape {handle} failed: {e}")
                continue
            absorb(handle, tweets)
            rg.human_delay(2, 4)

    # trending pool: mark exactly like cmd_trending (only the surfaced set)
    seen_trending = set(seen.get("_trending", []))
    found = {}
    for q in rg.TRENDING_QUERIES:
        if budget_spent():
            rg.log("scrape budget spent - skipping remaining trending queries")
            break
        try:
            res = rg.scrape_search(page, q)
        except Exception as e:
            rg.log(f"trending {q!r} failed, skipping: {e}")
            continue
        for t in res:
            tid = t["id"]
            if tid in replied or tid in black or tid in seen_trending or tid in found:
                continue
            if t["author"].lower() == "first_sauce_lab":
                continue
            if not t["time"] or rg._is_fresh(t["time"]):
                found[tid] = t
        rg.human_delay(2, 4)
    top = list(found.values())[:rg.MAX_TRENDING]
    for t in top:
        t["source"] = "trending"
    seen["_trending"] = (list(seen_trending) + [t["id"] for t in top])[-40:]
    pool.extend(top)  # trending after targets

    # news pool: what actually broke today, Latest-sorted. Placed FIRST (and
    # capped) so a run spends part of its replies on current news instead of
    # only on whatever the standing target list happens to have posted.
    if NEWS_QUERIES_ENABLED:
        qs = news_queries()
        if qs:
            rg.log(f"news queries: {qs}")
        seen_news = set(seen.get("_news", []))
        nfound = {}
        for q in qs:
            if budget_spent():
                rg.log("scrape budget spent - skipping remaining news queries")
                break
            try:
                res = rg.scrape_search(page, q, live=True)
            except Exception as e:
                rg.log(f"news search {q!r} failed, skipping: {e}")
                continue
            for t in res:
                tid = t["id"]
                if tid in replied or tid in black or tid in seen_news or tid in nfound:
                    continue
                if t["author"].lower() == "first_sauce_lab":
                    continue
                if not t["time"] or rg._is_fresh(t["time"]):
                    if _news_quality_ok(t["text"]):
                        t["source"] = "news"
                        nfound[tid] = t
            rg.human_delay(2, 4)
        ntop = list(nfound.values())[:MAX_NEWS_CANDIDATES]
        seen["_news"] = (list(seen_news) + [t["id"] for t in ntop])[-60:]
        pool = ntop + pool

    st["seen"] = seen
    rg.save_state(st)
    return pool


def post_batch(items):
    """Post accepted takes in one browser context. Returns
    (posted, failed_ids, err)."""
    import browser_thread
    browser_thread.USER = "first_sauce_lab"
    posted, failed, dead = [], [], False
    with __import__("playwright").sync_api.sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            rg.PROFILE, headless=True, viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not rg.check_session(page):
            ctx.close()
            return posted, failed, "NOT_LOGGED_IN"
        for tid, take in items:
            try:
                new_id = browser_thread.post_one(page, take, reply_to=tid)
                if new_id:
                    posted.append((tid, new_id))
                    rg.log(f"REPLIED to {tid} -> {new_id}")
                else:
                    failed.append(tid)
                    rg.log(f"REPLY FAILED to {tid}")
            except Exception as e:
                failed.append(tid)
                rg.log(f"REPLY FAILED to {tid}: {type(e).__name__}: {e}")
            time.sleep(random.uniform(3, 6))
        ctx.close()
    return posted, failed, None


def main():
    """Execute one complete reply run: scrape, draft, gate, and post."""
    if not KEY and not KEY_GEMINI:
        print("reply-guy direct: no API keys - add GOOGLE_API_KEY or OPENROUTER_API_KEY to hermes .env")
        return 1
    # Another engine may be using the shared X profile; skip quietly rather
    # than trample it and mistake the logged-out page for an auth failure.
    if not rg.acquire_browser_lock():
        return 0
    import atexit
    atexit.register(rg.release_browser_lock)
    global _DEADLINE
    _DEADLINE = time.time() + RUN_BUDGET_S
    st = rg.load_state()
    st.setdefault("replied", [])
    st.setdefault("failed", {})

    # ---- 1. scrape ----
    pool = []
    with __import__("playwright").sync_api.sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            rg.PROFILE, headless=True, viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not rg.check_session(page):
            ctx.close()
            print("reply-guy direct: NOT LOGGED IN - run browser_login.py")
            return 2
        pool = scrape_pool(page)
        ctx.close()
    # scrape_pool loads and saves its OWN state copy (it must, to track what it
    # has seen mid-pass). This function loaded its snapshot before that, so
    # without re-reading, the save at the end of main() writes the stale dict
    # over the top and discards every scrape-phase seen[] update - including
    # seen["_news"] and seen["_trending"]. Replies were never affected (they
    # live in st["replied"], appended after this point), which is why the
    # clobber went unnoticed; it only cost re-drafting tweets the seen lists
    # were meant to skip.
    st = rg.load_state()
    if not pool:
        return 0  # nothing fresh - silent

    # ---- 2. draft ----
    takes, attempts, api_fails = [], 0, 0
    for cand in pool:
        if len(takes) >= MAX_REPLIES or attempts >= MAX_DRAFT_ATTEMPTS:
            break
        if budget_spent():
            rg.log(f"draft budget spent - posting the {len(takes)} take(s) "
                   f"drafted so far")
            break
        attempts += 1
        txt, mdl = llm(draft_prompt(cand))
        if txt == KEY_DEAD:
            print("reply-guy direct: OPENROUTER key rejected (401) - check hermes .env OPENROUTER_API_KEY")
            return 1
        if txt is None:
            api_fails += 1
            if api_fails >= 3:
                break
            continue
        take = gate(txt)
        if not take:
            if rg.DEAD_MOVE_RE.search(txt):
                rg.log(f"REFUSED dead move for {cand['id']}: {txt[:80]}")
            continue
        # one quote-opener per run, then the drafter has to find another shape
        if (rg.PACKAGING_RE.search(take)
                and sum(1 for _, p in takes if rg.PACKAGING_RE.search(p))
                >= QUOTE_OPENER_CAP):
            rg.log(f"DROPPED (quote-opener quota spent): {cand['id']}")
            continue
        rg.log(f"TAKE via {mdl}: {cand['id']} ({take[:80]})")
        # no two takes sharing an opening shape this run
        if any(take[:40].lower() == prev[:40].lower() for _, prev in takes):
            continue
        takes.append((cand["id"], take))

    if not takes:
        if attempts > 0:
            rg.log(f"reply-guy direct: 0 takes accepted out of {attempts} draft attempts (all gated)")
        return 0  # nothing cleared the bar - silent

    # ---- 3. post ----
    posted, failed, err = post_batch(takes)
    if err == "NOT_LOGGED_IN":
        print("reply-guy direct: NOT LOGGED IN - run browser_login.py")
        return 2

    for tid, _new in posted:
        if tid not in st["replied"]:
            st["replied"].append(tid)
        st["failed"].pop(tid, None)  # a success clears any prior strike
    for tid in failed:
        st["failed"][tid] = st["failed"].get(tid, 0) + 1
        if st["failed"][tid] == 2:
            rg.log(f"BLACKLISTED {tid} (2 posting failures - dead target)")
    rg.save_state(st)

    src = {c["id"]: c.get("source", "trending") for c in pool}
    n_targets = sum(1 for tid, _ in posted if src.get(tid) == "target")
    n_news = sum(1 for tid, _ in posted if src.get(tid) == "news")
    n_trend = len(posted) - n_targets - n_news
    line = (f"reply-guy direct: replied {len(posted)} "
            f"(targets {n_targets} / news {n_news} / trending {n_trend})")
    if len(posted) < len(takes):
        line += f", {len(takes) - len(posted)} post-failures"
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
