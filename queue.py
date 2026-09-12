#!/usr/bin/env python3
"""Queue management for drafts.

Drafts live in drafts/ as individual .txt files, one tweet each.
The generator (cron) writes them; post.py publishes them.

Usage:
  python3 queue.py list          # list queued drafts
  python3 queue.py add "text"    # add a draft
  python3 queue.py count         # number of drafts
  python3 queue.py next          # print the oldest draft text
"""
import os, sys, glob, datetime

DRAFTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drafts")

def list_drafts():
    return sorted(glob.glob(os.path.join(DRAFTS_DIR, "*.txt")))

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for p in list_drafts():
            print(os.path.basename(p), "|", open(p, encoding="utf-8").read().strip()[:60])
    elif cmd == "add":
        text = " ".join(sys.argv[2:]).strip()
        if not text:
            print("ERROR: no text", file=sys.stderr); sys.exit(1)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        # unique name
        i = 0
        while True:
            name = f"{ts}_{i}.txt" if i else f"{ts}.txt"
            p = os.path.join(DRAFTS_DIR, name)
            if not os.path.exists(p):
                break
            i += 1
        open(p, "w", encoding="utf-8").write(text)
        print(f"queued: {name}")
    elif cmd == "count":
        print(len(list_drafts()))
    elif cmd == "next":
        drafts = list_drafts()
        if drafts:
            print(open(drafts[0], encoding="utf-8").read().strip())
        else:
            print("(empty)")
    else:
        print("unknown command", file=sys.stderr); sys.exit(1)

if __name__ == "__main__":
    main()
