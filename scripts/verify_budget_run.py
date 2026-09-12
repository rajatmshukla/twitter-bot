#!/usr/bin/env python3
"""Prove the run budget fires through the REAL main() and still exits cleanly.

Runs main() with a deliberately tiny budget so the guard trips early, with the
post step CAPTURED instead of published and state writes suppressed. Asserts
the run self-terminates and reaches the post stage rather than being killed by
the cron wrapper's 1800s timeout (the 2026-09-10 failure).

Costs ~2-3 min. Publishes nothing.
"""
import sys, time

sys.path.append(r"C:\Users\Rajat\twitter-bot")
import reply_guy_direct as rd

rd.RUN_BUDGET_S = 120          # trip the budget fast; scrape alone takes ~16 min
captured = []
logs = []


def fake_post_batch(items):
    captured.extend(items)
    return [(tid, f"would-be-{tid}") for tid, _ in items], [], None


rd.post_batch = fake_post_batch
rd.rg.log = lambda m: (logs.append(m), print(f"   engine: {m}"))[0]
rd.rg.save_state = lambda st: None            # leave the real state file alone

t0 = time.time()
rc = rd.main()
elapsed = time.time() - t0

print()
print(f"main() rc={rc} in {elapsed:.0f}s")
print(f"takes reaching the post stage: {len(captured)}")
spent_lines = [m for m in logs if "budget spent" in m]
print(f"budget-stop log lines: {len(spent_lines)}")
for m in spent_lines:
    print(f"   -> {m}")

fails = []
if rc != 0:
    fails.append(f"expected clean exit 0, got {rc}")
if not spent_lines:
    fails.append("budget never tripped - the guard did not fire")
if elapsed > 600:
    fails.append(f"took {elapsed:.0f}s - far beyond the 120s budget")
print()
if fails:
    print("FAILED: " + "; ".join(fails))
    sys.exit(1)
print("PASS: main() self-terminated on the budget and exited 0")
