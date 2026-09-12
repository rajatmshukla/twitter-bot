#!/usr/bin/env python3
"""FIFO draft queue consumer: post the next queued draft to X.

Finds the earliest draft file in the drafts/ directory in sorted filename order,
pipes its contents into post.py as a subprocess, and deletes the draft file if
posting (or dry-running) succeeds.

Invocation:
- CLI / cron:
    python post_next.py
    python post_next.py --post
- Typically scheduled via cron or task scheduler for periodic draft release.

Inputs / Reads:
- Reads draft files matching drafts/*.txt in lexicographical order.

Outputs / Writes:
- Deletes the consumed draft file on returncode 0 (in both dry-run and post modes).
- Delegates logging to post.py, which writes to logs/posts.log.

Live X Account Impact:
- With --post: publishes a live tweet to the account via post.py and spends API quota.
- Without --post (default): dry-run only, does not touch the live X account.

Exit codes:
- 0: Successfully posted or dry-ran.
- 1: Subprocess error during posting.
- 2: Queue is empty (no drafts found).
"""
import os, sys, subprocess, glob, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DRAFTS_DIR = os.path.join(BASE, "drafts")

def main():
    """Pop and publish the next pending draft from the drafts queue directory.

    Scans drafts/*.txt sorted alphabetically, takes the first entry, passes its
    text to post.py via subprocess, and removes the file if post.py exits with 0.

    Returns:
        Integer exit code: 0 if processed, 1 on subprocess failure, 2 if queue empty.
    Side effects:
        Reads and permanently deletes the earliest draft file in drafts/.
        Executes post.py subprocess, potentially creating a live post on X.
    """
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
