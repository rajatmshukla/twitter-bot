#!/usr/bin/env python3
"""Unit checks for the run budget + agy per-run cap (2026-09-10 timeout fix).

Why this exists: the 14:00 run on 2026-09-10 died at the wrapper's 1800s kill
with exit 1, discarding every draft and posting nothing. The agy fallback cost
~2.5 min per cycle and was tried for every candidate that fell through, on top
of a ~16 min scrape. These checks pin the guards that bound a run.
"""
import sys, time

sys.path.append(r"C:\Users\Rajat\twitter-bot")
import reply_guy_direct as rd

fails = []


def check(name, got, want):
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
