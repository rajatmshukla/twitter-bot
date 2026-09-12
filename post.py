#!/usr/bin/env python3
"""Post a generated tweet to X via the API v2 (tweepy, OAuth 1.0a user context).

Modes:
  --post       actually post (requires keys in .env)
  --dry-run    print what would be posted, post nothing (default)

Requires a .env file with:
  X_API_KEY=            (consumer key)
  X_API_SECRET=         (consumer secret)
  X_ACCESS_TOKEN=       (access token)
  X_ACCESS_SECRET=      (access token secret)

Tweet text is read from stdin (piped) or a file argument.
"""
import os, sys, argparse, datetime

def load_env(path=".env"):
    env = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", action="store_true", help="actually post (default is dry-run)")
    ap.add_argument("--file", help="read tweet text from file")
    ap.add_argument("--label", default="manual", help="label for the log line")
    args = ap.parse_args()

    if args.file:
        text = open(args.file, encoding="utf-8").read().strip()
    else:
        text = sys.stdin.read().strip()

    if not text:
        print("ERROR: empty tweet text", file=sys.stderr)
        sys.exit(1)

    if len(text) > 280:
        print(f"ERROR: tweet too long ({len(text)} chars)", file=sys.stderr)
        sys.exit(1)

    env = load_env()
    keys_present = all(env.get(k) for k in
                       ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"))

    ts = datetime.datetime.now().isoformat(timespec="seconds")

    if not args.post or not keys_present:
        mode = "DRY-RUN" if not keys_present else "DRY-RUN(--post w/o keys)"
        print(f"[{ts}] {mode} [{args.label}] {len(text)} chars: {text[:60]}...")
        # log to file for the audit trail
        with open("logs/posts.log", "a", encoding="utf-8") as f:
            f.write(f"{ts} {mode} [{args.label}] {len(text)} chars\n")
        return 0

    import tweepy
    client = tweepy.Client(
        consumer_key=env["X_API_KEY"],
        consumer_secret=env["X_API_SECRET"],
        access_token=env["X_ACCESS_TOKEN"],
        access_token_secret=env["X_ACCESS_SECRET"],
    )
    resp = client.create_tweet(text=text)
    if resp.data and resp.data.get("id"):
        tid = resp.data["id"]
        print(f"[{ts}] POSTED [{args.label}] id={tid}: {text[:60]}...")
        with open("logs/posts.log", "a", encoding="utf-8") as f:
            f.write(f"{ts} POSTED [{args.label}] id={tid} {len(text)} chars\n")
        return 0
    else:
        print(f"[{ts}] ERROR: post failed, response={resp}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
