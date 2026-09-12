#!/usr/bin/env python3
"""WARNING: This test launches the live browser profile and accesses x.com.
Do not run casually, because it interacts with the active session on x.com.

Behaviour under test:
Regression tests for mentions_guy and browser_guard handling profile contention.
When another process holds the Chromium profile, opening it second yields an unauthenticated
shell. This test verifies that the engine stands down silently on contention rather than firing
false "NOT LOGGED IN" Discord alerts, while still alerting (exit 2) if stalls persist.

Real process cases tested:
  Case C: Stale lock left by a dead process is taken over immediately.
  Case A: Lock held by another engine causes silent stand-down (exit 0, no stdout).
  Case B: Profile held by an unlocked intruder is treated as contention (exit 0, silent).
  Case D: Profile unusable consecutively (streak >= 2) triggers stalled alert (exit 2).

How to run:
    python scripts/test_mentions_contention.py
Requires: Live logged-in Chromium profile, network access to x.com, and Playwright.
Temporarily modifies and restores browser_guard.HEALTH_FILE.

What a failure means in practice:
False alarms will wake the on-call channel during routine contention, crashed processes
will orphan locks blocking future runs, or real authentication loss will go unnoticed.
"""
import json, os, shutil, subprocess, sys, textwrap, time

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
import browser_guard as g

ENGINE = os.path.join(BOT, "mentions_guy.py")
PY = sys.executable
FAILS = []


def check(label, got, want):
    """Assert actual equals expected, logging failures to FAILS list."""
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r} want {want!r}")
    if not ok:
        FAILS.append(label)


def clear_lock():
    """Remove browser guard lock file if present to guarantee a clean baseline."""
    try:
        os.remove(g.LOCK_FILE)
    except OSError:
        pass


def run_engine(timeout=600):
    """Execute mentions_guy.py in a child process with MENTIONS_DRY_RUN unset."""
    env = dict(os.environ)
    env.pop("MENTIONS_DRY_RUN", None)  # production path
    r = subprocess.run([PY, ENGINE], capture_output=True, text=True,
                       timeout=timeout, cwd=BOT, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def spawn_intruder():
    """Spawn a background process holding the live profile without taking the lock."""
    p = subprocess.Popen(
        [PY, "-c", textwrap.dedent(f"""
            import sys, time
            sys.path.append(r"{BOT}")
            import browser_post
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                ctx = p.chromium.launch_persistent_context(
                    browser_post.PROFILE, headless=True,
                    viewport={{"width": 1280, "height": 900}})
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto("https://x.com/home", wait_until="domcontentloaded")
                print("HOLDING", flush=True)
                time.sleep(420)
        """)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    if not p.stdout.readline().strip():
        p.kill()
        return None
    return p


def case_c_dead_holder():
    """Verify that a lock file left behind by a dead PID is claimed immediately."""
    print("\n== C. lock left behind by a killed process ==")
    clear_lock()
    with open(g.LOCK_FILE, "w", encoding="utf-8") as f:
        f.write("999999 killed_engine 2026-09-11T13:00:00\n")
    print(f"   planted lock: {g.holder()}")
    t0 = time.time()
    got = g.acquire("latecomer")
    print(f"   acquire -> {got} in {time.time()-t0:.1f}s")
    check("C: takes over a dead holder's lock", got, True)
    check("C: lock now names the new holder", (g.holder() or ["", ""])[1], "latecomer")
    g.release()


def case_a_lock_held():
    """Verify mentions_guy exits 0 silently without alerts when lock is held."""
    print("\n== A. lock held by another engine ==")
    holder = subprocess.Popen(
        [PY, "-c", textwrap.dedent(f"""
            import sys, time
            sys.path.append(r"{BOT}")
            import browser_guard as g
            assert g.acquire("fake_intruder_locked")
            print("HOLDING", flush=True)
            time.sleep(180)
        """)],
        stdout=subprocess.PIPE, text=True)
    try:
        holder.stdout.readline()
        print(f"   holder: {g.holder()}")
        rc, out, err = run_engine(timeout=120)
        print(f"   rc={rc} stdout={out!r}")
        last = err.splitlines()[-1] if err else ""
        print(f"   stderr: {last}")
        check("A: exit code", rc, 0)
        check("A: no stdout, so no Discord alert", out, "")
        check("A: says nothing about NOT LOGGED IN", "NOT LOGGED IN" in err, False)
    finally:
        holder.kill()
        holder.wait(timeout=20)
    clear_lock()


def case_b_and_d(intruder):
    """Verify contention stays silent on first hit (B) but alerts when stalled (D)."""
    g._save_health({"last_ok": None, "consecutive_unusable": 0})
    print("\n== B. profile held by an intruder that ignores the lock (streak 0) ==")
    rc, out, err = run_engine()
    verdict = [l for l in err.splitlines() if "verdict" in l]
    print(f"   rc={rc} stdout={out!r}")
    print(f"   verdict line: {verdict[-1] if verdict else '(none)'}")
    check("B: exit code 0, not 2", rc, 0)
    check("B: no stdout, so no Discord alert", out, "")
    check("B: classified busy", bool(verdict) and "verdict: busy" in verdict[-1], True)
    check("B: health streak is 1", g.health().get("consecutive_unusable"), 1)

    print("\n== D. same, profile already unusable twice in a row (streak 2) ==")
    g._save_health({"last_ok": None, "consecutive_unusable": 2})
    rc, out, err = run_engine()
    verdict = [l for l in err.splitlines() if "verdict" in l]
    print(f"   rc={rc} stdout={out!r}")
    print(f"   verdict line: {verdict[-1] if verdict else '(none)'}")
    check("D: classified stalled", bool(verdict) and "verdict: stalled" in verdict[-1], True)
    check("D: exit code 2, so the cron surfaces it", rc, 2)
    check("D: explains itself", "no usable X session" in out, True)
    check("D: streak reached 3", g.health().get("consecutive_unusable"), 3)


def main():
    """Run lock contention test suite, backing up and restoring health state."""
    health_before = None
    if os.path.exists(g.HEALTH_FILE):
        health_before = open(g.HEALTH_FILE, encoding="utf-8").read()
    clear_lock()
    try:
        case_c_dead_holder()
        case_a_lock_held()
        time.sleep(2)
        intruder = spawn_intruder()
        if not intruder:
            print("SKIP B/D: could not start the intruder")
        else:
            try:
                case_b_and_d(intruder)
            finally:
                intruder.kill()
                intruder.wait(timeout=20)
    finally:
        clear_lock()
        # Put production session health back the way we found it.
        if health_before is not None:
            open(g.HEALTH_FILE, "w", encoding="utf-8").write(health_before)
        print(f"\nrestored health record: {json.dumps(g.health())}")
    print("\n" + ("ALL PASS" if not FAILS else f"FAILURES: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
