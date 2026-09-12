#!/usr/bin/env python3
"""Publish a single tweet to X via the official API v2 using tweepy.

Provides command-line tweet publishing using OAuth 1.0a user context.
Enforces character limits, supports stdin or file inputs, and defaults to a safe
dry-run mode unless explicitly instructed to publish.

Invocation:
- CLI:
    python post.py --file <path> [--post] [--label <str>]
    cat tweet.txt | python post.py [--post] [--label <str>]
- May be called by automated scheduling scripts or cron pipelines to publish
  pre-generated tweets via the official API v2.

Inputs / Reads:
- Reads credentials from local .env file (X_API_KEY, X_API_SECRET,
  X_ACCESS_TOKEN, X_ACCESS_SECRET).
- Reads tweet text from the file path given by --file, or standard input.

Outputs / Writes:
- Appends audit logs to logs/posts.log for dry runs and live posts.

Live X Account Impact:
- When --post is passed with valid keys: publishes a public tweet to the
  authenticated account and consumes X API v2 write budget.
- When --post is omitted (default) or keys are missing: operates in dry-run
  mode without making any network calls to X.
"""
import os, sys, argparse, datetime

def load_env(path=".env"):
    """Parse key-value configuration pairs from a dotenv file.

    Simple parser to read environment variables without external dependencies.
    Ignores comments and empty lines.

    Args:
        path: File system path to the dotenv file (defaults to '.env').
    Returns:
        Dict mapping string keys to string values found in the file.
    Side effects:
        Reads file from disk if it exists.
    """
    env = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

def main():
    """CLI entry point for validating and publishing a tweet via API v2.

    Parses command-line arguments, validates tweet text length, checks for
    required credentials, and either logs a dry-run or publishes to X via
    tweepy.Client.create_tweet.

    Returns:
        Integer exit code: 0 on success or valid dry-run, 1 on error.
    Side effects:
        Reads stdin or disk file, appends audit entries to logs/posts.log,
        and creates a public post on X when --post is specified with API keys.
    """
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
