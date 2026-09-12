# Twitter Browser-Automation Playbook
### Everything an agent needs to run @first_sauce_lab's X machinery on this machine — no credentials, only technical knowledge and code.

**Written for Mochi by Legolas — 2026-09-07.** Every code block below is VERBATIM from the working scripts on this machine (paths noted at the top of each block) or is a documented patch applied on 2026-09-07. Where a file is too long to quote fully, the block ends with `# ... (rest in <file>)` — the file exists on disk and is authoritative. Nothing here is invented; if a behavior surprises you, read the source file before changing it.

---

## 0. The one-paragraph model

One Windows 10 machine (`C:\Users\Rajat`), one X account worked by browser automation: **@first_sauce_lab** (the job names sometimes say "LegolasBot" — legacy label, same account/queue/pipeline). The account session lives in a **Playwright persistent Chromium profile** at `C:\Users\Rajat\twitter-bot\browser-profile`. Scripts launch that profile **headless**, drive the real X web UI by `data-testid` selectors, type with human delays, and verify posts via toasts/composer state. Posting/replies/scraping/follows are separate scripts; **Hermes cron jobs** (see §8) run them on schedules through thin wrapper scripts. LLM work (drafting replies, takes, summaries) is done by **direct HTTP calls to free model endpoints** — the browser is never asked to think, only to act.

Two hard rules that shape everything:

1. **No credentials ever in code or logs.** Sessions live in the browser profile. When a session dies, a *headed* login script opens a window and **the human types the credentials** — never ask for, read, or handle a password/2FA.
2. **Rajat's verification gate:** never post anything unverified. Every number, model name, price, and date in posted content must be verified against a live source first. Unverified = not posted.

---

## 1. Machine layout and runtimes

| Path | Role |
|---|---|
| `C:\Users\Rajat\twitter-bot\` | Bot home. All X scripts, the browser profile, queues, logs, state |
| `C:\Users\Rajat\twitter-bot\browser-profile\` | The persistent Chromium profile (the X session). NEVER delete, NEVER open two at once |
| `C:\Users\Rajat\twitter-bot\drafts\` | Draft queue (`*.txt`, one tweet per file). Post slots drain it oldest-first |
| `C:\Users\Rajat\twitter-bot\drafts\overlong\` | Quarantine for >250-char drafts (the slot script moves them here) |
| `C:\Users\Rajat\twitter-bot\logs\` | `replies.log`, `posts.log`, `threads.log`, `follows.log`, `reply_guy_state.json`, `news_monitor_state.json`, `model_monitor_state.json` |
| `C:\Users\Rajat\twitter-bot\follow_pool.json` | Follow-spread pool: `{"handles": [...], "done": {handle: date}}` |
| `C:\Users\Rajat\AppData\Local\hermes\scripts\` | Hermes cron wrapper scripts (thin; they exec the engines) |
| `C:\Users\Rajat\AppData\Local\hermes\.env` | Secret keys BY NAME ONLY for engines (`OPENROUTER_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`). Never print values |

**Python runtime — the trap that has burned agents before:**

- Hermes' venv python is 3.11 at `C:\Users\Rajat\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`. **Playwright 1.62+ and browsers are installed in this venv (since 2026-08-27).**
- Bare `python3` on this machine is 3.12 (WindowsApps) and **cannot import the venv's cp311 packages** (`greenlet._greenlet` missing). When the cron environment sets `PYTHONPATH` to the venv's site-packages, bare `python3` breaks worse.
- **Therefore: every script that launches playwright must run under `sys.executable` of the venv** (cron wrappers do `subprocess.run([sys.executable, ENGINE], ...)`). When running manually, use the full venv python path:
  ```
  "C:/Users/Rajat/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe" C:/Users/Rajat/twitter-bot/reply_guy_direct.py
  ```
- `import queue` shadow trap: `twitter-bot/queue.py` (a draft-queue helper) shadows the stdlib `queue` module for playwright if the bot dir is on `sys.path`. `follow_accounts.py` documents: **append, never insert**, the bot dir to `sys.path`. Run engine scripts from OUTSIDE the bot dir when possible.

---

## 2. The session model (read this before anything else)

```python
# VERBATIM core pattern — every browser script on this machine (browser_post.py, browser_thread.py,
# reply_guy.py, reply_guy_direct.py, follow_accounts.py) launches exactly this way:

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE,               # e.g. r"C:\Users\Rajat\twitter-bot\browser-profile"
        headless=True,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    # ... work ...
    ctx.close()
```

Rules:

- **Persistent context** = cookies/localStorage survive between runs. One X login lasts weeks — until X revokes it (see §9).
- **SingletonLock:** Chromium persistent profiles refuse two concurrent contexts. If a script errors with a lock/profile-in-use message, another browser job is running (check `wmic process where "name='python.exe'" get commandline` for it). Cron schedules (§8) are spaced to avoid collisions; never start a manual browser run that overlaps a scheduled one.
- `--disable-blink-features=AutomationControlled` hides the automation tell. Keep it.

### Session check — the ONLY trustworthy test

```python
# VERBATIM from reply_guy.py / browser_post.py / browser_thread.py (identical in all three)

def check_session(page):
    page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60_000)
    human_delay(2, 4)
    if "login" in page.url:
        return False
    # logged-in home timeline has the "Post" nav button — a logged-out x.com
    # splash contains no "login" in the URL, so the URL check ALONE IS NOT ENOUGH.
    try:
        page.wait_for_selector('[data-testid="SideNav_NewTweet_Button"]', timeout=15_000)
        return True
    except Exception:
        return False
```

CLI form: `python browser_post.py --check` → prints `SESSION OK` or `NOT LOGGED IN`, exit 0/2.

### Login recovery — the HUMAN does credentials, always

```python
# VERBATIM from browser_login.py (entire file, 71 lines)

"""Log into X once so the bot can post from a persistent browser session.

Opens a real (headed) Chromium window with a persistent profile. YOU log in
manually (type your credentials yourself — the script never sees them). Once
you're logged in and see your home timeline, close the window. The session is
saved in the profile and reused by browser_post.py.

Usage: python3 browser_login.py
"""
import os, sys, time

from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")

def main():
    os.makedirs(PROFILE, exist_ok=True)
    print("Opening X login in a real browser window...")
    print("LOG IN YOURSELF. When you see your home timeline, close the window.")
    print("(You have up to 3 minutes.)")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://x.com/login", wait_until="domcontentloaded")
        # Wait up to 3 minutes for the user to finish logging in and close.
        try:
            page.wait_for_url("https://x.com/home", timeout=180_000)
        except Exception:
            pass
        ctx.close()
    # verify session: open a quick headless check
    ok = check_session()
    if ok:
        print("LOGIN OK — session saved. Bot can post.")
    else:
        print("WARNING: could not verify session. Try again and make sure you reach the home timeline.")

def check_session():
    """Reliable check: logged-in-only DOM marker (SideNav_NewTweet_Button).

    The old URL-only check (`"login" not in url`) is NOT trustworthy — a
    logged-out x.com/ splash contains no "login" and falsely reports OK.
    """
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                PROFILE, headless=True,
                viewport={"width": 1280, "height": 900})
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=45_000)
            time.sleep(3)
            try:
                page.wait_for_selector('[data-testid="SideNav_NewTweet_Button"]', timeout=15_000)
                ok = True
            except Exception:
                ok = False
            ctx.close()
            return ok
    except Exception as e:
        print(f"  session check error: {e}")
        return False

if __name__ == "__main__":
    main()
```

**If you ever need a session back: tell Rajat to run `python browser_login.py`, watch the headed window, close it at the home timeline. You never see or type the password. Never store X cookies anywhere else.**

---

## 3. Selector map — the X DOM vocabulary

All selectors are X's `data-testid` attributes (stable since this bot started, 2026-08):

| Element | Selector |
|---|---|
| Logged-in nav proof | `[data-testid="SideNav_NewTweet_Button"]` |
| Composer textarea (dialog or home) | `[data-testid="tweetTextarea_0"]` |
| Composer inside a dialog | `[role="dialog"] [data-testid="tweetTextarea_0"]` |
| Post button (dialog) | `[data-testid="tweetButton"]` |
| Post button (home inline) | `[data-testid="tweetButtonInline"]` |
| Reply button on a tweet permalink | `[data-testid="reply"]` |
| Success toast | `[data-testid="toast"]` (contains `a[href*="/status/"]` = your new tweet link) |
| Tweet article | `article[data-testid="tweet"]` |
| Tweet text inside article | `[data-testid="tweetText"]` |
| Follow button scoped to primary column | `[data-testid="primaryColumn"] button[data-testid$="-follow"]` |
| Unfollow (proof of follow) | `[data-testid="primaryColumn"] button[data-testid$="-unfollow"]` |
| Confirm sheet | `[data-testid="confirmationSheetConfirm"]` |
| File input for media | `input[data-testid="fileInput"]` |
| Media preview | `[data-testid="attachments"] img` |

Timing vocabulary: `human_delay(2, 4)` (random uniform seconds) between every significant action; typing uses `delay=random.randint(25, 60)` ms per character. Selector waits: 15–20 s. Page gotos: `wait_until="domcontentloaded", timeout=60_000`.

---

## 4. Posting — plain tweets (the queue/post-slot engine)

```python
# VERBATIM from browser_post.py — post_text() core (2026-08-24 battle-tested)

def post_text(page, text, image_path=None):
    # X's /compose/post page is unstable (2026-08-24: hidden duplicate dialog +
    # mask overlay + unsent-draft restore sheets). The home inline composer is
    # reliable; Ctrl+Enter submits without needing a button click.
    page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60_000)
    human_delay(2, 4)
    if "login" in page.url:
        return 2
    try:
        page.wait_for_selector('[data-testid="tweetTextarea_0"]', timeout=20_000)
    except Exception:
        log("ERROR: composer textarea not found (page change or bot check)")
        return 3
    box = page.locator('[data-testid="tweetTextarea_0"]').last
    if image_path:
        try:
            # hidden file input on the compose page
            fi = page.locator('input[data-testid="fileInput"]').first
            fi.set_input_files(image_path, timeout=20_000)
            log(f"image attached: {image_path}")
        except Exception as e:
            log(f"ERROR: could not attach image: {e}")
            return 3
        human_delay(3, 5)  # upload + preview render
        # sanity: media preview present (attachments container with a rendered image)
        try:
            page.wait_for_selector('[data-testid="attachments"] img', timeout=20_000)
            log("media preview confirmed")
        except Exception:
            log("WARN: media preview not confirmed, posting anyway")
    box.click()
    human_delay(0.4, 1.0)
    box.type(text, delay=random.randint(25, 60))
    human_delay(0.8, 1.8)
    # submit via the inline Post button (tweetButtonInline on home); Ctrl+Enter fallback
    btn = page.locator('[data-testid="tweetButtonInline"]').last
    try:
        if btn.count():
            btn.click(timeout=10_000)
        else:
            page.keyboard.press('Control+Enter')
    except Exception:
        page.keyboard.press('Control+Enter')
    human_delay(2, 4)
    # verify: composer should be gone / toast appeared
    try:
        page.wait_for_selector('[data-testid="toast"]', timeout=10_000)
        log("POSTED (toast confirmed)")
        return 0
    except Exception:
        # home inline composer stays in the DOM after posting — check it cleared
        try:
            page.wait_for_function(
                '() => { const el = document.querySelector(\'[data-testid="tweetTextarea_0"]\'); return !el || el.innerText.trim() === ""; }',
                timeout=8_000)
            log("POSTED (composer cleared)")
            return 0
        except Exception:
            log("UNSURE: no toast, composer still has text — manual check needed")
            return 4
```

Notes:
- Media attach flow: `input[data-testid="fileInput"]` → `set_input_files` → wait for `[data-testid="attachments"] img`.
- **Verification of a successful post is mandatory** (toast OR composer-cleared). "UNSURE" = return code 4 = human check needed, never assume.
- Exit codes (whole script): 0 posted, 2 not logged in, 3 blocked/captcha, 4 other error. Empty tweet / >280 chars rejected before launch.

---

## 5. Replying and threads

### The reply flow (used by reply-guy and cmd_reply)

```python
# VERBATIM from browser_thread.py — post_one() core (proven reply flow)

def post_one(page, text, reply_to=None):
    """Post a single tweet; reply_to = tweet id to reply to (thread)."""
    if reply_to:
        page.goto(f"https://x.com/{USER}/status/{reply_to}", wait_until="domcontentloaded", timeout=60_000)
        human_delay(2, 4)
        try:
            page.wait_for_selector('[data-testid="reply"]', timeout=15_000)
            page.locator('[data-testid="reply"]').first.click()
        except Exception:
            log("ERROR: reply button not found")
            return None
        human_delay(1, 2)
    else:
        page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=60_000)
        human_delay(2, 4)
    if "login" in page.url:
        return None
    try:
        page.wait_for_selector('[data-testid="tweetTextarea_0"]', timeout=20_000)
    except Exception:
        log("ERROR: composer textarea not found")
        return None
    box = composer_box(page)
    box.click()
    human_delay(0.4, 1.0)
    box.type(text, delay=random.randint(25, 60))
    human_delay(0.8, 1.8)
    btn = post_button(page)
    if not btn.count():
        log("ERROR: Post button not found")
        return None
    btn.click()
    human_delay(2, 4)
    # grab the new tweet id from the toast's View link if present (reliable),
    # else fall back to the profile page's first status link.
    tid = None
    try:
        page.wait_for_selector('[data-testid="toast"]', timeout=10_000)
        view = page.locator('[data-testid="toast"] a[href*="/status/"]').first
        if view.count():
            href = view.get_attribute("href")
            if href and "/status/" in href:
                tid = href.split("/status/")[1].split("?")[0]
    except Exception:
        pass
    if tid is None:
        try:
            page.wait_for_selector('[data-testid="tweetTextarea_0"]', state="detached", timeout=8_000)
        except Exception:
            log("UNSURE: no toast, no close — aborting")
            return None
    if tid is None:
        human_delay(2, 3)
        page.goto(f"https://x.com/{USER}", wait_until="domcontentloaded", timeout=60_000)
        human_delay(2, 3)
        try:
            page.wait_for_selector('a[href*="/status/"]', timeout=15_000)
            href = page.locator('a[href*="/status/"]').first.get_attribute("href")
            tid = href.split("/status/")[1].split("?")[0]
        except Exception:
            log("WARNING: could not read tweet id from profile")
            return "unknown"
    return tid
```

Helpers it uses:

```python
# VERBATIM from browser_thread.py

def composer_box(page):
    """Return the composer textarea locator (dialog-first)."""
    dialog = page.locator('[role="dialog"]')
    if dialog.count():
        return dialog.locator('[data-testid="tweetTextarea_0"]').first
    return page.locator('[data-testid="tweetTextarea_0"]').last

def post_button(page):
    dialog = page.locator('[role="dialog"]')
    if dialog.count():
        return dialog.locator('[data-testid="tweetButton"]')
    return page.locator('[data-testid="tweetButton"]').last
```

### Threads

`browser_thread.py --file thread.txt` — plain text parts separated by a line of exactly `<<<BREAK>>>`. It posts part 1, then each next part via `post_one(page, part, reply_to=last_id)` (reply-to-chain), 3–5 s between parts, and returns the new tweet id each time. Parts >280 chars are rejected up front. Logs to `logs/threads.log`.

---

## 6. Scraping — reading the ecosystem

### Profile scrape (the reply-guy candidate source)

```python
# VERBATIM from reply_guy.py — scrape_profile() + freshness

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

def _is_fresh(dt):
    try:
        t = datetime.datetime.fromisoformat(dt.replace("Z", "+00:00"))
        age = datetime.datetime.now(datetime.timezone.utc) - t
        return age.total_seconds() < FRESH_HOURS * 3600
    except Exception:
        return True
```

Key extraction details: only the account's OWN tweets count (`/{handle}/status/` in the href — retweets/promos point at other authors); time comes from the `<time datetime>` ISO attr; freshness window = `FRESH_HOURS = 48`.

### Search scrape (trending pool)

```python
# VERBATIM from reply_guy.py — scrape_search() (Top-sorted search = high engagement)

def scrape_search(page, query):
    """Return list of {author, id, text, time} from Top-sorted search results."""
    out = []
    url = f"https://x.com/search?q={quote(query)}&f=top"
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
```

### State discipline (the anti-spam heart)

- `reply_guy_state.json` = `{"seen": {handle: [recent tweet ids]}, "replied": [all replied tweet ids], "failed": {tid: strike_count}}`.
- `replied` grows forever — never re-reply. `seen[handle]` keeps the last 20 ids per handle. `_trending` keeps the last 40 surfaced search ids.
- **A tweet is only consumed (marked seen) when acted on or deliberately skipped — never silently dropped.** The trending code marks candidates seen so they aren't re-suggested every run, but a post-limited leftover must stay unseen for the next run.
- Two-strike rule (2026-09-07): a tweet that fails posting twice goes into `failed` with count 2 and is filtered from every future pool (`black = {tid for tid, n in (st.get("failed") or {}).items() if n >= 2}`). A successful post clears the strike.

---

## 7. The reply-guy engine (the flagship) — full anatomy

**`C:\Users\Rajat\twitter-bot\reply_guy_direct.py`** — direct-API engine, no agent loop. Runs every day at 07:00, 10:00, 12:00, 14:00, 16:00, 20:00 (Hermes cron `twitter-reply-guy`, no_agent, script `reply_guy_direct_cron.py`, deliver origin). Flow:

1. **Scrape** (one headless browser context): visit all 57 `TARGETS` profiles (§6 profile scrape, 2–4 s human delays), then 9 trending searches (`AI GPT Claude OpenAI Anthropic AGI LLM Gemini Grok`, Top-sorted), 48 h freshness. Pool = target items first, trending after. State `seen` updated.
2. **Draft** (no browser): for each candidate, one direct HTTP call to a free model (§10) with a tiny prompt → a "take". Gate it (length ≤250, no banned phrases, no em/en dashes, no CoT preamble, no duplicate opening shape). Cap 8 takes, 14 draft attempts per run.
3. **Post** (second browser context — always a FRESH context after scraping): for each take, `browser_thread.post_one(page, take, reply_to=tid)` (the reply flow, §5). 3–6 s between posts.
4. **Update state**: successes → `replied`; failures → `failed` strikes (2 = blacklisted, logged `BLACKLISTED <tid>`).
5. **Report**: prints one line — `reply-guy direct: replied N (targets A / trending B), M post-failures`. Empty stdout = silent (nothing posted). Exit 2 = not logged in (say loudly: run browser_login.py). Exit 1 = OpenRouter key rejected 401.

Constants:

```python
# VERBATIM from reply_guy_direct.py (as patched 2026-09-07)

MAX_REPLIES = 8        # hard cap per run (same as the agent cron)
MAX_DRAFT_ATTEMPTS = 14
MAX_TAKE_CHARS = 250   # X's counter weights punctuation; 250 python chars is safe

# Rajat's rule: cron engines run the most-available :free model first.
# glm-5.2:free DELISTED 2026-09-06 (HTTP 404) - kept LAST as a harmless
# fallback slot, never primary again.
MODELS = json.loads(os.environ.get("REPLY_MODELS", '["minimax/minimax-m3:free", "google/gemma-4-31b-it:free", "z-ai/glm-5.2:free"]'))
```

The model chain (`llm()`): try `MODELS[0]` via OpenRouter → if 429 retry twice with backoff (12 s, 24 s) → reject output if it starts with a CoT marker (`"here's a thinking process"`, `"let me think"`, `"reasoning:"`, `"step 1:"`, …) → fall to `gemini-2.5-flash` via Google's native endpoint with `thinkingBudget: 0` → then remaining OpenRouter models. `KEY_DEAD` (401) aborts the whole run loudly. Attribution is logged per take: `TAKE via minimax/minimax-m3:free: <tid> (<text[:80]>)` — free endpoints report no usage, so this line is the only accounting.

The take prompt (system + one user prompt per candidate) is a full voice spec — the gist: reply as one real AI engineer, own opinions, concrete nouns, no template shapes, no personified systems, zero em/en dashes, no hashtags, short beats full, and **the FACT GATE: never state a number/model/claim not present in the tweet itself**; output exactly `SKIP` if nothing earns a reply.

The scrape retry pass (added 2026-09-07 after three days of the profile-throttle incident — see §9):

```python
# VERBATIM from reply_guy_direct.py scrape_pool() — retry pass (patched 2026-09-07)

    emptied = []
    for handle in rg.TARGETS:
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
    if emptied:
        rg.log(f"RETRY pass for {len(emptied)} empty profiles: "
               + ", ".join("@" + h for h in emptied))
        time.sleep(45)
        for handle in emptied:
            try:
                tweets = rg.scrape_profile(page, handle)
            except Exception as e:
                rg.log(f"retry scrape {handle} failed: {e}")
                continue
            absorb(handle, tweets)
            rg.human_delay(2, 4)
```

`absorb(handle, tweets)` = filter to fresh/unseen/un-blacklisted, sort newest first, extend pool up to `MAX_PER_TARGET` per handle, roll `seen[handle]` to last 20.

Legacy manual CLI (`reply_guy.py`, agent-driven era, still useful for one-offs): `scrape` (JSON candidates), `trending`, `reply <tweet_id> -f take.txt`, `state`, `verify <handle...>` (checks handles exist/active — use before adding targets).

---

## 8. The full cron topology (what runs when, who stays silent)

Hermes cron jobs (job registry: `%LOCALAPPDATA%\hermes\cron\jobs.json`; no_agent jobs = script stdout is the delivery, EMPTY stdout = silent success):

| Job | Schedule (ET) | Script | Account/action | Silence rule |
|---|---|---|---|---|
| twitter-reply-guy | 07,10,12,14,16,20:00 | `reply_guy_direct_cron.py` → `reply_guy_direct.py` | replies on @first_sauce_lab | silent if nothing posted |
| twitter-daily-drafts | 08:00 | agent job (minimax free) | writes 3 drafts to `twitter-bot/drafts/draft_YYYYMMDD_HHMMSS_N.txt` | delivers report |
| twitter-ai-skills-drafts | 15:00 | agent job (minimax free) | same, "AI skills" beat | delivers report |
| twitter-codex-infographic | 11:00 | agent job (deepseek — Rajat's choice) | Codex-built infographic + post | delivers |
| post slots (x6: 09:00, 10:30, 13:00, 16:30, 18:00, 21:00 + one 21:30) | :00/:30 | `legolasbot_post_next.py` → `browser_post.py` | drains `drafts/` oldest-first | EMPTY queue = silent, exit 0; session dead = silent, draft KEPT |
| news monitor | :25 & :55 | `news_monitor_cron.py` → `news_monitor.py` | RSS → verified post | silent when nothing new |
| model monitor | :05 every 4h | `model_monitor_cron.py` → `model_monitor.py` | frontier model drops | silent unless verified drop |
| followers check | 08:30 | `followers_check_cron.py` | count check | silent unless grew |
| impressions check | 09:05 | `impressions_cron.py` | 7-day impressions | silent unless grew |
| follow spread (x4: 06:45, 09:15, 15:15, 21:30) | spread slots | `follow_spread_cron.py` → `follow_accounts.py` | pool-driven follows | silent unless a follow landed |
| pool replenish | 14:30 (M/W/F/Sat) | `replenish_pool_cron.py` | discovery + verify | silent |
| gateway watchdog | every 5m | `gateway_watchdog.py` | hermes process | silent when healthy |

Draft-slot details that matter (from the job prompt, non-negotiable): drafts must be genuinely interesting (quality bar: would a smart person stop scrolling?); every fact verified against live sources (the prompt even re-verifies against `openrouter.ai/api/v1/models` before any number is used); **hard 250-python-char cap with X-estimate under 250** — X's counter weighs punctuation ~7% heavier than python `len()` (2026-08-24: a 277-char draft counted 296 on X and the Post button stayed disabled, blocking the slot); **files at the ROOT of drafts/**, never in subfolders (except the script's own `overlong/` quarantine); no helper scripts in drafts/.

`legolasbot_post_next.py` gate — the 250 quarantine (VERBATIM, abridged):

```python
    # 250-char gate (X's counter weights punctuation ~7% heavier than python
    # len — 2026-08-24: 277 python chars counted as 296 by X and the Post
    # button stayed disabled). Quarantine anything at/over 250.
    try:
        text = open(nxt, encoding="utf-8").read().strip()
        if len(text) > 250:
            qdir = os.path.join(DRAFTS_DIR, "overlong")
            os.makedirs(qdir, exist_ok=True)
            qpath = os.path.join(qdir, label)
            os.replace(nxt, qpath)
            print(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] quarantined {label} "
                  f"({len(text)} chars > 280) -> drafts/overlong/")
            return 3
```

Post-slot exit semantics: `0` posted-and-draft-deleted; `2` = not logged in — **draft is NOT consumed**, stays queued; queue empty = normal, silent, exit 0.

Follow spread mechanics (`follow_spread_cron.py` + `follow_accounts.py`):

```python
# VERBATIM from follow_spread_cron.py (pool discipline)

MAX_FOLLOWS = 25  # per session; X rate-limits ~30 follows per 15 min on young accounts
# handles are marked done AFTER the attempt (followed OR failed) so a dead
# handle or rate-limited click is never retried forever.
# Prints ONLY when at least one new follow landed. Empty stdout = silent cron.
```

```python
# VERBATIM from follow_accounts.py (the click + verify flip)

                btn = page.locator(
                    '[data-testid="primaryColumn"] button[data-testid$="-follow"]').first
                if btn.count() == 0:
                    skipped.append(h)
                    log(f"SKIP {h} (no header follow button / already following)")
                    continue
                btn.click()
                # verify the flip: the SAME button becomes "-unfollow"
                flipped = False
                for _ in range(8):
                    human_delay(0.6, 1.2)
                    if page.locator(
                            '[data-testid="primaryColumn"] button[data-testid$="-unfollow"]').count() > 0:
                        flipped = True
                        break
                if flipped:
                    followed.append(h)
                    log(f"FOLLOWED {h}")
                else:
                    # maybe a confirm sheet appeared (X asks "Follow @x?")
                    sheet = page.locator('[data-testid="confirmationSheetConfirm"]')
                    if sheet.count():
                        sheet.first.click()
                        human_delay(1.0, 2.0)
                        if page.locator(
                                '[data-testid="primaryColumn"] button[data-testid$="-unfollow"]').count() > 0:
                            followed.append(h)
                            log(f"FOLLOWED {h} (via confirm)")
                        else:
                            failed.append(h)
                            log(f"FAIL {h} (confirm did not take)")
                    else:
                        failed.append(h)
                        log(f"FAIL {h} (no flip to Following)")
```

The selector scoping comment matters: the right sidebar ("Who to follow") ALSO renders buttons whose testid ends in `-follow` — those are suggestions and must never be clicked; always scope to `[data-testid="primaryColumn"]`. Log line format `SUMMARY followed=N skipped=M failed=K` is parsed by the cron wrapper for its silent-until-hit rule.

News monitor (`news_monitor.py`): polls 6 official RSS feeds (openai.com/news/rss.xml, blog.google/technology/ai/rss/, deepmind.google/blog/rss.xml, huggingface.co/blog/feed.xml, techcrunch AI feed, theverge AI feed). Caps tightened 2026-09-05 after a reach audit: **1 post per run (was 3), 3 per day (was 6)** — back-to-back posts cannibalize reach under the AI recommender; own-posts stay near the playbook ceiling. Summary ≤170 chars condensed by a DeepSeek call grounded strictly in the article text; degrades to title+link if the key is missing. `seen` set must GROW (a fixed 40-key window re-cycled the full OpenAI archive in the 2026-08-14 incident — items are marked seen only after a confirmed post; TEST mode never writes state).

---

## 9. Failure playbook — every incident class we know, with the fix

| Symptom | Meaning | Fix |
|---|---|---|
| `ERROR: reply button not found` (replies.log/threads.log) | Permalink loaded but `[data-testid="reply"]` absent within 15 s. Per-tweet (deleted between scrape and goto, replies restricted, account state) — NOT session death: other posts succeed same run | Two-strike blacklist (state `failed`, count ≥2 = never drafted again). Success clears a strike. First failure is one strike — allowed one retry |
| `ERROR: composer textarea not found` | Page rendered but composer didn't appear after clicking reply | Same strike/retry treatment. If it repeats on many tweets → likely a bot check/page change → check session manually (`--check`), report |
| `UNSURE: no toast, no close` / `composer still has text` | Post may or may not have landed | Never assume. Manual check; return code 4. The profile-page fallback id-grab covers most cases |
| Profiles render EMPTY mid-run (WARN in replies.log), same block every run | X profile-view throttle: after ~18–26 profile loads in a session X serves empty pages for ~90 s, then recovers (observed every run 09-04→09-07; the account is NOT blocked — light sessions render those profiles fine) | Retry pass after 45 s pause (patched into scrape_pool). **Do NOT diagnose by launching extra probe runs — probes deepen the throttle and confound measurements** |
| `NOT LOGGED IN` / session revoked | X revoked the session. Known trigger: automation bursts (45-profile follow runs did it 2026-09-04) | Run `browser_login.py` HEADED; Rajat types credentials; verify with `--check`. Drafts are never consumed while dead |
| Post button disabled at slot time | Draft over X's real counter (~7% over python len at 250+) | 250-python-char hard gate + quarantine to `drafts/overlong/` |
| RSS archive replay ("new" items are 2015-era) | Seen window truncated instead of growing | Seen set grows forever (cap 5000 defensive); items seen only after confirmed post |
| Model returns CoT preamble / empty / filler | Free model degraded or shell (empty-200) | Chain: CoT-marker guard on first 140 chars → descend chain → never post it. 429 = retry same model twice (12 s, 24 s) before descending |
| `z-ai/glm-5.2:free` 404 | **Delisted** (2026-09-06). Free tiers vanish without notice — a 404 on a pinned `:free` slug is a delisting, not a config bug | Repin to `minimax/minimax-m3:free` (jobs.json hot-edit, backup first). 2026-09-07: also reordered in reply_guy_direct.py |
| Profile SingletonLock error | Two browser contexts on the same profile | Find the other process (`wmic ... get commandline`), wait or kill it; never force |
| Follows stop landing (0 followed) | X follow rate limit (~30/15 min on young accounts) or all pool done | Silent run by design; pool `done` map prevents retry loops; next spread slot retries fresh handles |
| Posting works but profile views throttle | Covered above | Retry pass; spread slots keep total daily profile loads bounded |

General debugging commands:

```bash
# is a browser job running right now? (grep the engine name)
wmic process where "name='python.exe'" get processid,creationdate,commandline

# watch the last reply-guy activity
tail -20 C:/Users/Rajat/twitter-bot/logs/replies.log

# session alive?
"C:/Users/Rajat/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe" C:/Users/Rajat/twitter-bot/browser_post.py --check
```

---

## 10. The LLM side (no browser) — free endpoints, verified chains

Engines read keys BY NAME from `%LOCALAPPDATA%\hermes\.env` (`OPENROUTER_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`) — never hardcode, never log values.

- **OpenRouter free primary: `minimax/minimax-m3:free`** (Rajat's standing rule — the free, most-available, most-capable model; 2026-09-06 bake-off: 5/5 clean, ~3 s, real prose). `google/gemma-4-31b-it:free` was 429-throttled that day; `cohere/north-mini-code:free` etc. returned HTTP 200 with EMPTY output (shells — unusable); `nvidia/nemotron-3.5-lightning:free` leaks CoT and runs ~38 s.
- **Gemini 2.5 flash free** (reliable fallback): native endpoint `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GOOGLE_API_KEY}`, `systemInstruction` separate from `contents`, **`thinkingConfig.thinkingBudget: 0`** or output includes reasoning. Free tier ~250 req/day.
- OpenAI-compat request shape to OpenRouter: `POST https://openrouter.ai/api/v1/chat/completions`, `Authorization: Bearer`, temperature 0.95, max_tokens ~320, urllib only (no SDKs needed). 401 = abort loudly naming the env key; 429 = backoff; empty/CoT = descend.

---

## 11. Content rules for @first_sauce_lab (what may be posted)

Loaded per-job from skills (`first-sauce-voice`, `humanizer`) — the summary:

1. One real person who works with AI daily; opinions owned, plain; contractions; never lowercase-everything.
2. **Zero em dashes or en dashes. Never**: delve, unpack, harness, leverage, game-changer, pivotal, "here's the thing", "let me break this down", "the future of X", "experts say", "sources indicate".
3. No template shapes, no catchphrases, no hashtags, no persona performance, no personified systems, no symmetrical antithesis framing.
4. Concrete nouns over philosophy; one line is a complete reply; match the tweet's register.
5. **Fact gate:** commentary only — never state a number/model/claim not in the tweet (reply-guy) or not verified live (drafts). Verification BEFORE writing, always.
6. 250-char cap (python len) with X-estimate under the hard cap.
7. SKIP discipline: if nothing earns a reply, output `SKIP` — never filler.

---

## 12. If you (Mochi) need to act manually — quick recipes

1. **Verify session:** `browser_post.py --check` (venv python).
2. **Post a file:** `browser_post.py --file path.txt` — it must already pass the gates; the script rejects >280 only.
3. **One reply:** `reply_guy.py reply <tweet_id> -f take.txt` (writes state, logs).
4. **Scrape a candidate pool to JSON:** `reply_guy.py scrape` / `trending` (agent-era tools, still functional).
5. **Check a handle exists:** `reply_guy.py verify <handle>`.
6. **Re-run a cron job manually:** Hermes `cronjob action=run` on the job id — never launch the engine's browser context yourself while a scheduled run may be in flight; wait for slot boundaries (§8 times).
7. **Backup before touching state:** copy `logs/*.json` and `cron/jobs.json` first (`*.bak-<timestamp>` pattern is the house style).
8. **Never** post content you generated without the verification gate passing; never post drafts that didn't come through the drafting pipeline without re-running the gates yourself.

---

## 13. File inventory (authoritative)

| File | Purpose |
|---|---|
| `twitter-bot/browser_login.py` | Headed login (human types credentials) |
| `twitter-bot/browser_post.py` | Plain tweet + image, home composer, `--check` |
| `twitter-bot/browser_thread.py` | Threads + the reply flow (`post_one`), `--check` |
| `twitter-bot/reply_guy.py` | Constants, state, scrapers, legacy CLI (scrape/reply/state/verify/trending) |
| `twitter-bot/reply_guy_direct.py` | Direct-API reply engine (the 6×/day cron workhorse) |
| `twitter-bot/follow_accounts.py` | Follow clicker with flip verification |
| `hermes/scripts/legolasbot_post_next.py` | Draft-queue drainer (250 gate, quarantine, silent-keep) |
| `hermes/scripts/follow_spread_cron.py` | Pool-driven follow spread (silent-until-hit) |
| `hermes/scripts/news_monitor.py` + `_cron.py` | RSS news monitor (1/run, 3/day) |
| `hermes/scripts/model_monitor.py` + `_cron.py` | Frontier model drop monitor |
| `hermes/scripts/impressions_cron.py`, `followers_check_cron.py` | Growth counters (silent unless grew) |
| `twitter-bot/logs/*.log` + `*_state.json` | All history and dedupe state |

Everything above was read from source on 2026-09-07 and cross-checked against three days of incident logs. When in doubt, the code on disk is the truth — and when Rajat says a number must be verified, verify it live before it ships.

*— End of playbook. No credentials were included because none belong in code; the session is the only credential, and the human owns it.*


---

# APPENDIX — FULL LITERAL SOURCE OF EVERY WORKFLOW SCRIPT
Verbatim, unmodified file contents. The prose sections above stay authoritative;
where they disagree with a file, the file wins.

## FULL SOURCE: `reply_guy_direct.py`  (409 lines, 17531 bytes)

```python
#!/usr/bin/env python3
"""Direct-API reply-guy engine for @first_sauce_lab (no agent loop).

Replaces the agent-driven twitter-reply-guy cron (Rajat 2026-09-04: stop
burning DeepSeek tokens on agent-context re-reads). This script:
  1. scrapes TARGET profiles + trending searches via reply_guy.py helpers
  2. drafts takes with a direct OpenRouter :free model call (default
     z-ai/glm-5.2:free, fallback chain) - tiny prompts, no agent context
  3. code-gates each take (length, banned machine tells, catchphrases)
  4. posts up to 8 via browser_thread.post_one (proven reply flow)
  5. updates reply_guy_state.json exactly like the old flow

Cost: ~1 small API call per candidate instead of ~800k tokens of agent
loop context per run. Zero $ on :free endpoints. Model override via env
REPLY_MODEL; key via OPENROUTER_API_KEY (process env or hermes .env).

Exit codes: 0 ok (silent stdout when nothing posted), 1 crash, 2 dead
session (prints error). Run under sys.executable from the cron wrapper.
"""
import json, os, re, sys, time, random, datetime, urllib.request, urllib.error

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
# glm-5.2:free DELISTED 2026-09-06 (HTTP 404) - kept LAST as a harmless
# fallback slot, never primary again.
MODELS = json.loads(os.environ.get("REPLY_MODELS", '["minimax/minimax-m3:free", "google/gemma-4-31b-it:free", "z-ai/glm-5.2:free"]'))
MAX_REPLIES = 8        # hard cap per run (same as the agent cron)
MAX_DRAFT_ATTEMPTS = 14
MAX_TAKE_CHARS = 250   # X's counter weights punctuation; 250 python chars is safe

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
                time.sleep(12 * (attempt + 1))
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
    """GLM-5.2:free primary (Rajat 2026-09-04); Gemini 2.5 flash free is the
    fallback when GLM 429s or leaks reasoning; minimax/gemma last resort.
    Returns (text, model_name), (KEY_DEAD, None) or (None, None)."""
    for model in MODELS[:1]:
        txt, dead = _openrouter(model, prompt)
        if dead:
            return KEY_DEAD, None
        if txt and not any(mark in txt[:140].lower() for mark in COT_MARKERS):
            return txt, model
    txt = _gemini(prompt)
    if txt and not any(mark in txt[:140].lower() for mark in COT_MARKERS):
        return txt, f"gemini:{GEMINI_MODEL}"
    for model in MODELS[1:]:
        txt, dead = _openrouter(model, prompt)
        if dead:
            return KEY_DEAD, None
        if txt and not any(mark in txt[:140].lower() for mark in COT_MARKERS):
            return txt, model
    return None, None


def gate(txt):
    t = txt.strip()
    if not t:
        return None
    if t.upper().startswith("SKIP"):
        return None
    if len(t) > MAX_TAKE_CHARS or len(t) < 3:
        return None
    if "—" in t or "–" in t:
        return None
    low = t.lower()
    if any(b in low for b in BANNED):
        return None
    # quote-wrapped output from the model
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        t = t[1:-1].strip()
        if not t:
            return None
    return t


def draft_prompt(cand):
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
        pool.extend({"handle": handle, **t} for t in fresh[:rg.MAX_PER_TARGET])
        seen[handle] = (seen[handle] + [t["id"] for t in tweets])[-20:]
        return True

    emptied = []
    for handle in rg.TARGETS:
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
    if emptied:
        rg.log(f"RETRY pass for {len(emptied)} empty profiles: "
               + ", ".join("@" + h for h in emptied))
        time.sleep(45)
        for handle in emptied:
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
    seen["_trending"] = (list(seen_trending) + [t["id"] for t in top])[-40:]
    pool.extend(top)  # trending after targets

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
    if not KEY and not KEY_GEMINI:
        print("reply-guy direct: no API keys - add GOOGLE_API_KEY or OPENROUTER_API_KEY to hermes .env")
        return 1
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
    if not pool:
        return 0  # nothing fresh - silent

    # ---- 2. draft ----
    takes, attempts, api_fails = [], 0, 0
    for cand in pool:
        if len(takes) >= MAX_REPLIES or attempts >= MAX_DRAFT_ATTEMPTS:
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
            continue
        rg.log(f"TAKE via {mdl}: {cand['id']} ({take[:80]})")
        # no two takes sharing an opening shape this run
        if any(take[:40].lower() == prev[:40].lower() for _, prev in takes):
            continue
        takes.append((cand["id"], take))

    if not takes:
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

    n_targets = sum(1 for tid, _ in posted if tid in {c["id"] for c in pool if c.get("handle")})
    n_trend = len(posted) - n_targets
    line = (f"reply-guy direct: replied {len(posted)} "
            f"(targets {n_targets} / trending {n_trend})")
    if len(posted) < len(takes):
        line += f", {len(takes) - len(posted)} post-failures"
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `reply_guy.py`  (326 lines, 12990 bytes)

```python
#!/usr/bin/env python3
"""Reply-guy engine for First Sauce Labs: scrape big AI accounts, post takes.

Modes:
  scrape                     -> print JSON candidates (tweets not yet seen/replied)
  reply <tweet_id> -f f.txt  -> post file text as a reply to that tweet
  state                      -> show replied/seen counts

State file: logs/reply_guy_state.json  ({"seen": {handle: [ids]}, "replied": [ids]})
Read-only scrape; the only write action is reply (posts to X).
"""
import os, sys, json, time, random, argparse, datetime
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
    "sama", "OpenAI", "AnthropicAI", "GoogleDeepMind", "xai", "elonmusk",
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
]
MAX_PER_TARGET = 8
FRESH_HOURS = 48

# AI keywords for the trending search scrape (Top-sorted = high engagement).
TRENDING_QUERIES = ["AI", "GPT", "Claude", "OpenAI", "Anthropic", "AGI", "LLM", "Gemini", "Grok"]
MAX_TRENDING = 10

def human_delay(a=1.0, b=2.5):
    time.sleep(random.uniform(a, b))

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
    page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60_000)
    human_delay(2, 4)
    if "login" in page.url:
        return False
    try:
        page.wait_for_selector('[data-testid="SideNav_NewTweet_Button"]', timeout=15_000)
        return True
    except Exception:
        return False

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

def scrape_search(page, query):
    """Return list of {author, id, text, time} from Top-sorted search results."""
    out = []
    url = f"https://x.com/search?q={quote(query)}&f=top"
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
```

## FULL SOURCE: `browser_post.py`  (148 lines, 5640 bytes)

```python
#!/usr/bin/env python3
"""Post a tweet via the X web UI (browser automation), using the persistent
session created by browser_login.py. No API keys needed.

Usage:
  python3 browser_post.py "tweet text"
  python3 browser_post.py --file drafts/xxx.txt
  python3 browser_post.py --check          # verify session, post nothing

Exit codes: 0 posted, 2 not logged in, 3 blocked/captcha, 4 other error
"""
import os, sys, time, random, argparse, datetime

from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")

def human_delay(a=1.0, b=2.5):
    time.sleep(random.uniform(a, b))

def log(line):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {line}")
    with open(os.path.join(BOT, "logs", "posts.log"), "a", encoding="utf-8") as f:
        f.write(f"{ts} {line}\n")

def check_session(page):
    """True only if the page shows real logged-in UI, not just a shell."""
    page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60_000)
    human_delay(2, 4)
    if "login" in page.url:
        return False
    # logged-in home timeline has the "Post" nav button and the composer
    try:
        page.wait_for_selector('[data-testid="SideNav_NewTweet_Button"]', timeout=15_000)
        return True
    except Exception:
        return False

def post_text(page, text, image_path=None):
    # X's /compose/post page is unstable (2026-08-24: hidden duplicate dialog +
    # mask overlay + unsent-draft restore sheets). The home inline composer is
    # reliable; Ctrl+Enter submits without needing a button click.
    page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60_000)
    human_delay(2, 4)
    if "login" in page.url:
        return 2
    try:
        page.wait_for_selector('[data-testid="tweetTextarea_0"]', timeout=20_000)
    except Exception:
        log("ERROR: composer textarea not found (page change or bot check)")
        return 3
    box = page.locator('[data-testid="tweetTextarea_0"]').last
    if image_path:
        try:
            # hidden file input on the compose page
            fi = page.locator('input[data-testid="fileInput"]').first
            fi.set_input_files(image_path, timeout=20_000)
            log(f"image attached: {image_path}")
        except Exception as e:
            log(f"ERROR: could not attach image: {e}")
            return 3
        human_delay(3, 5)  # upload + preview render
        # sanity: media preview present (attachments container with a rendered image)
        try:
            page.wait_for_selector('[data-testid="attachments"] img', timeout=20_000)
            log("media preview confirmed")
        except Exception:
            log("WARN: media preview not confirmed, posting anyway")
    box.click()
    human_delay(0.4, 1.0)
    box.type(text, delay=random.randint(25, 60))
    human_delay(0.8, 1.8)
    # submit via the inline Post button (tweetButtonInline on home); Ctrl+Enter fallback
    btn = page.locator('[data-testid="tweetButtonInline"]').last
    try:
        if btn.count():
            btn.click(timeout=10_000)
        else:
            page.keyboard.press('Control+Enter')
    except Exception:
        page.keyboard.press('Control+Enter')
    human_delay(2, 4)
    # verify: composer should be gone / toast appeared
    try:
        page.wait_for_selector('[data-testid="toast"]', timeout=10_000)
        log("POSTED (toast confirmed)")
        return 0
    except Exception:
        # home inline composer stays in the DOM after posting — check it cleared
        try:
            page.wait_for_function(
                '() => { const el = document.querySelector(\'[data-testid="tweetTextarea_0"]\'); return !el || el.innerText.trim() === ""; }',
                timeout=8_000)
            log("POSTED (composer cleared)")
            return 0
        except Exception:
            log("UNSURE: no toast, composer still has text — manual check needed")
            return 4

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default=None)
    ap.add_argument("--file", default=None)
    ap.add_argument("--image", default=None)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.check:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(PROFILE, headless=True,
                                                       viewport={"width":1280,"height":900})
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            ok = check_session(page)
            ctx.close()
        print("SESSION OK" if ok else "NOT LOGGED IN")
        return 0 if ok else 2

    if args.file:
        text = open(args.file, encoding="utf-8").read().strip()
    elif args.text:
        text = args.text.strip()
    else:
        text = sys.stdin.read().strip()

    if not text:
        log("ERROR: empty tweet")
        return 4
    if len(text) > 280:
        log(f"ERROR: too long ({len(text)} chars)")
        return 4

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True, viewport={"width":1280,"height":900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not check_session(page):
            log("NOT LOGGED IN — run browser_login.py")
            ctx.close()
            return 2
        rc = post_text(page, text, image_path=args.image)
        ctx.close()
        return rc

if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `browser_thread.py`  (182 lines, 6471 bytes)

```python
#!/usr/bin/env python3
"""Post a threaded post (tweet thread) via the X web UI (browser automation).

Thread file format: plain text, tweet parts separated by a line containing
exactly <<<BREAK>>>. Example:

  part one of the thread.
  <<<BREAK>>>
  part two, a reply to part one.
  <<<BREAK>>>
  part three.

Usage:
  python3 browser_thread.py --file thread.txt
  python3 browser_thread.py --check

Exit codes: 0 posted fully, 2 not logged in, 3 blocked/error, 4 file issue
"""
import os, sys, time, random, argparse, datetime

from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")

def human_delay(a=1.0, b=2.5):
    time.sleep(random.uniform(a, b))

def log(line):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {line}")
    with open(os.path.join(BOT, "logs", "threads.log"), "a", encoding="utf-8") as f:
        f.write(f"{ts} {line}\n")

def check_session(page):
    page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60_000)
    human_delay(2, 4)
    if "login" in page.url:
        return False
    try:
        page.wait_for_selector('[data-testid="SideNav_NewTweet_Button"]', timeout=15_000)
        return True
    except Exception:
        return False

def composer_box(page):
    """Return the composer textarea locator (dialog-first)."""
    dialog = page.locator('[role="dialog"]')
    if dialog.count():
        return dialog.locator('[data-testid="tweetTextarea_0"]').first
    return page.locator('[data-testid="tweetTextarea_0"]').last

def post_button(page):
    dialog = page.locator('[role="dialog"]')
    if dialog.count():
        return dialog.locator('[data-testid="tweetButton"]')
    return page.locator('[data-testid="tweetButton"]').last

def post_one(page, text, reply_to=None):
    """Post a single tweet; reply_to = tweet id to reply to (thread)."""
    if reply_to:
        page.goto(f"https://x.com/{USER}/status/{reply_to}", wait_until="domcontentloaded", timeout=60_000)
        human_delay(2, 4)
        try:
            page.wait_for_selector('[data-testid="reply"]', timeout=15_000)
            page.locator('[data-testid="reply"]').first.click()
        except Exception:
            log("ERROR: reply button not found")
            return None
        human_delay(1, 2)
    else:
        page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=60_000)
        human_delay(2, 4)
    if "login" in page.url:
        return None
    try:
        page.wait_for_selector('[data-testid="tweetTextarea_0"]', timeout=20_000)
    except Exception:
        log("ERROR: composer textarea not found")
        return None
    box = composer_box(page)
    box.click()
    human_delay(0.4, 1.0)
    box.type(text, delay=random.randint(25, 60))
    human_delay(0.8, 1.8)
    btn = post_button(page)
    if not btn.count():
        log("ERROR: Post button not found")
        return None
    btn.click()
    human_delay(2, 4)
    # grab the new tweet id from the toast's View link if present (reliable),
    # else fall back to the profile page's first status link.
    tid = None
    try:
        page.wait_for_selector('[data-testid="toast"]', timeout=10_000)
        view = page.locator('[data-testid="toast"] a[href*="/status/"]').first
        if view.count():
            href = view.get_attribute("href")
            if href and "/status/" in href:
                tid = href.split("/status/")[1].split("?")[0]
    except Exception:
        pass
    if tid is None:
        try:
            page.wait_for_selector('[data-testid="tweetTextarea_0"]', state="detached", timeout=8_000)
        except Exception:
            log("UNSURE: no toast, no close — aborting")
            return None
    if tid is None:
        human_delay(2, 3)
        page.goto(f"https://x.com/{USER}", wait_until="domcontentloaded", timeout=60_000)
        human_delay(2, 3)
        try:
            page.wait_for_selector('a[href*="/status/"]', timeout=15_000)
            href = page.locator('a[href*="/status/"]').first.get_attribute("href")
            tid = href.split("/status/")[1].split("?")[0]
        except Exception:
            log("WARNING: could not read tweet id from profile")
            return "unknown"
    return tid

def main():
    global USER
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=None)
    ap.add_argument("--user", default="first_sauce_lab")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    USER = args.user

    if args.check:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(PROFILE, headless=True,
                                                       viewport={"width":1280,"height":900})
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            ok = check_session(page)
            ctx.close()
        print("SESSION OK" if ok else "NOT LOGGED IN")
        return 0 if ok else 2

    if not args.file or not os.path.exists(args.file):
        log(f"ERROR: no thread file: {args.file}")
        return 4
    raw = open(args.file, encoding="utf-8").read().strip()
    parts = [p.strip() for p in raw.split("<<<BREAK>>>") if p.strip()]
    if not parts:
        log("ERROR: empty thread")
        return 4
    for i, p in enumerate(parts):
        if len(p) > 280:
            log(f"ERROR: part {i+1} too long ({len(p)} chars)")
            return 4

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True, viewport={"width":1280,"height":900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not check_session(page):
            log("NOT LOGGED IN — run browser_login.py")
            ctx.close()
            return 2
        posted = 0
        last_id = None
        for i, part in enumerate(parts):
            tid = post_one(page, part, reply_to=last_id)
            if tid is None:
                log(f"FAILED at part {i+1} of {len(parts)} — thread incomplete")
                ctx.close()
                return 3
            posted += 1
            last_id = tid
            log(f"part {i+1}/{len(parts)} posted (id={tid})")
            human_delay(3, 5)
        ctx.close()

    log(f"THREAD POSTED: {posted} parts")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `browser_login.py`  (71 lines, 2695 bytes)

```python
#!/usr/bin/env python3
"""Log into X once so the bot can post from a persistent browser session.

Opens a real (headed) Chromium window with a persistent profile. YOU log in
manually (type your credentials yourself — the script never sees them). Once
you're logged in and see your home timeline, close the window. The session is
saved in the profile and reused by browser_post.py.

Usage: python3 browser_login.py
"""
import os, sys, time

from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")

def main():
    os.makedirs(PROFILE, exist_ok=True)
    print("Opening X login in a real browser window...")
    print("LOG IN YOURSELF. When you see your home timeline, close the window.")
    print("(You have up to 3 minutes.)")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE,
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://x.com/login", wait_until="domcontentloaded")
        # Wait up to 3 minutes for the user to finish logging in and close.
        try:
            page.wait_for_url("https://x.com/home", timeout=180_000)
        except Exception:
            pass
        ctx.close()
    # verify session: open a quick headless check
    ok = check_session()
    if ok:
        print("LOGIN OK — session saved. Bot can post.")
    else:
        print("WARNING: could not verify session. Try again and make sure you reach the home timeline.")

def check_session():
    """Reliable check: logged-in-only DOM marker (SideNav_NewTweet_Button).

    The old URL-only check (`\"login\" not in url`) is NOT trustworthy — a
    logged-out x.com/ splash contains no \"login\" and falsely reports OK.
    """
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                PROFILE, headless=True,
                viewport={"width": 1280, "height": 900})
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=45_000)
            time.sleep(3)
            try:
                page.wait_for_selector('[data-testid="SideNav_NewTweet_Button"]', timeout=15_000)
                ok = True
            except Exception:
                ok = False
            ctx.close()
            return ok
    except Exception as e:
        print(f"  session check error: {e}")
        return False

if __name__ == "__main__":
    main()
```

## FULL SOURCE: `follow_accounts.py`  (105 lines, 4596 bytes)

```python
#!/usr/bin/env python3
"""Follow a curated list of real AI accounts for @first_sauce_lab.

Discovery lever: a new account following 14 accounts is invisible. Following
the real AI ecosystem (labs + the people the reply-guy engine already
engages) puts the profile in front of that audience and unlocks normal X
discovery. NOT follow-for-follow farming — these are accounts the bot
genuinely engages with every day via reply_guy.py.

Usage: python3 follow_accounts.py [handle...]  (defaults to the reply-guy list)
Run with python3 (WindowsApps). Logs to logs/follows.log.
"""
import os, sys, time, random, datetime

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)  # append, never insert: bot dir's queue.py must not shadow stdlib
from playwright.sync_api import sync_playwright
import browser_post

FOLLOW_LOG = os.path.join(BOT, "logs", "follows.log")

DEFAULT_HANDLES = [
    # labs + leaders (same list the reply-guy engine watches)
    "sama", "OpenAI", "AnthropicAI", "GoogleDeepMind", "xai", "elonmusk",
    "karpathy", "gdb", "miramurati", "ilyasut", "kevinweil", "markchen90",
    "jackclarkSF", "janleike", "TobyWalsh", "thsottiaux",
    "ylecun", "simonw", "goodside", "hwchase17", "_philschmid", "AndrewYNg",
    "sundarpichai", "jun_song",
]

def human_delay(a=1.5, b=3.5):
    time.sleep(random.uniform(a, b))

def log(line):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with open(FOLLOW_LOG, "a", encoding="utf-8") as f:
        f.write(f"{ts} {line}\n")
    print(f"{ts} {line}")

def main():
    handles = sys.argv[1:] or DEFAULT_HANDLES
    followed, skipped, failed = [], [], []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            browser_post.PROFILE, headless=True,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not browser_post.check_session(page):
            ctx.close()
            log("NOT LOGGED IN — aborting follows")
            return 2
        for h in handles:
            try:
                page.goto(f"https://x.com/{h}", wait_until="domcontentloaded", timeout=60_000)
                human_delay()
                # The header follow button lives in the primary column; the
                # right sidebar ("Who to follow") ALSO renders buttons whose
                # testid ends in "-follow" — those are suggestions and must
                # never be clicked. Scope to the primary column.
                btn = page.locator(
                    '[data-testid="primaryColumn"] button[data-testid$="-follow"]').first
                if btn.count() == 0:
                    skipped.append(h)
                    log(f"SKIP {h} (no header follow button / already following)")
                    continue
                btn.click()
                # verify the flip: the SAME button becomes "-unfollow"
                flipped = False
                for _ in range(8):
                    human_delay(0.6, 1.2)
                    if page.locator(
                            '[data-testid="primaryColumn"] button[data-testid$="-unfollow"]').count() > 0:
                        flipped = True
                        break
                if flipped:
                    followed.append(h)
                    log(f"FOLLOWED {h}")
                else:
                    # maybe a confirm sheet appeared (X asks "Follow @x?")
                    sheet = page.locator('[data-testid="confirmationSheetConfirm"]')
                    if sheet.count():
                        sheet.first.click()
                        human_delay(1.0, 2.0)
                        if page.locator(
                                '[data-testid="primaryColumn"] button[data-testid$="-unfollow"]').count() > 0:
                            followed.append(h)
                            log(f"FOLLOWED {h} (via confirm)")
                        else:
                            failed.append(h)
                            log(f"FAIL {h} (confirm did not take)")
                    else:
                        failed.append(h)
                        log(f"FAIL {h} (no flip to Following)")
            except Exception as e:
                failed.append(h)
                log(f"FAIL {h}: {type(e).__name__}: {e}")
        ctx.close()
    log(f"SUMMARY followed={len(followed)} skipped={len(skipped)} failed={len(failed)}")
    if failed:
        log("failed: " + ", ".join(failed))
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `post.py`  (81 lines, 2856 bytes)

```python
#!/usr/bin/env python3
"""Post a generated tweet to X via the API v2 (tweepy, OAuth 1.0a user context).

Modes:
  --post       actually post (requires keys in .env)
  --dry-run    print what would be posted, post nothing (default)

Requires a .env file with:
  X_API_KEY=            (consumer key)
  X_API_SECRET=         (consumer secret)
  X_ACCESS_TOKEN=       (access token)
  X_ACCESS_SECRET=      (access token secret)

Tweet text is read from stdin (piped) or a file argument.
"""
import os, sys, argparse, datetime

def load_env(path=".env"):
    env = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", action="store_true", help="actually post (default is dry-run)")
    ap.add_argument("--file", help="read tweet text from file")
    ap.add_argument("--label", default="manual", help="label for the log line")
    args = ap.parse_args()

    if args.file:
        text = open(args.file, encoding="utf-8").read().strip()
    else:
        text = sys.stdin.read().strip()

    if not text:
        print("ERROR: empty tweet text", file=sys.stderr)
        sys.exit(1)

    if len(text) > 280:
        print(f"ERROR: tweet too long ({len(text)} chars)", file=sys.stderr)
        sys.exit(1)

    env = load_env()
    keys_present = all(env.get(k) for k in
                       ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"))

    ts = datetime.datetime.now().isoformat(timespec="seconds")

    if not args.post or not keys_present:
        mode = "DRY-RUN" if not keys_present else "DRY-RUN(--post w/o keys)"
        print(f"[{ts}] {mode} [{args.label}] {len(text)} chars: {text[:60]}...")
        # log to file for the audit trail
        with open("logs/posts.log", "a", encoding="utf-8") as f:
            f.write(f"{ts} {mode} [{args.label}] {len(text)} chars\n")
        return 0

    import tweepy
    client = tweepy.Client(
        consumer_key=env["X_API_KEY"],
        consumer_secret=env["X_API_SECRET"],
        access_token=env["X_ACCESS_TOKEN"],
        access_token_secret=env["X_ACCESS_SECRET"],
    )
    resp = client.create_tweet(text=text)
    if resp.data and resp.data.get("id"):
        tid = resp.data["id"]
        print(f"[{ts}] POSTED [{args.label}] id={tid}: {text[:60]}...")
        with open("logs/posts.log", "a", encoding="utf-8") as f:
            f.write(f"{ts} POSTED [{args.label}] id={tid} {len(text)} chars\n")
        return 0
    else:
        print(f"[{ts}] ERROR: post failed, response={resp}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `queue.py`  (52 lines, 1714 bytes)

```python
#!/usr/bin/env python3
"""Queue management for drafts.

Drafts live in drafts/ as individual .txt files, one tweet each.
The generator (cron) writes them; post.py publishes them.

Usage:
  python3 queue.py list          # list queued drafts
  python3 queue.py add "text"    # add a draft
  python3 queue.py count         # number of drafts
  python3 queue.py next          # print the oldest draft text
"""
import os, sys, glob, datetime

DRAFTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drafts")

def list_drafts():
    return sorted(glob.glob(os.path.join(DRAFTS_DIR, "*.txt")))

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for p in list_drafts():
            print(os.path.basename(p), "|", open(p, encoding="utf-8").read().strip()[:60])
    elif cmd == "add":
        text = " ".join(sys.argv[2:]).strip()
        if not text:
            print("ERROR: no text", file=sys.stderr); sys.exit(1)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        # unique name
        i = 0
        while True:
            name = f"{ts}_{i}.txt" if i else f"{ts}.txt"
            p = os.path.join(DRAFTS_DIR, name)
            if not os.path.exists(p):
                break
            i += 1
        open(p, "w", encoding="utf-8").write(text)
        print(f"queued: {name}")
    elif cmd == "count":
        print(len(list_drafts()))
    elif cmd == "next":
        drafts = list_drafts()
        if drafts:
            print(open(drafts[0], encoding="utf-8").read().strip())
        else:
            print("(empty)")
    else:
        print("unknown command", file=sys.stderr); sys.exit(1)

if __name__ == "__main__":
    main()
```

## FULL SOURCE: `list_following.py`  (30 lines, 1104 bytes)

```python
#!/usr/bin/env python3
"""List the accounts @first_sauce_lab currently follows."""
import os, sys, time, random
BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
from playwright.sync_api import sync_playwright
import browser_post

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        browser_post.PROFILE, headless=True,
        viewport={"width": 1280, "height": 1000},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://x.com/first_sauce_lab/following", wait_until="domcontentloaded", timeout=60_000)
    time.sleep(random.uniform(3, 5))
    # scroll to load more
    for _ in range(4):
        page.mouse.wheel(0, 2000)
        time.sleep(1.2)
    cells = page.locator('[data-testid="UserCell"]')
    n = cells.count()
    print("USERCELLS:", n)
    for i in range(n):
        try:
            txt = cells.nth(i).inner_text().replace("\n", " | ")
            print(f"[{i}] {txt[:90]}")
        except Exception as e:
            print(f"[{i}] err {e}")
    ctx.close()
```

## FULL SOURCE: `scrape_notifications.py`  (21 lines, 857 bytes)

```python
#!/usr/bin/env python3
"""Scrape Notifications tab for replies to the account."""
import os, time, random
from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, headless=True, viewport={"width": 1280, "height": 1000},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://x.com/notifications", wait_until="domcontentloaded", timeout=60_000)
    time.sleep(random.uniform(4, 6))
    for _ in range(3):
        page.mouse.wheel(0, 1200)
        time.sleep(random.uniform(1.5, 2.5))
    body = page.locator('[data-testid="primaryColumn"]').first.inner_text()
    print(body[:3000])
    ctx.close()
```

## FULL SOURCE: `gen_post_image.py`  (264 lines, 9190 bytes)

```python
#!/usr/bin/env python3
"""Generate First Sauce Labs X post images — text-forward relatable cards.

Visual system: black canvas, neon-green accent (#3DFF6E), Bahnschrift headlines,
Consolas kickers/captions. Two layouts: 'card' (kicker + big line + subline)
and 'split' (two-panel meme with optional meter bar).

Usage: python3 gen_post_image.py            # generate whole batch
       python3 gen_post_image.py <name>     # generate one card by name
Output: C:/Users/Rajat/twitter-bot/assets/posts/<name>.png  (1600x900)
"""
import os, sys, textwrap
from PIL import Image, ImageDraw, ImageFont

ASSETS = r"C:\Users\Rajats\twitter-bot\assets"  # fixed below
ASSETS = r"C:\Users\Rajat\twitter-bot\assets"
OUT = os.path.join(ASSETS, "posts")
FONTS = r"C:\Windows\Fonts"
W, H = 1600, 900

BLACK = (10, 10, 10)
PANEL_A = (13, 13, 13)
PANEL_B = (17, 17, 17)
GREEN = (61, 255, 110)
WHITE = (245, 245, 245)
GRAY = (138, 138, 138)
DIM = (70, 70, 70)

def font(name, size):
    return ImageFont.truetype(os.path.join(FONTS, name), size)

HEADLINE_FONT = "arialbd.ttf"   # static font — variable fonts (bahnschrift) render
                                # unreliably under PIL 12 (drawn width != measured)
MONO_FONT = "consola.ttf"

def wrap(draw, text, fnt, max_w):
    words = text.split()
    lines, cur = [], ""
    for w_ in words:
        t = (cur + " " + w_).strip()
        if draw.textlength(t, font=fnt) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines

def draw_kicker(d, x, y, text):
    d.rectangle([x, y + 14, x + 18, y + 26], fill=GREEN)
    f = font(MONO_FONT, 34)
    d.text((x + 34, y), text, font=f, fill=GRAY)

def draw_caption(d, x, y, text, fill=DIM):
    f = font(MONO_FONT, 28)
    d.text((x, y), text, font=f, fill=fill)

def draw_headline(d, cx, y, lines, fnt, colors, max_w):
    # cx is the CENTER x; left edge = cx - width/2  (NOT (cx - width)/2)
    for i, (line, col) in enumerate(zip(lines, colors)):
        lw = d.textlength(line, font=fnt)
        d.text((cx - lw / 2, y), line, font=fnt, fill=col)
        y += fnt.size + 26
    return y

def card(name, kicker, lines, colors, sub, caption="first sauce labs — lab notes"):
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)
    draw_kicker(d, 90, 80, kicker)
    d.rectangle([90, H - 130, 90 + 300, H - 128], fill=GREEN)
    fnt = font(HEADLINE_FONT, 110)
    wrapped = []
    for ln, col in zip(lines, colors):
        wrapped.extend((w, col) for w in wrap(d, ln, fnt, 1350))
    y = draw_headline(d, W / 2, 300, [w for w, _ in wrapped],
                      fnt, [c for _, c in wrapped], 1350)
    if sub:
        f = font(MONO_FONT, 40)
        sw = d.textlength(sub, font=f)
        d.text(((W - sw) / 2, y + 60), sub, font=f, fill=GRAY)
    draw_caption(d, 90, H - 110, caption)
    return img

def split(name, left_lines, right_lines, meter=None, caption="first sauce labs — lab notes"):
    """meter: (label, pct) drawn under right panel as a fill bar."""
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W // 2, H], fill=PANEL_A)
    d.rectangle([W // 2, 0, W, H], fill=PANEL_B)
    d.rectangle([W // 2 - 3, 0, W // 2 + 3, H], fill=GREEN)
    draw_kicker(d, 70, 70, "me:") if False else None
    # left panel
    f = font(MONO_FONT, 34)
    d.text((70, 70), "me:", font=f, fill=GRAY)
    fnt = font(HEADLINE_FONT, 90)
    lw_max = W // 2 - 160
    y = 200
    for ln in left_lines:
        for w_ in wrap(d, ln, fnt, lw_max):
            d.text((70, y), w_, font=fnt, fill=WHITE)
            y += fnt.size + 18
    # right panel
    d.text((W // 2 + 70, 70), "the context window:", font=f, fill=GRAY)
    y = 200
    for ln in right_lines:
        for w_ in wrap(d, ln, fnt, lw_max):
            d.text((W // 2 + 70, y), w_, font=fnt, fill=WHITE)
            y += fnt.size + 18
    if meter:
        label, pct = meter
        bx, by = W // 2 + 70, 640
        bw, bh = lw_max, 46
        d.text((bx, by - 44), label, font=font(MONO_FONT, 32), fill=GRAY)
        d.rectangle([bx, by, bx + bw, by + bh], outline=DIM, width=3)
        d.rectangle([bx, by, bx + int(bw * pct), by + bh], fill=GREEN)
        pct_txt = f"{int(pct * 100)}%"
        pt = font(MONO_FONT, 30)
        d.text((bx + bw - d.textlength(pct_txt, font=pt) - 12, by + 6),
               pct_txt, font=pt, fill=BLACK)
    draw_caption(d, 90, H - 110, caption)
    return img

BATCH = {
    "context_window": lambda: split(
        "context_window",
        ["the prompt", "is fine.", "just post it."],
        ["wait.", "actually.", "hold on."],
        meter=("context: 196k / 200k tokens", 0.98),
    ),
    "hallucination": lambda: card(
        "hallucination",
        "LAB NOTES",
        ["it's not", "hallucinating.", "it's being", "creative."],
        [WHITE, GREEN, WHITE, GREEN],
        "— someone who ships anyway",
    ),
    "confident_nonsense": lambda: card(
        "confident_nonsense",
        "LAB NOTES",
        ["prompt: act as an expert.", "output: confident nonsense."],
        [GRAY, GREEN],
        "we've all been here.",
    ),
    "dinner": lambda: split(
        "dinner",
        ["me at dinner:", "i work in ai."],
        ["them:", "so you type", "into a computer?"],
    ),
    "we_move": lambda: card(
        "we_move",
        "FIRST SAUCE LABS",
        ["day 47.", "the algorithm", "sees me."],
        [WHITE, GRAY, GREEN],
        "we move.",
    ),
    "relatable_20260811": lambda: card(
        "relatable_20260811",
        "LAB NOTES",
        ["eval says", "98%.", "prod says", "no."],
        [WHITE, GREEN, WHITE, GREEN],
        "both numbers are real. somehow.",
    ),
    "relatable_20260812": lambda: card(
        "relatable_20260812",
        "LAB NOTES",
        ["agent status:", "almost done.", "almost done."],
        [GRAY, GREEN, GREEN],
        "day three. still going.",
    ),
    "relatable_20260813": lambda: card(
        "relatable_20260813",
        "LAB NOTES",
        ["the gpu bill arrived.", "the model got smarter.", "i got poorer."],
        [WHITE, GREEN, WHITE],
        "the numbers check out. mine don't.",
    ),
    "relatable_20260814": lambda: card(
        "relatable_20260814",
        "LAB NOTES",
        ["i set temperature", "to 0.", "it still had", "opinions."],
        [WHITE, GREEN, WHITE, GREEN],
        "determinism is a lie.",
    ),
    "relatable_20260815": lambda: card(
        "relatable_20260815",
        "LAB NOTES",
        ["me: the bug", "is in the prompt.", "model: you're right.", "it's the prompt."],
        [WHITE, GREEN, WHITE, GREEN],
        "the bug was in the code. it always was.",
    ),
    "relatable_20260816": lambda: card(
        "relatable_20260816",
        "LAB NOTES",
        ["one more example", "in the prompt.", "that'll fix it."],
        [WHITE, GREEN, WHITE],
        "it did not fix it.",
    ),
    "relatable_20260817": lambda: card(
        "relatable_20260817",
        "LAB NOTES",
        ["prompt: be concise.", "output: an essay", "on being concise."],
        [GRAY, GREEN, WHITE],
        "the model has thoughts on brevity.",
    ),
    "relatable_20260818": lambda: card(
        "relatable_20260818",
        "LAB NOTES",
        ["the model", "apologized.", "then doubled", "down."],
        [WHITE, GREEN, WHITE, GREEN],
        "sorry doesn't fix the json.",
    ),
    "relatable_20260819": lambda: card(
        "relatable_20260819",
        "LAB NOTES",
        ["it worked", "yesterday.", "the model", "updated itself."],
        [WHITE, GREEN, WHITE, GREEN],
        "nothing is pinned. nothing is safe.",
    ),
}

def verify_bounds(path, border=26):
    """Check that no ink sits within `border` px of the canvas edge (overflow/clip).
    Known allowed edge elements: green divider of split layouts (center, not edge)."""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    bg = {(10, 10, 10), (13, 13, 13), (17, 17, 17)}
    hits = []
    for y in range(h):
        for x in list(range(border)) + list(range(w - border, w)):
            if px[x, y] not in bg:
                hits.append((x, y, px[x, y]))
    for x in range(w):
        for y in list(range(border)) + list(range(h - border, h)):
            if px[x, y] not in bg:
                hits.append((x, y, px[x, y]))
    return hits

def main():
    os.makedirs(OUT, exist_ok=True)
    names = sys.argv[1:] or list(BATCH)
    ok = True
    for n in names:
        if n not in BATCH:
            print(f"unknown card: {n}")
            continue
        img = BATCH[n]()
        p = os.path.join(OUT, f"{n}.png")
        img.save(p)
        hits = verify_bounds(p)
        # green divider at center (x=797..800) is by design; ignore it
        real = [h for h in hits if not (795 <= h[0] <= 805)]
        status = "CLEAN" if not real else f"EDGE HITS {len(real)} e.g. {real[:3]}"
        if real:
            ok = False
        print(f"{status}  {p}")
    if not ok:
        print("!! LAYOUT CHECK FAILED — inspect before posting")
        sys.exit(2)

if __name__ == "__main__":
    main()
```

## FULL SOURCE: `render_profile_assets.py`  (20 lines, 802 bytes)

```python
#!/usr/bin/env python3
"""Render banner + avatar HTML to PNGs via headless Chromium."""
from playwright.sync_api import sync_playwright

JOBS = [
    ("file:///C:/Users/Rajat/twitter-bot/assets/banner.html",
     r"C:\Users\Rajat\twitter-bot\assets\banner.png", 1500, 500),
    ("file:///C:/Users/Rajat/twitter-bot/assets/avatar.html",
     r"C:\Users\Rajat\twitter-bot\assets\avatar.png", 400, 400),
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for url, out, w, h in JOBS:
        page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
        page.goto(url, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(500)
        page.locator("body").screenshot(path=out)
        print("saved:", out)
    browser.close()
```

## FULL SOURCE: `scripts/check_len.py`  (7 lines, 208 bytes)

```python
import sys
for p in sys.argv[1:]:
    with open(p, encoding='utf-8') as f:
        t = f.read().strip()
    print(p, '->', len(t), 'chars')
    if len(t) > 280:
        print('  OVER LIMIT by', len(t) - 280)
```

## FULL SOURCE: `scripts/check_thread.py`  (21 lines, 537 bytes)

```python
import sys

path = sys.argv[1]
with open(path, encoding='utf-8') as f:
    content = f.read()

parts = content.split('<<<BREAK>>>')
print('num parts:', len(parts))
ok = True
for i, p in enumerate(parts, 1):
    stripped = p.strip('\n')
    n = len(stripped)
    status = 'OK' if n <= 280 else 'TOO LONG'
    if n > 280:
        ok = False
    print(f'part {i}: {n} chars [{status}]')
    print('---')
    print(stripped)
    print('---')
print('separator exact:', content.count('<<<BREAK>>>') == len(parts) - 1)
print('all <= 280:', ok)
```

## FULL SOURCE: `reply_guy_direct_cron.py`  (31 lines, 1002 bytes)

```python
#!/usr/bin/env python3
"""Cron wrapper for the direct-API reply-guy engine (no agent loop).

Runs twitter-bot/reply_guy_direct.py under sys.executable (the hermes venv
python, which has playwright since 2026-08-27). Stdout passes through so a
no_agent cron delivers only when the engine has something to say (replies
posted or a hard error); empty stdout = silent run. See follow_spread_cron.py
for the same pattern.
"""
import subprocess, sys

ENGINE = r"C:\Users\Rajat\twitter-bot\reply_guy_direct.py"

def main():
    try:
        r = subprocess.run(
            [sys.executable, ENGINE],
            capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        print("reply-guy direct: TIMEOUT after 30 min")
        return 1
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if out:
        sys.stdout.write(out + "\n")
    if err:
        sys.stderr.write(err + "\n")
    return r.returncode

if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `legolasbot_post_next.py`  (89 lines, 3624 bytes)

```python
#!/usr/bin/env python3
"""Post the next queued LegolasBot draft. Uses browser automation (free) when
no API keys are in .env, otherwise the API.

Script lives in the hermes scripts dir; the bot dir is C:/Users/Rajat/twitter-bot.

Usage: python3 legolasbot_post_next.py [--post]
  --post is accepted for compatibility; posting mode is auto-detected:
    API keys present in .env -> API post
    no keys                  -> browser post (needs session from browser_login.py)

Exit codes: 0 = posted OK, 1 = error, 2 = queue empty / not logged in, 3 = blocked
"""
import os, sys, subprocess, glob, datetime

BOT = r"C:\Users\Rajat\twitter-bot"
DRAFTS_DIR = os.path.join(BOT, "drafts")
ENV_FILE = os.path.join(BOT, ".env")

def has_api_keys():
    if not os.path.exists(ENV_FILE):
        return False
    for line in open(ENV_FILE, encoding="utf-8"):
        line = line.strip()
        if line.startswith("X_API_KEY=") and len(line) > len("X_API_KEY="):
            return True
    return False

def main():
    drafts = sorted(glob.glob(os.path.join(DRAFTS_DIR, "*.txt")))
    if not drafts:
        # Empty queue is the normal state between draft runs — not an error.
        # Stay silent and exit 0 so the cron slot reads as healthy.
        return 0
    nxt = drafts[0]
    label = os.path.basename(nxt)

    # 250-char gate (X's counter weights punctuation ~7% heavier than python
    # len — 2026-08-24: 277 python chars counted as 296 by X and the Post
    # button stayed disabled). Quarantine anything at/over 250.
    try:
        text = open(nxt, encoding="utf-8").read().strip()
        if len(text) > 250:
            qdir = os.path.join(DRAFTS_DIR, "overlong")
            os.makedirs(qdir, exist_ok=True)
            qpath = os.path.join(qdir, label)
            os.replace(nxt, qpath)
            print(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] quarantined {label} "
                  f"({len(text)} chars > 280) -> drafts/overlong/")
            return 3
    except OSError as e:
        print(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] read error {label}: {e}", file=sys.stderr)
        return 1
    # The venv python (sys.executable) has playwright since 2026-08-27, and the
    # cron env sets PYTHONPATH to venv site-packages, which breaks bare python3
    # (3.12) importing venv cp311 packages. Use sys.executable (2026-08-28).
    py = sys.executable

    if has_api_keys():
        r = subprocess.run([py, os.path.join(BOT, "post.py"), "--label", label, "--post"],
                           cwd=BOT, capture_output=True, text=True)
    else:
        # browser mode: first verify a session exists, else stay silent (no alert spam)
        chk = subprocess.run([py, os.path.join(BOT, "browser_post.py"), "--check"],
                             cwd=BOT, capture_output=True, text=True)
        if "NOT LOGGED IN" in chk.stdout:
            return 0  # silent: no message, draft kept; weekly report will flag it
        if "SESSION OK" not in chk.stdout:
            return 0  # silent: session check failed, draft kept
        r = subprocess.run([py, os.path.join(BOT, "browser_post.py"), "--file", nxt],
                           cwd=BOT, capture_output=True, text=True)

    out = r.stdout.strip()
    err = r.stderr.strip()
    if out:
        print(out)
    if err:
        print("STDERR:", err, file=sys.stderr)

    if r.returncode == 0:
        os.remove(nxt)
        return 0
    if r.returncode == 2:
        # not logged in / queue empty — do NOT consume the draft
        return 2
    return r.returncode

if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `legolasbot_post_thread.py`  (47 lines, 1684 bytes)

```python
#!/usr/bin/env python3
"""Post the newest daily Lab Notes thread (browser mode). Silent when nothing
to post or no session — the cron stays quiet, the weekly report surfaces issues.

Thread files: C:/Users/Rajat/twitter-bot/drafts/threads/thread_*.txt
Posted file is moved to posted/ so it isn't reposted.

Usage: python3 legolasbot_post_thread.py
Exit codes: 0 = ok/silent, 3 = failed, 4 = file issue
"""
import os, sys, subprocess, glob, shutil, datetime

BOT = r"C:\Users\Rajat\twitter-bot"
THREADS_DIR = os.path.join(BOT, "drafts", "threads")
POSTED_DIR = os.path.join(THREADS_DIR, "posted")

def main():
    os.makedirs(POSTED_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(THREADS_DIR, "thread_*.txt")))
    if not files:
        return 0  # silent
    nxt = files[0]

    # WindowsApps python3 has playwright; the venv python (sys.executable) does not.
    py = "python3"
    # session check first — silent if no session
    chk = subprocess.run([py, os.path.join(BOT, "browser_thread.py"), "--check"],
                         cwd=BOT, capture_output=True, text=True)
    if "NOT LOGGED IN" in chk.stdout:
        return 0
    if "SESSION OK" not in chk.stdout:
        return 0

    r = subprocess.run([py, os.path.join(BOT, "browser_thread.py"), "--file", nxt],
                       cwd=BOT, capture_output=True, text=True)
    out = r.stdout.strip()
    if out:
        print(out)
    if r.stderr.strip():
        print("STDERR:", r.stderr.strip(), file=sys.stderr)
    if r.returncode == 0:
        shutil.move(nxt, os.path.join(POSTED_DIR, os.path.basename(nxt)))
        return 0
    return r.returncode

if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `news_monitor_cron.py`  (27 lines, 978 bytes)

```python
#!/usr/bin/env python3
"""Cron entry for the news-speed monitor (@first_sauce_lab).

Cron runs this file under the venv python (3.11). Since 2026-08-27 the venv
itself has playwright 1.62.0 + browsers, and the cron environment sets
PYTHONPATH to the venv site-packages — which breaks bare `python3` (3.12)
imports of venv cp311 packages (greenlet._greenlet missing). So the subprocess
MUST run under sys.executable (the venv python). Verified 2026-08-28: browser
launches fine under the venv python.

Exit code is passed through: 0 = silent or posted, non-zero = failure alert.
"""
import subprocess, sys

MONITOR = "C:/Users/Rajat/AppData/Local/hermes/scripts/news_monitor.py"

def main():
    r = subprocess.run([sys.executable, MONITOR], capture_output=True, text=True)
    out = r.stdout.strip()
    if out:
        print(out)
    if r.stderr.strip():
        print(r.stderr.strip(), file=sys.stderr)
    return r.returncode

if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `news_monitor.py`  (403 lines, 17883 bytes)

```python
#!/usr/bin/env python3
"""News-speed monitor for @first_sauce_lab.

Polls official AI announcement feeds; when a NEW item appears, posts:
  NEW: <title>

  <summary>

  <url>
via the logged-in browser (browser_post pipeline). Silent when nothing new.
Caps: 3 posts per run, 6 per day. Feeds (verified 2026-08-14): openai,
google_ai, deepmind, huggingface, techcrunch_ai, theverge_ai. Run this from
OUTSIDE twitter-bot so the bot's queue.py doesn't shadow stdlib queue for
playwright.

Summaries (added 2026-08-15, Rajat): every news post carries a brief
cohesive summary of the article. Source material is the feed's own
description when present (openai, google_ai, techcrunch_ai, theverge_ai),
otherwise the article page text is fetched (deepmind, huggingface). A
DeepSeek call (DEEPSEEK_API_KEY from the hermes .env) condenses it to <=170
chars, grounded strictly in the article text — no invented names, versions,
or numbers. If the key is missing or the call fails, the post degrades to
the plain title+link format; it never blocks the news post.

Fixed 2026-08-14:
- OpenAI's RSS returns the FULL archive (~2500 items back to 2015), not just
  recent news. A fixed 40-key seen window re-cycles old items as "new" and
  spams the whole archive. The seen set must GROW, not truncate.
- Items are marked seen ONLY after a confirmed post, so cap-limited leftovers
  stay unseen and are retried next run instead of being consumed silently.
- TEST mode never writes state (would consume items without posting).
- The daily cap persists (posted_today was mutated on a local copy before).
"""
import os, sys, json, re, time, random, datetime, urllib.request
import xml.etree.ElementTree as ET

BOT = r"C:\Users\Rajat\twitter-bot"
STATE_FILE = os.path.join(BOT, "logs", "news_monitor_state.json")
POSTS_LOG = os.path.join(BOT, "logs", "posts.log")
HERMES_ENV = r"C:\Users\Rajat\AppData\Local\hermes\.env"

# name -> feed url (RSS verified 2026-08-14: openai, google_ai, deepmind,
# huggingface, techcrunch_ai, theverge_ai all return HTTP 200)
FEEDS = {
    "openai": "https://openai.com/news/rss.xml",
    "google_ai": "https://blog.google/technology/ai/rss/",
    "deepmind": "https://deepmind.google/blog/rss.xml",
    "huggingface": "https://huggingface.co/blog/feed.xml",
    "techcrunch_ai": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "theverge_ai": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
}

MAX_PER_RUN = 1   # 2026-09-05 audit: was 3. Back-to-back posts cannibalize reach under the AI recommender. 1/run at :25/:55 = >=30 min spacing.
MAX_PER_DAY = 3   # 2026-09-05 audit: was 6. 3/day max + draft slots + card keeps total own-posts near the playbook ceiling.
SEEN_CAP = 5000  # defensive ceiling; feeds sit well under this
SUMMARY_MAX = 170  # hard cap for the summary inside a 280-char post
TAKE_MAX = 90      # cap for the one-line first-sauce take
ARTICLE_TEXT_CAP = 3500  # chars of article text sent to the summarizer

# ---- Quality gate (2026-08-18) ----
# The openai feed lists EVERY blog post (workflow tips, event recaps, essays).
# A leaker persona reposting "Sales workflows with ChatGPT Work" five times in
# a row reads as a corporate spam bot — it was the entire visible feed on
# 2026-08-18 and every one of those posts had zero engagement. Only items a
# frontier-AI audience would call NOTABLE get posted. Order matters: JUNK
# terms are checked FIRST (a "ChatGPT Work workflows" post contains "gpt" but
# is junk), then STRONG model terms, then generic NOTABLE terms.
STRONG_MODEL_TERMS = [
    "gpt-", "gpt 5", "gpt5", "claude", "gemini", "grok", "llama",
    "mistral", "deepseek", "qwen", "nemotron", "sora", "veo", "imagen",
    "whisper", "jepa", "opus", "sonnet", "haiku", "o3 ", "o4 ",
    "openai o", "gemma", "phi-", "olmo",
]
NOTABLE_TERMS = [
    "release", "launch", "unveil", "announc", "ships", "shipping",
    "now available", "public preview", "general availability", "beta",
    "introduc", "raises", "raised", "funding", "acqui", "partnership",
    "partners", "joins", "system card", "api", "pricing", "open source",
    "open-sourced", "benchmark", "frontier", "model", "dataset", "agent",
    "safety", "policy", "inference", "record", "first",
]
JUNK_TERMS = [
    "workflow", "tips", "how to", "learn", "guide", "best practice",
    "tutorial", "case study", "webinar", "workshop", "meetup", "podcast",
    "q&a", "faq", "recap", "changelog", "what's new", "welcome",
    "community", "office hours", "event", "conference", "essay",
]
# 2026-09-05 audit (Tier A niche purity): hobbyist/creative posts (e.g. HF's
# "Training a coding model to paint watercolours") are off-niche noise for a
# frontier-AI account. Every off-niche post burns reach with followers and
# non-followers alike. Checked BEFORE strong-model terms — a title can name a
# model and still be junk ("build a watercolour app with gpt-5.6" is a
# tutorial, not frontier news).
OFF_NICHE_TERMS = [
    "watercolou", "paint", "drawing", "sketch", "art project", "hobby",
    "recipe", "cook", "bake", "garden", "knit", "origami", "diy ",
    "photograph your", "meme", "halloween", "christmas", "valentine",
    "birthday", "wedding", "pet ", "dog ", "cat ", "game jam", "modding",
]

def is_notable(title):
    tl = title.lower()
    if any(t in tl for t in JUNK_TERMS):
        return False
    if any(t in tl for t in OFF_NICHE_TERMS):
        return False
    if any(t in tl for t in STRONG_MODEL_TERMS):
        return True
    return any(t in tl for t in NOTABLE_TERMS)

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"})
    data = urllib.request.urlopen(req, timeout=25).read()
    return data

def parse_items(data):
    """Return list of (title, link, description) from RSS/Atom."""
    out = []
    root = ET.fromstring(data)
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = (it.findtext("description") or "").strip()
        if title:
            out.append((title, link, desc))
    if not out:  # Atom fallback
        ns = "{http://www.w3.org/2005/Atom}"
        for e in root.findall(".//" + ns + "entry"):
            title = (e.findtext(ns + "title") or "").strip()
            link_el = e.find(ns + "link")
            link = (link_el.get("href") if link_el is not None else "") or ""
            desc = (e.findtext(ns + "summary") or "").strip()
            if title:
                out.append((title, link, desc))
    return out

def load_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {"seen": {}, "posted_today": []}

def save_state(st):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)

def day_key():
    return datetime.datetime.now().strftime("%Y-%m-%d")

def fetch_article_text(url):
    """Fetch a page and return the first chunk of visible prose. Empty on any failure."""
    try:
        html = fetch(url).decode("utf-8", "ignore")
    except Exception:
        return ""
    html = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:ARTICLE_TEXT_CAP]

def load_deepseek_key():
    """DEEPSEEK_API_KEY from the hermes .env (cron env won't have it)."""
    try:
        with open(HERMES_ENV, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return os.environ.get("DEEPSEEK_API_KEY")

def summarize(title, text, max_len=SUMMARY_MAX):
    """Condense article text to <=max_len summary plus a one-line first-sauce
    take. Returns (summary, take); either may be None on failure.

    Grounding rule (Rajat's verification gate): summary AND take may contain
    ONLY facts present in the article text passed in. No invented specifics.
    The take is voice/opinion/framing on those facts — never new numbers.
    """
    if not text or not text.strip():
        return None, None
    key = load_deepseek_key()
    if not key:
        return None, None
    prompt = (
        "Write a short summary of this AI news article for a Twitter post, "
        "plus one take line.\n"
        "Rules:\n"
        f"- Summary: at most {max_len} characters, factual, plain.\n"
        f"- Take: at most {TAKE_MAX} characters, ONE line of opinion or "
        "framing on the news, written by a smart slightly tired AI engineer "
        "on a phone: dry, specific, opinionated, first person. It must encode "
        "what this means for people who work with AI. A joke is welcome if it "
        "rides on the real observation.\n"
        "- Use ONLY facts from the article text below in BOTH. The take may "
        "frame or judge those facts but must never add names, versions, "
        "numbers, prices, dates, or claims that are not in the article.\n"
        "- If the article has no specific numbers or model names, do not invent any.\n"
        "- Plain text: no emojis, no hashtags, no markdown, no quotes, no em "
        "dashes, no catchphrases, no 'delve/unveils/showcases/landscape/"
        "testament/game-changer', no hype.\n"
        "- Separate the two parts with the literal line: <|TAKE|>\n"
        f"- No AI tells in either part. Active voice, plain verbs.\n"
        f"\nArticle title: {title}\n\nArticle text:\n{text}"
    )
    body = json.dumps({
        "model": os.environ.get("NEWS_SUMMARY_MODEL", "deepseek-chat"),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 600,
        "temperature": 0.7,
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    # The endpoint intermittently returns 200 with EMPTY content under quick
    # repeat calls (observed 2026-08-15) — retry with backoff, treat empty as
    # a failure. Grounded summary beats a dropped one.
    for attempt in range(4):
        try:
            r = urllib.request.urlopen(req, timeout=40)
            d = json.loads(r.read())
            out = d["choices"][0]["message"]["content"].strip()
            if not out:
                raise ValueError("empty content")
            parts = out.split("<|TAKE|>")
            summary = parts[0].strip()[:max_len] if parts[0].strip() else None
            take = parts[1].strip()[:TAKE_MAX] if len(parts) > 1 and parts[1].strip() else None
            if summary is None and take is None:
                raise ValueError("both empty")
            return summary, take
        except Exception as e:
            print(f"summary error: {type(e).__name__}: {e}", file=sys.stderr)
        if attempt < 3:
            time.sleep(3 + attempt * 2)
    return None, None

def summary_budget(title, link):
    """Max summary length that still leaves the FULL title + link under 280.

    Title is the headline — it must never be cut. The summary takes whatever
    room is left; if that is < MIN_SUMMARY (too small to be useful), drop it.
    """
    fixed = len("NEW: ") + 4 + len(link) + len(title)  # 4 = two \n\n
    return 280 - fixed

MIN_SUMMARY = 40

def fmt_post(title, link, summary=None, take=None):
    """NEW: <title>\n\n<summary>\n\n<take>\n\n<url> — hard 280 cap.

    Order: title, summary, take, link. The take (opinion/framing) is the
    value-add that separates a first-sauce post from a feed bot, so it is
    kept in preference to the summary when space is tight: summary is trimmed
    first, then take. Title is never truncated except by the hard cap.
    """
    prefix = "NEW: "
    if not link:
        text = f"{prefix}{title}"
        for part in (summary, take):
            if part:
                text += f"\n\n{part.strip()}"
        return text[:280]
    fixed = len(prefix) + 4 + len(link)
    t = title[:280 - fixed]
    s = (summary or "").strip()
    tk = (take or "").strip()
    room = 280 - fixed - len(t)
    if tk:
        tk = tk[:room]
        room -= len(tk) + 2  # \n\n before take
        if tk:
            if s:
                s = s[:room - 2].strip() if room > 2 else ""
                if s and len(s) >= MIN_SUMMARY:
                    return f"{prefix}{t}\n\n{s}\n\n{tk}\n\n{link}"
            return f"{prefix}{t}\n\n{tk}\n\n{link}"
    if s:
        s = s[:280 - fixed - len(t)].strip()
        if len(s) >= MIN_SUMMARY:
            return f"{prefix}{t}\n\n{s}\n\n{link}"
    return f"{prefix}{t}\n\n{link}"

def post_tweet(text):
    """Post via browser_post pipeline. Returns True on success."""
    sys.path.append(BOT)  # append, NOT insert: bot dir's queue.py must not shadow stdlib
    from playwright.sync_api import sync_playwright
    import browser_post
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            browser_post.PROFILE, headless=True,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not browser_post.check_session(page):
            ctx.close()
            return False
        rc = browser_post.post_text(page, text, image_path=None)
        ctx.close()
        return rc == 0

def main():
    today = day_key()
    st = load_state()
    seen = st.setdefault("seen", {})
    posted_today = [d for d in st.get("posted_today", []) if d.startswith(today)]
    if len(posted_today) >= MAX_PER_DAY:
        return 0  # cap reached, silent

    # Fetch every feed ONCE, before any state mutation.
    feed_items = {}
    for name, url in FEEDS.items():
        try:
            feed_items[name] = parse_items(fetch(url))
        except Exception as e:
            print(f"[{name}] feed error: {type(e).__name__}: {e}", file=sys.stderr)

    # Seed run: record the ENTIRE current feed as seen so the full-archive RSS
    # never spams old news. Nothing is posted on the seed run.
    first_run = not seen or all(not v for v in seen.values())
    if first_run:
        for name, items in feed_items.items():
            seen[name] = [title[:80] for title, link, desc in items]
        save_state(st)
        total = sum(len(v) for v in seen.values())
        print(f"news monitor: seeded {total} items, will post only new ones from now")
        return 0

    # New feeds added to FEEDS after a previous seed: seed them silently too
    # (mark the whole current archive as seen) so they don't spam old items.
    new_feed_seeded = False
    for name, items in feed_items.items():
        if name not in seen:
            seen[name] = [title[:80] for title, link, desc in items]
            print(f"news monitor: seeded new feed [{name}] ({len(items)} items), silent")
            new_feed_seeded = True
    if new_feed_seeded and not os.environ.get("NEWS_MONITOR_TEST") == "1":
        save_state(st)

    new_items = []
    for name, items in feed_items.items():
        known = set(seen.get(name, []))
        for title, link, desc in items:
            key = title[:80]
            if key in known:
                continue
            if not is_notable(title):
                # Junk is marked seen WITHOUT posting — a workflow tip must
                # never wait in the unseen pool and post later. These stay
                # forgotten forever; they are not lost content.
                seen.setdefault(name, []).append(key)
                continue
            new_items.append((name, title, link, desc))

    if not new_items:
        # Persist the junk-seen marks made above (they must not linger as
        # "new" forever). Test mode never writes state.
        if not os.environ.get("NEWS_MONITOR_TEST") == "1":
            save_state(st)
        return 0  # silent

    new_items = new_items[:MAX_PER_RUN]
    posted = 0
    is_test = os.environ.get("NEWS_MONITOR_TEST") == "1"
    for name, title, link, desc in new_items:
        # Source text: feed description if present, else fetch the article page.
        src = desc or ""
        if len(src) < 80 and link:
            src = fetch_article_text(link)
        budget = summary_budget(title, link) if link else SUMMARY_MAX
        summary, take = None, None
        if src and budget >= MIN_SUMMARY:
            summary, take = summarize(title, src, min(budget, SUMMARY_MAX))
        text = fmt_post(title, link, summary, take)
        if len(text) > 280:
            continue
        if is_test:
            print(f"TEST would post [{name}] {title[:100]}")
            print(text)
            posted += 1
            continue
        if post_tweet(text):
            posted += 1
            posted_today.append(today)
            st["posted_today"] = posted_today  # persist so the daily cap holds
            lst = seen.setdefault(name, [])
            lst.append(title[:80])
            seen[name] = lst[-SEEN_CAP:]
            ts = datetime.datetime.now().isoformat(timespec="seconds")
            with open(POSTS_LOG, "a", encoding="utf-8") as f:
                f.write(f"{ts} NEWS MONITOR POSTED [{name}] {title[:100]}\n")
            print(f"POSTED [{name}] {title[:100]}")
            save_state(st)  # persist after EVERY success so a crash can't cause a repost

    if is_test:
        return 0  # TEST mode must NEVER write state (would consume items unposted)
    save_state(st)
    print(f"news monitor: {posted} posted, {len(new_items)} new")
    return 0 if posted > 0 else 1

if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `model_monitor_cron.py`  (24 lines, 755 bytes)

```python
#!/usr/bin/env python3
"""Cron entry for the frontier model-drop monitor (@first_sauce_lab).

Cron runs this file under the venv python (3.11, no playwright), so the real
work happens in a subprocess under python3 (WindowsApps 3.12, has playwright).
NEVER use sys.executable here.

Exit code is passed through: 0 = silent or posted, non-zero = failure alert.
"""
import subprocess, sys

MONITOR = "C:/Users/Rajat/AppData/Local/hermes/scripts/model_monitor.py"

def main():
    r = subprocess.run(["python3", MONITOR], capture_output=True, text=True)
    out = r.stdout.strip()
    if out:
        print(out)
    if r.stderr.strip():
        print(r.stderr.strip(), file=sys.stderr)
    return r.returncode

if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `model_monitor.py`  (284 lines, 9937 bytes)

```python
#!/usr/bin/env python3
"""Frontier model-drop monitor for @first_sauce_lab.

Polls HuggingFace's trending API; when a NEW frontier model (Qwen, Llama,
Mistral, DeepSeek, Gemma, Phi, Nemotron, Grok, etc.) appears, posts its
verified specs:

  first sauce. <id> is trending on hf.

  <numParameters>b params. <downloads> downloads. <likes> likes.

All numbers come straight from the HF API (repoData) — no invented specs.
Variants (-GGUF/-AWQ/-LoRA/-SFT/-4bit...), tiny adapters (<1B), and non-model
repos are skipped. First run SEEDS the current trending list without posting
(so old models never spam). Silent when nothing new.

Run from OUTSIDE twitter-bot (queue.py shadow pitfall).
"""
import os, sys, json, re, time, random, datetime, urllib.request

BOT = r"C:\Users\Rajat\twitter-bot"
STATE_FILE = os.path.join(BOT, "logs", "model_monitor_state.json")
POSTS_LOG = os.path.join(BOT, "logs", "posts.log")

TRENDING_URL = "https://huggingface.co/api/trending"
OR_URL = "https://openrouter.ai/api/v1/models"  # stealth-model watch (Ox Alpha)
MAX_PER_RUN = 2
MAX_PER_DAY = 4
SEEN_CAP = 2000
MIN_PARAMS = 1_000_000_000  # 1B+ — skip tiny adapters
# Frontier open-model orgs (id must START with one of these)
AUTHOR_OK = [
    "Qwen", "meta-llama", "Meta-Llama", "mistralai", "deepseek-ai",
    "google", "microsoft", "nvidia", "x-ai", "allenai", "CohereForAI",
    "tiiuae", "ibm", "amazon",
]
# Variant suffixes that mean "not the base release"
VARIANT_MARKERS = [
    "-GGUF", "-AWQ", "-GPTQ", "-MLX", "-LoRA", "-SFT", "-DPO", "-ORPO",
    "-4bit", "-8bit", "-16bit", "-bnb", "-it", "-Instruct", "-vl", "-VL",
    "-Fun", "-Turbo", "-Pro", "-mini", "-small", "-base", "-Flash",
    "-FP8", "-INT8", "-1.5B", "-3B", "-7B", "-8B", "-14B", "-32B", "-70B", "-90B",
]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"})
    return urllib.request.urlopen(req, timeout=25).read()


def load_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {"seen": [], "posted_today": []}


def save_state(st):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)


def day_key():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def eligible(item):
    """Return (model_id, repo_data) if this trending item is a postable
    frontier base model, else None."""
    rd = item.get("repoData") or {}
    mid = rd.get("id") or ""
    if item.get("repoType") != "model":
        return None
    if not mid or "/" not in mid:
        return None
    author = mid.split("/")[0]
    if author not in AUTHOR_OK:
        return None
    if any(m in mid for m in VARIANT_MARKERS):
        return None
    if not rd.get("numParameters") or rd["numParameters"] < MIN_PARAMS:
        return None
    return mid, rd


def fmt_num(n):
    return f"{int(n):,}"


def or_snapshot():
    """OpenRouter stealth-org models -> {id: {name, created, context, pricing, modality}}."""
    data = json.loads(fetch(OR_URL))
    out = {}
    for m in data.get("data", []):
        mid = m.get("id") or ""
        if mid.startswith("stealth/"):
            pr = m.get("pricing") or {}
            c = m.get("context_length")
            out[mid] = {
                "name": m.get("name"),
                "created": m.get("created"),
                "context": c,
                "pricing": f"{pr.get('prompt') or 0}/{pr.get('completion') or 0}",
                "modality": (m.get("architecture") or {}).get("modality"),
            }
    return out


def or_diff_texts(snap, prev):
    """Human-readable verified diffs between two stealth snapshots."""
    texts = []
    for mid, cur in sorted(snap.items()):
        c = cur.get("context")
        ck = f"{int(c/1000):,}k" if isinstance(c, (int, float)) and c >= 1000 else (c or "?")
        mod = cur.get("modality") or "text"
        if mid not in prev:
            texts.append(
                f"new stealth model on openrouter: {mid}. {ck} context. "
                f"{mod} in. ${cur['pricing']} per 1k tokens."
            )
        else:
            old = prev[mid]
            bits = []
            if cur.get("context") != old.get("context"):
                bits.append(f"context {old.get('context')} -> {cur.get('context')}")
            if cur.get("pricing") != old.get("pricing"):
                bits.append(f"price ${old.get('pricing')} -> ${cur.get('pricing')} per 1k")
            if cur.get("modality") != old.get("modality"):
                bits.append(f"modality {old.get('modality')} -> {cur.get('modality')}")
            if bits:
                texts.append(f"ox alpha updated on openrouter: {', '.join(bits)}.")
    return texts


def or_watch(st, posted_today, is_test):
    """Stealth-model (Ox Alpha) change watch. Seeds silently on first run,
    posts verified diffs when the OpenRouter catalog changes."""
    try:
        snap = or_snapshot()
    except Exception as e:
        print(f"model monitor: openrouter fetch error {type(e).__name__}: {e}", file=sys.stderr)
        return 0
    prev = st.setdefault("or_stealth", {})
    texts = or_diff_texts(snap, prev) if prev else []
    st["or_stealth"] = snap
    if not prev:
        if not is_test:
            save_state(st)
            print("model monitor: stealth watch seeded")
        else:
            print("TEST would seed stealth watch")
        return 0
    if not texts:
        return 0
    posted = 0
    for text in texts[:MAX_PER_RUN]:
        if len(posted_today) >= MAX_PER_DAY:
            break
        if is_test:
            print(f"TEST would post: {text}")
            posted += 1
            continue
        if post_tweet(text[:280]):
            posted += 1
            posted_today.append(day_key())
            st["posted_today"] = posted_today
            ts = datetime.datetime.now().isoformat(timespec="seconds")
            with open(POSTS_LOG, "a", encoding="utf-8") as f:
                f.write(f"{ts} MODEL MONITOR POSTED (openrouter) {text[:60]}\n")
            print(f"POSTED (openrouter): {text[:80]}")
            save_state(st)
    if is_test:
        return 0
    save_state(st)
    return posted


def fmt_post(mid, rd):
    params_b = rd["numParameters"] / 1e9
    params_s = f"{params_b:.1f}".rstrip("0").rstrip(".") if params_b < 100 else f"{int(params_b)}"
    pipe = rd.get("pipeline_tag") or "model"
    text = (
        f"first sauce. {mid} is trending on hf.\n\n"
        f"{params_s}b params. {fmt_num(rd.get('downloads') or 0)} downloads. "
        f"{fmt_num(rd.get('likes') or 0)} likes. {pipe}."
    )
    return text[:280]


def post_tweet(text):
    sys.path.append(BOT)  # append, never insert (queue.py shadow)
    from playwright.sync_api import sync_playwright
    import browser_post
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            browser_post.PROFILE, headless=True,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not browser_post.check_session(page):
            ctx.close()
            return False
        rc = browser_post.post_text(page, text, image_path=None)
        ctx.close()
        return rc == 0


def main():
    today = day_key()
    is_test = os.environ.get("MODEL_MONITOR_TEST") == "1"
    st = load_state()
    seen = st.setdefault("seen", [])
    posted_today = [d for d in st.get("posted_today", []) if d.startswith(today)]
    if len(posted_today) >= MAX_PER_DAY:
        return 0

    try:
        data = json.loads(fetch(TRENDING_URL))
    except Exception as e:
        print(f"model monitor: fetch error {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    items = data.get("recentlyTrending", []) + data.get("todayTrending", [])
    candidates = []
    for it in items:
        got = eligible(it)
        if got:
            mid, rd = got
            if mid not in seen:
                candidates.append((mid, rd))

    # First run: record the ENTIRE current eligible list as seen so existing
    # models never spam. Nothing is posted on the seed run.
    if not seen:
        for it in items:
            got = eligible(it)
            if got:
                mid, _ = got
                if mid not in seen:
                    seen.append(mid)
        seen[:] = seen[-SEEN_CAP:]
        if not is_test:
            save_state(st)
            print(f"model monitor: seeded {len(seen)} models, will post only new drops")
        else:
            print(f"TEST would seed {len(seen)} models")
        return 0

    if not candidates:
        or_watch(st, posted_today, is_test)
        return 0  # silent (or stealth-watch posted)

    candidates = candidates[:MAX_PER_RUN]
    posted = 0
    for mid, rd in candidates:
        text = fmt_post(mid, rd)
        if is_test:
            print(f"TEST would post: {mid}")
            print(text)
            posted += 1
            continue
        if post_tweet(text):
            posted += 1
            posted_today.append(today)
            st["posted_today"] = posted_today
            seen.append(mid)
            seen[:] = seen[-SEEN_CAP:]
            ts = datetime.datetime.now().isoformat(timespec="seconds")
            with open(POSTS_LOG, "a", encoding="utf-8") as f:
                f.write(f"{ts} MODEL MONITOR POSTED {mid}\n")
            print(f"POSTED {mid}")
            save_state(st)
    if is_test:
        return 0  # test never writes state
    # stealth-model watch (Ox Alpha): post catalog diffs under the same daily cap
    posted += or_watch(st, posted_today, is_test)
    save_state(st)
    print(f"model monitor: {posted} posted")
    return 0 if posted > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `follow_spread_cron.py`  (72 lines, 2874 bytes)

```python
#!/usr/bin/env python3
"""Pool-driven follow spread for @first_sauce_lab (rate-limit safe).

Rajat's growth goal (2026-08-20): 5-10 new followers/day. The lever is
following follow-back-friendly AI accounts from a verified pool
(follow_pool.json), NOT big-name accounts (they never follow back).

- Each run takes the next MAX_FOLLOWS handles from the pool (not yet done)
  and follows them via follow_accounts.py (paced, header-scoped buttons).
- Handles are marked done after the attempt (followed OR failed) so a dead
  handle or rate-limited click is never retried forever.
- Prints ONLY when at least one new follow landed (Rajat's silent-until-hit
  rule for no_agent crons). Empty stdout = silent cron.
- Runs under the hermes venv python (which has had playwright since
  2026-08-27); the cron env's PYTHONPATH breaks bare python3 importing venv
  cp311 packages, so the subprocess runs under sys.executable.
"""
import json, os, subprocess, sys, datetime

BOT = r"C:\Users\Rajat\twitter-bot"
FOLLOW = os.path.join(BOT, "follow_accounts.py")
POOL = os.path.join(BOT, "follow_pool.json")
MAX_FOLLOWS = 25  # per session; X rate-limits ~30 follows per 15 min on young accounts

def load_pool():
    if not os.path.exists(POOL):
        return {"handles": [], "done": {}}
    try:
        return json.load(open(POOL, encoding="utf-8"))
    except Exception:
        return {"handles": [], "done": {}}

def save_pool(pool):
    with open(POOL, "w", encoding="utf-8") as f:
        json.dump(pool, f, indent=1)

def main():
    pool = load_pool()
    handles = [h for h in pool.get("handles", []) if h not in pool.get("done", {})]
    if not handles:
        return 0  # pool exhausted or empty — silent (replenish via discovery)
    batch = handles[:MAX_FOLLOWS]
    r = subprocess.run(
        [sys.executable, FOLLOW] + batch,
        capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr

    done = pool.setdefault("done", {})
    today = datetime.date.today().isoformat()
    followed = 0
    if "SUMMARY followed=" in out:
        for line in out.splitlines():
            if "SUMMARY followed=" in line:
                try:
                    followed = int(line.split("followed=")[1].split()[0])
                except Exception:
                    followed = 0
        # mark every attempted handle done (followed, failed, or already-following)
        for h in batch:
            done[h] = today
        save_pool(pool)
        remaining = len([h for h in pool.get("handles", []) if h not in done])
        if followed > 0:
            print(f"followed {followed} (pool remaining: {remaining})")
        # silent when 0 — rate limited or all already following
        return 0
    # no summary at all = session dead or crash
    print(f"FOLLOW SPREAD FAILED: {out[-200:]}")
    return 1

if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `replenish_pool_cron.py`  (43 lines, 1825 bytes)

```python
#!/usr/bin/env python3
"""Weekly follow-pool replenishment for @first_sauce_lab (silent unless grown).

Chains discovery (X search People tab -> raw candidate handles) + verification
(profile visits -> follower counts, 500-150K band, own-tweets check) and merges
into twitter-bot/follow_pool.json.

Run under sys.executable (the venv python has playwright since 2026-08-27; the
cron env's PYTHONPATH breaks bare python3). Print ONLY when the pool grew
(no_agent cron silent-until-hit). Runtime ~10-12 min for 45 verified profiles.
"""
import json, os, subprocess, sys, datetime

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
DISCOVER = os.path.join(SCRIPTS, "twitter-growth", "discover_ai_accounts.py")
VERIFY = os.path.join(SCRIPTS, "twitter-growth", "verify_pool.py")
POOL = r"C:\Users\Rajat\twitter-bot\follow_pool.json"

def main():
    before = 0
    if os.path.exists(POOL):
        try:
            before = len(json.load(open(POOL, encoding="utf-8")).get("handles", []))
        except Exception:
            before = 0
    r1 = subprocess.run([sys.executable, DISCOVER], capture_output=True, text=True, timeout=900)
    if r1.returncode != 0 or "CANDIDATES" not in r1.stdout:
        print(f"REPLENISH FAILED (discover): {(r1.stdout + r1.stderr)[-200:]}")
        return 1
    r2 = subprocess.run([sys.executable, VERIFY], capture_output=True, text=True, timeout=1200)
    if r2.returncode != 0 or "POOL:" not in r2.stdout:
        print(f"REPLENISH FAILED (verify): {(r2.stdout + r2.stderr)[-200:]}")
        return 1
    try:
        after = len(json.load(open(POOL, encoding="utf-8")).get("handles", []))
    except Exception:
        after = before
    if after > before:
        print(f"follow pool replenished: {before} -> {after} handles")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `followers_check_cron.py`  (26 lines, 918 bytes)

```python
#!/usr/bin/env python3
"""Cron entry for the daily follower check (@first_sauce_lab).

Cron runs this under the venv python (3.11), which has had playwright since
2026-08-27; the cron environment sets PYTHONPATH to the venv site-packages,
which breaks bare `python3` (3.12) importing venv cp311 packages
(greenlet._greenlet missing). The subprocess MUST run under sys.executable.
Same pattern as news_monitor_cron.py. Verified 2026-08-29.

Exit code passed through: 0 = ok (silent unless growth), 1 = check failed.
"""
import subprocess, sys

CHECKER = "C:/Users/Rajat/AppData/Local/hermes/scripts/followers_check.py"

def main():
    r = subprocess.run([sys.executable, CHECKER], capture_output=True, text=True)
    out = r.stdout.strip()
    if out:
        print(out)
    if r.stderr.strip():
        print(r.stderr.strip(), file=sys.stderr)
    return r.returncode

if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `followers_check.py`  (71 lines, 2494 bytes)

```python
#!/usr/bin/env python3
"""Daily follower scoreboard for @first_sauce_lab (growth goal mode).

Rajat's goal (2026-08-20): 5-10 new followers/day. This is now a scoreboard,
not a watchdog: it ALWAYS reports the daily delta vs the previous day and the
7-day pace, so missing the goal is visible, not silent.
"""
import os, re, csv, datetime, sys

from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")
CSV = os.path.join(BOT, "logs", "followers.csv")
USER = "first_sauce_lab"

def get_followers():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True, viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"https://x.com/{USER}", wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(4000)
        txt = page.locator('[data-testid="primaryColumn"]').first.inner_text()
        ctx.close()
    m = re.search(r"([\d,]+)\s*Followers", txt)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))

def read_rows():
    if not os.path.exists(CSV):
        return []
    with open(CSV, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))

def main():
    count = get_followers()
    if count is None:
        print("FOLLOWER CHECK FAILED")  # visible alert, not silent
        return 1
    now = datetime.datetime.now().isoformat(timespec="minutes")
    rows = read_rows()
    prev = None
    if len(rows) >= 2:
        try:
            prev = int(rows[-1][1])
        except Exception:
            prev = None
    with open(CSV, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([now, count])

    if prev is None:
        print(f"@first_sauce_lab: {count} followers (baseline)")
        return 0
    delta = count - prev
    # 7-day pace: look back up to 8 rows (today + 7 prior days)
    pace = None
    if len(rows) >= 8:
        try:
            pace = (count - int(rows[-8][1])) / 7.0
        except Exception:
            pace = None
    pace_txt = f", 7-day avg {pace:+.1f}/day" if pace is not None else ""
    goal_txt = "ON GOAL" if delta >= 5 else f"SHORT of 5-10/day goal"
    print(f"@first_sauce_lab: {count} followers ({delta:+d} today, {goal_txt}{pace_txt})")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `impressions_cron.py`  (93 lines, 3556 bytes)

```python
#!/usr/bin/env python3
"""Daily impressions tracker for @first_sauce_lab.

Runs profile_audit.py under sys.executable (the venv python, which has had
playwright since 2026-08-27; the cron env's PYTHONPATH breaks bare python3
importing venv cp311 packages), which appends a row to
twitter-bot/logs/impressions.csv. The impressions field is
X's own "X impressions on your posts in the last 7 days" banner when it
renders (tag BANNER); since 2026-08-21 X has been rotating that UI variant
off, so the audit falls back to summing the official per-post Impressions
figures from each visible post's /analytics page (tag SUM).

Prints ONLY when the impressions figure grew since the previous recorded
check using the same source (Rajat's silent-until-hit rule). Empty stdout =
nothing to report.

Retries when the audit exits 2 (NOT LOGGED IN): other jobs share the same
persistent browser profile and the loser of the profile lock false-negatives
the login check. 60s between retries heals it. If the audit succeeds but no
impressions figure could be read from either source, that is a broken
sensor, not a quiet day — it alerts instead of staying silent.
"""
import subprocess, sys, csv, re, os, time

BOT = r"C:\Users\Rajat\twitter-bot"
AUDIT = os.path.join(BOT, "profile_audit.py")
CSV = os.path.join(BOT, "logs", "impressions.csv")
ATTEMPTS = 3
RETRY_GAP = 60
AUDIT_TIMEOUT = 420  # SUM fallback visits up to 8 /analytics pages


def num(s):
    if not s:
        return 0
    s = s.strip().upper()
    m = re.match(r"^([\d.,]+)\s*([KM]?)$", s)
    if not m:
        return 0
    v = float(m.group(1).replace(",", ""))
    return int(v * {"": 1, "K": 1000, "M": 1000000}[m.group(2)])


def run_audit():
    last = None
    for i in range(ATTEMPTS):
        try:
            r = subprocess.run([sys.executable, AUDIT], capture_output=True,
                               text=True, timeout=AUDIT_TIMEOUT)
            last = (r.returncode, r.stdout.strip(), r.stderr.strip())
        except subprocess.TimeoutExpired:
            last = ("timeout", "", f"audit exceeded {AUDIT_TIMEOUT}s")
            break
        if last[0] == 2 and i < ATTEMPTS - 1:
            time.sleep(RETRY_GAP)
            continue
        break
    return last


def main():
    rc, out, err = run_audit()
    if rc != 0:
        tail = (out or err)[-200:]
        print(f"IMPRESSIONS AUDIT FAILED (exit {rc}): {tail}")
        return 1
    if not os.path.exists(CSV):
        print("IMPRESSIONS AUDIT: no CSV written")
        return 1
    rows = list(csv.reader(open(CSV, encoding="utf-8")))
    if len(rows) < 2:
        return 0  # first row only = baseline, silent
    # columns: ts, followers, following, impressions_7d, total_views_sample, n, tag
    prev = num(rows[-2][3]) if len(rows[-2]) > 3 else 0
    cur_raw = rows[-1][3] if len(rows[-1]) > 3 else ""
    cur = num(cur_raw)
    if not cur_raw:
        print("IMPRESSIONS SENSOR BLIND: no impressions figure found (banner gone AND per-post analytics failed)")
        return 1
    prev_tag = rows[-2][6] if len(rows[-2]) > 6 else ""
    cur_tag = rows[-1][6] if len(rows[-1]) > 6 else ""
    if prev_tag and cur_tag and prev_tag != cur_tag:
        return 0  # different sources, not comparable — silent baseline
    if cur > prev:
        if cur_tag == "BANNER":
            print(f"@first_sauce_lab 7-day impressions: {rows[-2][3]} -> {cur_raw}")
        else:
            print(f"@first_sauce_lab impressions (top-post sum): {rows[-2][3]} -> {cur_raw}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

## FULL SOURCE: `twitter-growth/discover_ai_accounts.py`  (107 lines, 4688 bytes)

```python
#!/usr/bin/env python3
"""Discover small/mid AI accounts via X search People tab (read-only).

Collects handles + follower counts for AI-related queries, filters to
follow-back-friendly size (1K-100K followers), saves candidates JSON.
"""
import json, sys, time, random, re
from urllib.parse import quote
from playwright.sync_api import sync_playwright

PROFILE = r"C:\Users\Rajat\twitter-bot\browser-profile"
OUT = r"C:\Users\Rajat\AppData\Local\Temp\fsl_discovery.json"
QUERIES = ["AI engineer", "machine learning", "LLM", "AI tools", "prompt engineering",
           "deep learning", "AI developer", "data science", "AI startup", "open source AI",
           "ML engineer", "AI researcher", "AI news", "generative AI", "AI agents",
           "LangChain", "PyTorch", "HuggingFace", "AI products", "LLM engineer", "AI jobs",
           "AI infrastructure", "RAG", "fine-tuning", "AI coding", "computer vision",
           "MLOps", "vector database", "AI hardware", "NLP", "AI security", "AI robotics"]
MAX_PER_QUERY = 20

def human_delay(a=1.5, b=3.0):
    time.sleep(random.uniform(a, b))

def parse_count(s):
    s = s.replace(",", "").replace(".", "")
    m = re.search(r"([\d.]+)([KM])?", s.replace(" ", ""))
    if not m:
        return None
    n = float(m.group(1))
    if m.group(2) == "K":
        n *= 1000
    elif m.group(2) == "M":
        n *= 1_000_000
    return int(n)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, headless=True, viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60_000)
    human_delay()
    if "login" in page.url:
        ctx.close()
        print(json.dumps({"error": "not logged in"}))
        sys.exit(2)

    found = {}
    # Pass 1: harvest X's own "Who to follow" sidebar suggestions — cheap page
    # loads (home + AI profiles), no profile visits needed.
    suggest_pages = ["home"] + ["minchoi", "XFreeze", "shaxzadaly", "aiseceng", "ML_Chem", "machinelearnTec",
                                "matchaman11", "TheMLHub", "TmlrOrg", "Roger_Achkar", "sachiefsci", "ML_Times",
                                "molecularML", "Iamtugar", "Abrxx90", "Abdulrafiu_dev"]
    for sp in suggest_pages:
        url = "https://x.com/home" if sp == "home" else f"https://x.com/{sp}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            human_delay(2, 4)
            aside = page.locator('aside')
            if aside.count():
                for link in aside.first.locator('a[href^="/"]').all():
                    href = link.get_attribute("href") or ""
                    h = href.strip("/")
                    if re.match(r"^[A-Za-z0-9_]{3,20}$", h) and h not in found and h != "first_sauce_lab":
                        found[h] = {"handle": h, "query": "who-to-follow"}
        except Exception:
            continue
        human_delay(1, 2)

    # Pass 2: search People tab per query
    for q in QUERIES:
        url = f"https://x.com/search?q={quote(q)}&f=user"
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        human_delay(2, 4)
        try:
            page.wait_for_selector('a[href^="/"][role="link"]', timeout=15_000)
        except Exception:
            continue
        for _ in range(4):
            page.mouse.wheel(0, 2500)
            time.sleep(1.5)
        # user cells: links to profiles within search results
        links = page.locator('a[href^="/"]')
        n = links.count()
        for i in range(min(n, 150)):
            try:
                href = links.nth(i).get_attribute("href") or ""
                if "/status/" in href or "/search" in href or "/settings" in href:
                    continue
                h = href.strip("/")
                if not h or len(h) > 20 or h in ("home", "explore", "notifications", "messages",
                                                 "bookmarks", "communities", "premium", "verified",
                                                 "settings", "i", "intent", "compose", "tos", "privacy"):
                    continue
                if not re.match(r"^[A-Za-z0-9_]{3,20}$", h):
                    continue
                if h in found:
                    continue
                found[h] = {"handle": h, "query": q}
            except Exception:
                continue
        human_delay(2, 4)
    ctx.close()

cands = list(found.values())
json.dump(cands, open(OUT, "w", encoding="utf-8"), indent=1)
print(f"CANDIDATES: {len(cands)} -> {OUT}")
```

## FULL SOURCE: `twitter-growth/verify_pool.py`  (90 lines, 3432 bytes)

```python
#!/usr/bin/env python3
"""Verify discovery candidates: visit each profile, read follower count,
check own tweets visible, keep the 500-150K band, write follow_pool.json.

Pool format: {"handles": [h, ...], "done": {h: date}} — consumed by
follow_spread_cron.py (up to 25/session).
"""
import json, sys, time, random, re, os

from playwright.sync_api import sync_playwright

PROFILE = r"C:\Users\Rajat\twitter-bot\browser-profile"
RAW = r"C:\Users\Rajat\AppData\Local\Temp\fsl_discovery.json"
POOL = r"C:\Users\Rajat\twitter-bot\follow_pool.json"
MIN_F, MAX_F = 500, 50_000  # 50K cap: follow-back odds collapse above this (Rajat, 2026-08-22)
MAX_VISITS = 60

def human_delay(a=1.5, b=3.0):
    time.sleep(random.uniform(a, b))

def main():
    raw = json.load(open(RAW, encoding="utf-8"))
    # dedupe, keep query diversity: cap 8 per query
    by_q = {}
    order = []
    for c in raw:
        q = c.get("query", "?")
        if q not in by_q:
            by_q[q] = []
            order.append(q)
        if len(by_q[q]) < 8:
            by_q[q].append(c["handle"])
    handles = []
    for q in order:
        handles += by_q[q]
    handles = handles[:MAX_VISITS]

    pool = {"handles": [], "done": {}}
    if os.path.exists(POOL):
        try:
            pool = json.load(open(POOL, encoding="utf-8"))
        except Exception:
            pass

    kept = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True, viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60_000)
        human_delay()
        if "login" in page.url:
            ctx.close()
            print(json.dumps({"error": "not logged in"}))
            sys.exit(2)
        for h in handles:
            try:
                page.goto(f"https://x.com/{h}", wait_until="domcontentloaded", timeout=60_000)
                human_delay(2, 4)
                try:
                    page.wait_for_selector('article[data-testid="tweet"]', timeout=12_000)
                    has_tweets = True
                except Exception:
                    has_tweets = False
                txt = page.locator('[data-testid="primaryColumn"]').first.inner_text()
                m = re.search(r"([\d,]+)\s*Followers", txt)
                f = int(m.group(1).replace(",", "")) if m else None
                if f is None:
                    print(f"{h}: NO COUNT (protected/dead)")
                elif not (MIN_F <= f <= MAX_F):
                    print(f"{h}: OUT OF BAND ({f})")
                elif not has_tweets:
                    print(f"{h}: NO OWN TWEETS ({f})")
                else:
                    kept.append({"handle": h, "followers": f})
                    print(f"{h}: KEEP ({f})")
            except Exception as e:
                print(f"{h}: ERR {type(e).__name__}")
            human_delay(1, 2)
        ctx.close()

    kept.sort(key=lambda c: -c["followers"])
    existing = [h for h in pool.get("handles", []) if h not in {c["handle"] for c in kept}]
    pool["handles"] = existing + [c["handle"] for c in kept]
    json.dump(pool, open(POOL, "w", encoding="utf-8"), indent=1)
    print(f"POOL: {len(pool['handles'])} handles -> {POOL}")

if __name__ == "__main__":
    main()
```

## FULL SOURCE: `gemini_image_gen.py`  (110 lines, 3540 bytes)

```python
#!/usr/bin/env python3
"""Generate an image via Google Gemini 2.5 Flash Image model.

Usage:
    python gemini_image_gen.py "prompt describing the image"
    python gemini_image_gen.py "prompt" --model "gemini-3.1-flash-image"

Reads GOOGLE_API_KEY from the Hermes .env file.
Saves output to image_cache/ and prints the absolute path on success.
"""

import argparse
import os
import sys
from datetime import datetime

# ── resolve paths ──────────────────────────────────────────────────
HERMES_HOME = os.environ.get(
    "HERMES_HOME",
    os.path.expanduser("~/AppData/Local/hermes"),
)
ENV_PATH = os.path.join(HERMES_HOME, ".env")
OUTPUT_DIR = os.path.join(HERMES_HOME, "image_cache")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _load_env():
    """Load GOOGLE_API_KEY from the Hermes .env file."""
    if not os.path.isfile(ENV_PATH):
        print(f"ERROR: .env not found at {ENV_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)


def generate_image(prompt: str, model: str = "gemini-2.5-flash-image") -> str:
    """Call Gemini image-generation model, save to disk, return file path."""
    from google import genai
    from google.genai import types

    _load_env()
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["Text", "Image"]
        ),
    )

    if not response.candidates:
        print("ERROR: no candidates returned", file=sys.stderr)
        sys.exit(1)

    # Extract the image part
    image_bytes = None
    text_response = ""
    for part in response.candidates[0].content.parts:
        if hasattr(part, "inline_data") and part.inline_data:
            image_bytes = part.inline_data.data
        elif hasattr(part, "text") and part.text:
            text_response += part.text + "\n"

    if not image_bytes:
        print("ERROR: no image data in response", file=sys.stderr)
        print(f"Text received: {text_response.strip()}", file=sys.stderr)
        sys.exit(1)

    # Save with a timestamped name
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_prompt = "".join(c if c.isalnum() or c in " _-" else "_" for c in prompt)[:40]
    filename = f"gemini_{ts}_{safe_prompt}.png"
    out_path = os.path.join(OUTPUT_DIR, filename)
    with open(out_path, "wb") as f:
        f.write(image_bytes)

    # Print the absolute path so the caller can use MEDIA:path
    abs_path = os.path.abspath(out_path)
    print(abs_path)
    if text_response.strip():
        print(f"TEXT: {text_response.strip()}", file=sys.stderr)

    return abs_path


def main():
    parser = argparse.ArgumentParser(description="Generate an image via Gemini")
    parser.add_argument("prompt", nargs="+", help="Image description")
    parser.add_argument("--model", default="gemini-2.5-flash-image",
                        help="Gemini image model (default: gemini-2.5-flash-image)")
    args = parser.parse_args()

    prompt = " ".join(args.prompt)
    path = generate_image(prompt, model=args.model)
    print(path)


if __name__ == "__main__":
    main()
```

## FULL SOURCE: `find_tweet.py`  (47 lines, 1693 bytes)

```python
#!/usr/bin/env python3
"""Find the status URL of @first_sauce_lab's tweet matching a text marker."""
import sys, time
sys.path.append(r"C:\Users\Rajat\twitter-bot")
from playwright.sync_api import sync_playwright
import browser_post

MARKER = "Anthropic shares more details about how Claude"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        browser_post.PROFILE, headless=True,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    if not browser_post.check_session(page):
        print("NOT LOGGED IN")
        ctx.close()
        sys.exit(2)
    page.goto("https://x.com/first_sauce_lab", wait_until="domcontentloaded", timeout=60000)
    time.sleep(4)
    try:
        page.wait_for_selector('article[data-testid="tweet"]', timeout=20000)
    except Exception:
        print("NO TWEETS")
        ctx.close()
        sys.exit(1)
    arts = page.locator('article[data-testid="tweet"]')
    n = arts.count()
    found = None
    for i in range(n):
        art = arts.nth(i)
        try:
            txt = art.locator('[data-testid="tweetText"]').first.inner_text()
        except Exception:
            continue
        if MARKER in txt:
            link = art.locator('a[href*="/status/"]').first
            href = link.get_attribute("href") if link.count() else ""
            found = href.split("/status/")[1].split("?")[0]
            print(f"FOUND {found} | {txt[:80]}")
            break
    if not found:
        print("MARKER NOT FOUND — scanning visible tweets failed")
        ctx.close()
        sys.exit(1)
    ctx.close()
```

## FULL SOURCE: `fix_watermark_repost.py`  (159 lines, 6295 bytes)

```python
#!/usr/bin/env python3
"""One-shot: delete the mangled Anthropic watermark tweet, repost clean.

Single persistent context so the whole fix happens inside one session window
(X has been killing the profile session minutes after login, 2026-08-15).
Sequence: session check -> find mangled tweet by marker -> delete -> verify ->
post corrected text -> report new id. Prints one line per step; exit 0 only
if delete verified AND repost confirmed.
"""
import os, sys, time, random, datetime

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")
sys.path.append(BOT)  # append, NOT insert: bot queue.py must not shadow stdlib

from playwright.sync_api import sync_playwright
import browser_post

MANGLE_MARKER = "NEW: Anthropic shares"          # matches ONLY the mangled tweet right now
CORRECTED = (
    "NEW: Anthropic shares more details about how Claude\u2019s new watermarks will work\n\n"
    "Anthropic details how Claude's new watermarks will work, including their limits and effects\n\n"
    "https://techcrunch.com/2026/08/15/anthropic-shares-more-details-about-how-claudes-new-watermarks-will-work/"
)

def log(line):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {line}", flush=True)

def human_delay(a=0.8, b=1.8):
    time.sleep(random.uniform(a, b))

def find_mangled(page):
    """Return the status id of the tweet whose text starts with MANGLE_MARKER."""
    page.goto("https://x.com/first_sauce_lab", wait_until="domcontentloaded", timeout=60_000)
    human_delay(2.5, 4)
    try:
        page.wait_for_selector('article[data-testid="tweet"]', timeout=20_000)
    except Exception:
        return None
    human_delay(1, 2)
    arts = page.locator('article[data-testid="tweet"]')
    n = min(arts.count(), 12)
    for i in range(n):
        art = arts.nth(i)
        try:
            txt = art.locator('[data-testid="tweetText"]').first.inner_text()
        except Exception:
            continue
        if txt.startswith(MANGLE_MARKER):
            link = art.locator('a[href*="/status/"]').first
            href = link.get_attribute("href") if link.count() else ""
            if "/status/" in href:
                return href.split("/status/")[1].split("?")[0]
    return None

def delete_one(page, tid):
    url = f"https://x.com/first_sauce_lab/status/{tid}"
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    human_delay(2, 4)
    try:
        page.wait_for_selector('[data-testid="caret"]', timeout=20_000)
    except Exception:
        log(f"delete {tid}: caret not found")
        return False
    page.locator('[data-testid="caret"]').first.click()
    human_delay(1, 2)
    try:
        page.wait_for_selector('text=Delete', timeout=10_000)
        page.locator('text=Delete').first.click()
    except Exception:
        log(f"delete {tid}: Delete menu item not found")
        return False
    human_delay(1, 2)
    try:
        page.wait_for_selector('[data-testid="confirmationSheetConfirm"]', timeout=10_000)
        page.locator('[data-testid="confirmationSheetConfirm"]').click()
    except Exception:
        log(f"delete {tid}: confirm button not found")
        return False
    human_delay(2, 3)
    # verify: the caret should be gone from the (now-deleted) status page
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    human_delay(2, 3)
    gone = page.locator('[data-testid="caret"]').count() == 0
    log(f"delete {tid}: submitted, post gone = {gone}")
    return gone

def read_new_id(page):
    """After posting, read the newest profile tweet matching the corrected title."""
    page.goto("https://x.com/first_sauce_lab", wait_until="domcontentloaded", timeout=60_000)
    human_delay(2.5, 4)
    try:
        page.wait_for_selector('article[data-testid="tweet"]', timeout=20_000)
    except Exception:
        return None
    arts = page.locator('article[data-testid="tweet"]')
    n = min(arts.count(), 6)
    for i in range(n):
        art = arts.nth(i)
        try:
            txt = art.locator('[data-testid="tweetText"]').first.inner_text()
        except Exception:
            continue
        if txt.startswith("NEW: Anthropic shares") and "Anthropic details" in txt:
            link = art.locator('a[href*="/status/"]').first
            href = link.get_attribute("href") if link.count() else ""
            if "/status/" in href:
                return href.split("/status/")[1].split("?")[0]
    return None

def main():
    if len(CORRECTED) > 280:
        log(f"ERROR: corrected text {len(CORRECTED)} chars")
        return 4
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=True, viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not browser_post.check_session(page):
            log("NOT LOGGED IN — run browser_login.py")
            ctx.close()
            return 2
        tid = find_mangled(page)
        if not tid:
            log("MANGLED TWEET NOT FOUND")
            ctx.close()
            return 3
        log(f"found mangled tweet {tid}")
        if not delete_one(page, tid):
            log("DELETE NOT VERIFIED — aborting repost")
            ctx.close()
            return 3
        log(f"deleted {tid}, posting corrected text")
        rc = browser_post.post_text(page, CORRECTED, image_path=None)
        ctx.close()
    if rc != 0:
        log(f"REPOST FAILED rc={rc}")
        return 3
    log("REPOSTED (toast confirmed)")
    # read the new id in a second quick context
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                PROFILE, headless=True, viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if browser_post.check_session(page):
                new_id = read_new_id(page)
                if new_id:
                    log(f"NEW TWEET ID {new_id}")
            ctx.close()
    except Exception as e:
        log(f"id read skipped: {e}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

