#!/usr/bin/env python3
"""Read the X profile's cookie store from disk, without launching Chromium.

Tells apart two things that look identical from inside a page:
  - the login cookies are gone / corrupted  -> browser_login.py is required
  - the cookies are on disk and fine        -> the browser just could not
                                               open the store (contention or a
                                               wedged profile)

Read-only: opens the SQLite file with mode=ro and never writes to it.

  python3 scripts/probe_profile_cookies.py
"""
import os, sqlite3, sys, datetime

BOT = r"C:\Users\Rajat\twitter-bot"
CANDIDATES = [
    os.path.join(BOT, "browser-profile", "Default", "Network", "Cookies"),
    os.path.join(BOT, "browser-profile", "Default", "Cookies"),
]
DAYS = 86400  # Chromium expires_utc is microseconds since 1601-01-01


def chrome_time_to_iso(v):
    """Convert a Chromium timestamp in microseconds since 1601-01-01 UTC to an ISO string.

    Adds integer microseconds v as a timedelta to the 1601-01-01 00:00:00 UTC epoch,
    returning a timezone-aware ISO 8601 string or None on failure or empty input.
    """
    if not v:
        return None
    try:
        # microseconds since 1601-01-01
        epoch1601 = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
        return (epoch1601 + datetime.timedelta(microseconds=int(v))).isoformat()
    except Exception:
        return None


def main():
    """Inspect the local Chromium cookie database on disk for valid Twitter login sessions.

    Drives SQLite read-only queries against candidate profile paths without launching Chromium.
    Checks database integrity, counts cookies, and checks expiration of x.com auth tokens.
    Prints file stats, table integrity, and cookie rows to stdout. Writes nothing to disk.
    """
    found = False
    for path in CANDIDATES:
        if not os.path.exists(path):
            print(f"missing: {path}")
            continue
        found = True
        size = os.path.getsize(path)
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")
        print(f"\n=== {path}")
        print(f"    size {size} bytes, modified {mtime}")
        uri = "file:" + path.replace("\\", "/") + "?mode=ro&immutable=1"
        try:
            con = sqlite3.connect(uri, uri=True)
        except Exception as e:
            print(f"    OPEN FAILED: {type(e).__name__}: {e}")
            continue
        try:
            cur = con.cursor()
            print(f"    integrity_check: {cur.execute('PRAGMA integrity_check').fetchone()[0]}")
            total = cur.execute("select count(*) from cookies").fetchone()[0]
            print(f"    total cookies: {total}")
            rows = cur.execute(
                "select name, host_key, length(encrypted_value), expires_utc "
                "from cookies where host_key like '%x.com%' order by host_key, name"
            ).fetchall()
            print(f"    x.com/twitter.com rows: {len(rows)}")
            for name, host, ln, exp in rows:
                iso = chrome_time_to_iso(exp)
                flag = ""
                if name == "auth_token":
                    expired = False
                    if exp:
                        now_us = (datetime.datetime.now(datetime.timezone.utc)
                                  - datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
                                  ).total_seconds() * 1_000_000
                        expired = int(exp) < now_us
                    flag = "  <== AUTH  EXPIRED" if expired else "  <== AUTH cookie present"
                print(f"      {name:<24} {host:<14} len={ln:<5} exp={iso}{flag}")
        except Exception as e:
            print(f"    QUERY FAILED: {type(e).__name__}: {e}")
        finally:
            con.close()
    if not found:
        print("no cookie store found at any candidate path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
