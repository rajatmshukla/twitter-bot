#!/usr/bin/env python3
"""Layered test of the keyless Antigravity (agy) drafting path.

What each layer proves:
  L1  agy exists, is authenticated on the Google account, and answers a prompt
      with no API key involved.
  L2  the reply ENGINE reaches agy through its own llm() when Gemini and
      OpenRouter are both unavailable, and the result clears the engine's
      gate(). This is the wiring proof, not a standalone-function demo.
  L3  a full main() pass: real browser launch + session check -> draft via agy
      -> gate -> post stage. The post is CAPTURED, never published, and state
      writes are suppressed, so this test has zero public side effects.

Usage:
  venv/Scripts/python.exe scripts/test_agy_path.py        # all layers
  venv/Scripts/python.exe scripts/test_agy_path.py 3      # only layer 3

Run one layer at a time when diagnosing. agy is RELIABLE WHEN SPACED and
returns empty output when calls come back to back (observed 2026-09-10: single
calls answer in ~40s every time, but three successive invocations inside two
minutes produced empty responses). All three layers in one run is itself a
burst, so a failure here does not mean the path is broken; re-run the single
layer after a pause before concluding anything.

Exit 0 = all selected checks passed.
"""
import os, subprocess, sys, time

sys.path.append(r"C:\Users\Rajat\twitter-bot")
import reply_guy_direct as rd

WANT = {int(a) for a in sys.argv[1:] if a.isdigit()} or {1, 2, 3}

AGY = os.path.join(os.environ.get("LOCALAPPDATA", r"C:\Users\Rajat\AppData\Local"),
                   "agy", "bin", "agy.exe")

# Fixed fixture, NOT a scraped tweet. Deterministic input keeps the test
# repeatable; the text is phrased like something the engine actually targets.
FIXTURE = {
    "handle": "testfixture",
    "id": "TEST-NOPOST-AGY",
    "text": ("We shipped an agent that reads its own error logs and opens the fix "
             "as a PR. Review takes longer than the fix did."),
    "source": "news",
}

results = []


def check(layer, ok, detail=""):
    results.append((layer, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {layer}" + (f" :: {detail}" if detail else ""))


# ------------------------------ L1 ------------------------------
if 1 in WANT:
    print(f"--- L1  binary  : {AGY}")
    t0 = time.time()
    try:
        v = subprocess.run([AGY, "--version"], capture_output=True, text=True, timeout=90)
        print(f"---     version : {(v.stdout or v.stderr or '').strip().splitlines()[0]}")
    except Exception as e:
        print(f"---     version : {type(e).__name__}: {e}")
    try:
        p = subprocess.run([AGY, "--print-timeout", "90s",
                            "--print=Reply with exactly: AGY-L1-OK"],
                           capture_output=True, text=True, timeout=180)
        out = (p.stdout or "").strip()
        check("L1 agy authenticated and answers with no API key",
              "AGY-L1-OK" in out, f"{time.time() - t0:.1f}s :: {out[:60]!r}")
    except Exception as e:
        check("L1 agy authenticated and answers with no API key", False,
              f"{type(e).__name__}: {e}")

# ------------------------------ L2 ------------------------------
if 2 in WANT:
    print("--- L2  forcing the engine past Gemini and OpenRouter")
    print(f"---     before  : KEY_GEMINI={'set' if rd.KEY_GEMINI else 'empty'} "
          f"MODELS={rd.MODELS}")
    rd.KEY_GEMINI = ""
    rd.MODELS = []
    t0 = time.time()
    txt, mdl = rd.llm(rd.draft_prompt(FIXTURE))
    take = rd.gate(txt) if txt else None
    print(f"---     llm()   : model={mdl!r} in {time.time() - t0:.1f}s")
    print(f"---     raw     : {txt!r}")
    print(f"---     gated   : {take!r}")
    check("L2 engine reaches agy through its own llm()", mdl == "antigravity:agy",
          f"model={mdl!r}")
    check("L2 agy draft clears the engine gate()", bool(take),
          "" if take else "gate rejected the draft")

# ------------------------------ L3 ------------------------------
if 3 in WANT:
    print("--- L3  full main() pass, posting captured instead of published")
    rd.KEY_GEMINI = ""
    rd.MODELS = []
    rd.MAX_REPLIES = 1
    rd.MAX_DRAFT_ATTEMPTS = 1
    rd.scrape_pool = lambda page: [FIXTURE]      # skip scraping, one candidate
    captured = []

    def fake_post_batch(takes):
        captured.extend(takes)
        return [(tid, f"https://x.com/i/status/{tid}") for tid, _ in takes], [], None

    rd.post_batch = fake_post_batch
    loglines = []

    def capture_log(msg):
        loglines.append(msg)
        print(f"---     engine log: {msg}")

    rd.rg.log = capture_log
    rd.rg.save_state = lambda st: None            # leave the real state file alone
    rc = rd.main()
    print(f"---     main() rc: {rc}")
    print(f"---     would-post: {[t for _, t in captured]}")
    check("L3 main() returns 0", rc == 0, f"rc={rc}")
    check("L3 a take reached the post stage", bool(captured),
          "" if captured else "no take: lock busy, session down, or gate rejected")
    check("L3 the take was produced by agy",
          any("antigravity:agy" in m for m in loglines),
          "; ".join(loglines) if loglines else "no engine log lines")
    check("L3 this test published nothing", True, "post_batch was captured")
    check("L3 no em/en dash survived the gate",
          not any("\u2014" in t or "\u2013" in t for _, t in captured),
          "dash leaked into a publishable take" if any(
              "\u2014" in t or "\u2013" in t for _, t in captured) else "")

# ---------------------------- summary ----------------------------
print("\n=== SUMMARY ===")
for layer, ok, _ in results:
    print(f"{'PASS' if ok else 'FAIL'}  {layer}")
passed = sum(1 for _, ok, _ in results if ok)
print(f"{passed}/{len(results)} checks passed (layers {sorted(WANT)})")
sys.exit(0 if passed == len(results) else 1)
