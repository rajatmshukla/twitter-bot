#!/usr/bin/env python3
"""Post the next queued draft. One tweet per run.

Usage: python3 post_next.py [--post]
  without --post: dry-run (default)
  with --post:    actually publish (requires keys in .env)

Exit codes: 0 = posted/dry-ran OK, 1 = error, 2 = queue empty
"""
import os, sys, subprocess, glob, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DRAFTS_DIR = os.path.join(BASE, "drafts")

def main():
    drafts = sorted(glob.glob(os.path.join(DRAFTS_DIR, "*.txt")))
    if not drafts:
        print(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] queue empty")
        return 2
    nxt = drafts[0]
    text = open(nxt, encoding="utf-8").read().strip()
    label = os.path.basename(nxt)
    mode = ["--post"] if "--post" in sys.argv else []
    r = subprocess.run([sys.executable, os.path.join(BASE, "post.py"), "--label", label] + mode,
                       input=text, capture_output=True, text=True, cwd=BASE)
    print(r.stdout.strip())
    if r.stderr.strip():
        print("STDERR:", r.stderr.strip(), file=sys.stderr)
    if r.returncode == 0:
        # consumed: remove from queue (posted or dry-ran)
        os.remove(nxt)
        return 0
    return r.returncode

if __name__ == "__main__":
    sys.exit(main())
