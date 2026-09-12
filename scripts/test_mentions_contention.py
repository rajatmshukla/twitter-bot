#!/usr/bin/env python3
"""Regression tests for the 2026-09-11 13:41 mentions-guy failure.

That run printed "NOT LOGGED IN - run browser_login.py" and exited 2, which
sent a red cron alert to Discord. The login was never broken. A one-shot
verify_x_account.py run held the same Chromium profile; the mentions engine
opened it second and got a logged-out shell.

Cases, all with real processes:

  C. lock left by a dead process  -> taken over immediately, not after 45 min.
                                     (A cron timeout kill cannot run atexit.)
  A. lock held by another engine  -> stand down SILENTLY: exit 0, no stdout.
  B. profile held by an intruder  -> first sighting is treated as contention:
     that ignores the lock            silent, exit 0, no alert.
  D. same, with the profile already unusable twice in a row -> "stalled":
                                     the engine finally speaks, exit 2.

B and D together are the point. B is why the false alarm disappears. D is why
it cannot hide a real expiry behind silence forever.

Usage: python3 scripts/test_mentions_contention.py
"""
import json, os, shutil, subprocess, sys, textwrap, time

BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
import browser_guard as g

ENGINE = os.path.join(BOT, "mentions_guy.py")
PY = sys.executable
FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r} want {want!r}")
    if not ok:
        FAILS.append(label)


def clear_lock():
    try:
        os.remove(g.LOCK_FILE)
    except OSError:
        pass


def run_engine(timeout=600):
    env = dict(os.environ)
    env.pop("MENTIONS_DRY_RUN", None)  # production path
    r = subprocess.run([PY, ENGINE], capture_output=True, text=True,
                       timeout=timeout, cwd=BOT, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def spawn_intruder():
    """A process holding the PROFILE without taking the lock: the 13:41 shape."""
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
    """B: first sighting -> silent. D: while already stalled -> alert."""
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
