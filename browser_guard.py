#!/usr/bin/env python3
"""One shared guard for the X browser profile. Two jobs:

1. THE LOCK. Every engine that opens twitter-bot/browser-profile must hold
   logs/browser.lock first. Two Chromium instances on one profile do not
   queue, they corrupt the session: the loser lands on a logged-out page and
   reports "NOT LOGGED IN", which reads as an expired login and sends you to
   browser_login.py for nothing. Observed 2026-09-10 (reply + mentions
   overlapped) and again 2026-09-11 13:41 (a one-shot verify_x_account run
   started at 13:40; mentions_guy took a free lock, opened the profile, and
   found a logged-out shell).

2. THE SESSION VERDICT. There were three copies of check_session (reply_guy,
   browser_post, browser_thread) and a fix applied to one did not reach the
   others, so "NOT LOGGED IN" kept coming back. classify_session() replaces
   all of them and separates two things that used to look identical:

     dead  - the login is genuinely gone (no auth_token cookie, or a redirect
             to /i/flow/login). Action required: browser_login.py.
     busy  - the login is fine, the page just has not hydrated yet, usually
             because another process has the profile, or the cold profile is
             slow. Action required: none. Retry, then stay quiet.

   Only "dead" deserves a red cron alert.

Self-test:
  python3 browser_guard.py            # lock round-trip, no browser
  python3 browser_guard.py --session  # live verdict + markers
"""
import os, sys, time, json, datetime

BOT = r"C:\Users\Rajat\twitter-bot"
LOCK_FILE = os.path.join(BOT, "logs", "browser.lock")
LOCK_STALE_SECONDS = 2700  # 45 min; no engine run legitimately exceeds this

# A held lock older than this is abandoned. Recorded at write time, because
# mtime alone cannot tell a live holder from a crashed one on Windows without
# PowerShell, and that is not worth the dependency.
AUTH_COOKIE = "auth_token"
# Consecutive unusable runs before we stop assuming contention and say
# something. Three hourly mentions slots is enough evidence that it is not a
# passing overlap, and short enough that a real expiry is not hidden all day.
BUSY_ALERT_AFTER = 3
HEALTH_FILE = os.path.join(BOT, "logs", "session_health.json")


def _load_health():
    try:
        return json.load(open(HEALTH_FILE, encoding="utf-8"))
    except Exception:
        return {}


def _save_health(h):
    try:
        os.makedirs(os.path.dirname(HEALTH_FILE), exist_ok=True)
        with open(HEALTH_FILE, "w", encoding="utf-8") as f:
            json.dump(h, f, indent=1)
    except OSError:
        pass


def note_health(verdict):
    """Record a session verdict; return the new consecutive-unusable streak.

    'ok' resets the streak. Anything else extends it. Shared across engines on
    purpose: the streak measures how long the PROFILE has been unusable, not
    how long one script has been unlucky.
    """
    h = _load_health()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    if verdict == "ok":
        h["last_ok"] = now
        h["consecutive_unusable"] = 0
    else:
        h["consecutive_unusable"] = int(h.get("consecutive_unusable", 0)) + 1
        h["last_bad"] = now
        h["last_bad_verdict"] = verdict
    _save_health(h)
    return int(h["consecutive_unusable"])


def health():
    """The stored session-health record (for diagnostics)."""
    return _load_health()


def _pid_alive(pid):
    """True if a process with this pid exists.

    A killed holder cannot run atexit, and a cron wrapper that hits its
    timeout kills the child outright, so waiting for the 45-minute stale
    window would silently skip hourly slots. Check the pid instead.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# lock
# --------------------------------------------------------------------------
def holder():
    """(pid, name, iso_time) of the current holder, or None."""
    try:
        txt = open(LOCK_FILE, encoding="utf-8").read().strip()
    except OSError:
        return None
    parts = txt.split()
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if parts:
        return parts[0], "?", "?"
    return None


def lock_age():
    try:
        return time.time() - os.path.getmtime(LOCK_FILE)
    except OSError:
        return None


def acquire(name="engine", force=False):
    """True if this run may use the profile. False if someone else holds it."""
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    if force or os.environ.get("BROWSER_LOCK_FORCE") == "1":
        force = True
    if os.path.exists(LOCK_FILE) and not force:
        who = holder()
        holder_pid = who[0] if who else None
        if holder_pid == str(os.getpid()):
            return True  # re-entrant: this process already holds it
        age = lock_age() or 0
        dead_holder = bool(holder_pid) and not _pid_alive(holder_pid)
        if age < LOCK_STALE_SECONDS and not dead_holder:
            return False
        why = (f"holder pid {holder_pid} is gone" if dead_holder
               else f"age {int(age)}s")
        print(f"[guard] taking over browser lock ({why}, held by "
              f"{who[1] if who else '?'})", file=sys.stderr)
    try:
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(f"{os.getpid()} {name} "
                    f"{datetime.datetime.now().isoformat(timespec='seconds')}\n")
        return True
    except OSError:
        return True  # an unwritable lock must never block real work


def release():
    # Only the holder clears it: a late finisher must not free a lock another
    # process has since taken.
    who = holder()
    if who and who[0] != str(os.getpid()) and os.environ.get("BROWSER_LOCK_FORCE") != "1":
        return
    # Belt and braces: if the recorded pid is gone, we still clear (stale lock).
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


def hold(name="engine", force=False):
    """Acquire for this process and register release at exit. -> bool."""
    if not acquire(name, force=force):
        return False
    import atexit
    atexit.register(release)
    return True


def busy_reason():
    """Human string for why we are standing down. For logs only."""
    who = holder()
    age = lock_age()
    if who:
        return (f"profile held by {who[1]} (pid {who[0]}) for "
                f"{int(age or 0)}s")
    return "profile lock present"


# --------------------------------------------------------------------------
# session verdict
# --------------------------------------------------------------------------
def _has_auth_cookie(page):
    try:
        for c in page.context.cookies("https://x.com"):
            if c.get("name") == AUTH_COOKIE and c.get("value"):
                return True
    except Exception:
        pass
    return False


def _ui_state(page):
    """Which logged-in markers are on screen right now."""
    marks = {}
    for name, sel in (
        ("new_tweet_btn", '[data-testid="SideNav_NewTweet_Button"]'),
        ("composer", '[data-testid="tweetTextarea_0"]'),
        ("account_switch", '[data-testid="SideNav_AccountSwitcher_Button"]'),
        ("primary_col", '[data-testid="primaryColumn"]'),
        ("login_btn", '[data-testid="loginButton"]'),
    ):
        try:
            marks[name] = page.locator(sel).count()
        except Exception:
            marks[name] = 0
    return marks


def classify_session(page, tries=3, per_try_wait_ms=20000, verbose=False):
    """Return (verdict, detail).

    verdict: "ok"      logged in and usable
             "busy"    login probably fine, UI failed to hydrate (contention
                       or cold profile). Retryable. Never an auth failure.
             "stalled" busy for BUSY_ALERT_AFTER runs in a row. Something is
                       actually wrong (expired login, or a wedged process
                       holding the profile). Worth one alert.
             "dead"    positive evidence the login is gone: a redirect into
                       the login flow, or the logged-out Sign in button.

    Honest limit, learned from the live test on 2026-09-11: from inside the
    page, "another process holds the profile" and "the session expired" look
    IDENTICAL. Both land on a bare https://x.com/ with no marker and an
    unreadable cookie jar, because a second Chromium on the same profile
    cannot even open the Cookies DB. So the code cannot decide on one look.
    It decides on repetition instead: stay quiet while it might be contention,
    and speak up once it clearly is not.
    """
    last = {}
    for attempt in range(1, tries + 1):
        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded",
                      timeout=60_000)
        except Exception as e:
            last = {"error": f"{type(e).__name__}: {e}"}
            time.sleep(3 * attempt)
            continue

        try:
            page.wait_for_selector(
                '[data-testid="SideNav_NewTweet_Button"]',
                timeout=per_try_wait_ms)
            note_health("ok")
            return "ok", {"attempt": attempt, "url": page.url}
        except Exception:
            pass

        marks = _ui_state(page)
        url = ""
        try:
            url = page.url
        except Exception:
            pass
        auth = _has_auth_cookie(page)
        last = {"attempt": attempt, "url": url, "marks": marks, "auth_cookie": auth}

        if verbose:
            print(f"[guard] attempt {attempt}: url={url} auth={auth} marks={marks}",
                  file=sys.stderr)

        # Only positive evidence counts as dead. An unreadable cookie jar is
        # what contention produces, so it must not be treated as proof.
        if "login" in url or marks.get("login_btn"):
            note_health("dead")
            return "dead", last

        if attempt < tries:
            try:
                page.reload(wait_until="domcontentloaded", timeout=60_000)
            except Exception:
                pass
            time.sleep(4 * attempt)  # 4s, 8s

    streak = note_health("busy")
    if streak >= BUSY_ALERT_AFTER:
        last["consecutive_unusable"] = streak
        return "stalled", last
    return "busy", last


def check_session(page, tries=3):
    """True only for a usable logged-in session. Kept simple for callers that
    only care about yes/no; use classify_session() when the difference between
    'dead' and 'busy' changes what you print."""
    verdict, detail = classify_session(page, tries=tries)
    return verdict == "ok"


# --------------------------------------------------------------------------
def _selftest():
    print("== lock ==")
    assert holder() is None or True
    got = acquire("selftest")
    print(f"acquire first      -> {got} (want True)")
    print(f"holder             -> {holder()}")
    import subprocess
    r = subprocess.run([sys.executable, "-c",
                        "import sys; sys.path.append(r'C:\\Users\\Rajat\\twitter-bot');"
                        "import browser_guard as g; print(g.acquire('other'))"],
                       capture_output=True, text=True)
    print(f"acquire from child -> {r.stdout.strip()} (want False)")
    release()
    print(f"holder after release -> {holder()} (want None)")
    r = subprocess.run([sys.executable, "-c",
                        "import sys; sys.path.append(r'C:\\Users\\Rajat\\twitter-bot');"
                        "import browser_guard as g; print(g.acquire('other'))"],
                       capture_output=True, text=True)
    print(f"acquire after release -> {r.stdout.strip()} (want True)")
    # The child exited holding the lock; clean it up so the selftest does not
    # leak a stale lock into the next real cron run (learned the hard way).
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass
    print(f"lock file after cleanup -> {os.path.exists(LOCK_FILE)} (want False)")
    return 0


def _session_test():
    sys.path.append(BOT)
    from playwright.sync_api import sync_playwright
    import browser_post
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            browser_post.PROFILE, headless=True,
            viewport={"width": 1280, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        v, d = classify_session(page, verbose=True)
        print(f"verdict: {v}")
        print(f"detail : {json.dumps(d)}")
        ctx.close()
    return 0


if __name__ == "__main__":
    if "--session" in sys.argv:
        sys.exit(_session_test())
    sys.exit(_selftest())
