#!/usr/bin/env python3
"""Verification that the execution budget cleanly terminates candidate loops in main().

This script accesses the network for scraping but does not post or mutate state.
Run with:
    python scripts/verify_budget_run.py

Behaviour under test:
Executes the production reply_guy_direct.main() routine with a reduced 120s budget:
- Overrides post_batch to capture drafts without publishing to X.
- Overrides save_state to avoid persisting state mutations.
- Confirms the engine logs a "budget spent" event and cleanly terminates candidate loops.
- Asserts main() returns exit code 0 in well under the cron timeout (takes ~2-3 minutes).

Why this matters:
Pins down the fix for the 2026-09-10 timeout failure, where candidate generation overrun
caused the cron wrapper to kill the process at 1800s, dropping all generated drafts.

What a failure means in practice:
Candidate generation loops are ignoring the deadline, risking hard process termination by the
cron wrapper and complete loss of all generated drafts.
"""
import sys, time

sys.path.append(r"C:\Users\Rajat\twitter-bot")
import reply_guy_direct as rd

rd.RUN_BUDGET_S = 120          # trip the budget fast; scrape alone takes ~16 min
captured = []
logs = []


def fake_post_batch(items):
    """Capture candidate items in memory and simulate successful posting responses."""
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
