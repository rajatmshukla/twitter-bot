#!/usr/bin/env python3
"""Offline unit checks for run budget and per-run antigravity call caps.

This test does not hit the live account, network, or browser.
Run with:
    python scripts/test_run_budget.py

Behaviour under test:
Pins runtime budget guards and subprocess execution limits in reply_guy_direct:
- rd.budget_spent(): verifies that time limits are obeyed when a deadline is reached.
- rd._antigravity() call cap: blocks repeated slow LLM calls once AGY_MAX_PER_RUN is met.
- rd._antigravity() budget gate: skips LLM invocation entirely once the budget deadline passes.
- rd.RUN_BUDGET_S and AGY_MAX_PER_RUN bounds: ensures limits stay well below the wrapper timeout.

Why this matters:
Without these guards, candidate generation after long scraping runs can trigger the cron
wrapper's 1800-second hard kill, discarding all drafts without publishing anything.

What a failure means in practice:
A failure means reply_guy_direct could overrun its execution window during slow LLM cycles,
causing the task runner to terminate the process and lose all drafted posts.
"""
import sys, time

sys.path.append(r"C:\Users\Rajat\twitter-bot")
import reply_guy_direct as rd

fails = []


def check(name, got, want):
    """Assert actual equals expected, logging failures to fails list."""
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name}: got {got!r} want {want!r}")
    if not ok:
        fails.append(name)


# budget_spent(): no deadline set means the budget never trips
rd._DEADLINE = None
check("budget_spent with no deadline", rd.budget_spent(), False)

rd._DEADLINE = time.time() - 1
check("budget_spent past deadline", rd.budget_spent(), True)

rd._DEADLINE = time.time() + 300
check("budget_spent future deadline", rd.budget_spent(), False)

# agy per-run cap: at the cap, return without spawning a ~60s subprocess
rd._agy_calls = rd.AGY_MAX_PER_RUN
t0 = time.time()
txt, dead = rd._antigravity("this prompt must never reach agy")
elapsed = time.time() - t0
check("agy at cap returns None", txt, None)
check("agy at cap is not KEY_DEAD", dead, False)
check("agy at cap returns instantly", elapsed < 1.0, True)

# budget gate: with the budget spent, agy is skipped, not tried
rd._agy_calls = 0
rd._DEADLINE = time.time() - 1
t0 = time.time()
txt, dead = rd._antigravity("this prompt must also never reach agy")
elapsed = time.time() - t0
check("agy skipped when budget spent", txt, None)
check("agy budget-skip returns instantly", elapsed < 1.0, True)

# the budget must leave real headroom under the wrapper's 1800s kill
check("run budget under the 1800s wrapper kill", rd.RUN_BUDGET_S < 1800, True)
check("agy cap is a small per-run number", rd.AGY_MAX_PER_RUN <= 2, True)

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("ALL PASS")
