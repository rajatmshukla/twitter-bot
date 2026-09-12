#!/usr/bin/env python3
"""Validate thread structure and post lengths for multi-part tweet drafts.

Usage:
    python scripts/check_thread.py <thread_file>

What it reads:
Reads a single thread draft file from sys.argv[1], decoded as UTF-8. Thread segments
are delimited by the string "<<<BREAK>>>".

What it measures:
- Number of thread segments parsed by splitting on "<<<BREAK>>>".
- Character length of each part after stripping leading and trailing newlines.
- Whether each segment satisfies the 280-character limit.
- Consistency between delimiter occurrences and parsed part counts.

What the printed verdict means:
- num parts: Count of tweets comprising the thread.
- part <i>: <n> chars [OK|TOO LONG]: Verification of individual tweet length.
- separator exact: True if delimiter count strictly equals len(parts) - 1.
- all <= 280: Overall verdict. True means every tweet in the thread is within the
  280-character limit and ready to post. False means one or more parts will fail posting.
  Note that the script outputs verdicts to stdout and exits with code 0.
"""
import sys

path = sys.argv[1]
with open(path, encoding='utf-8') as f:
    content = f.read()

parts = content.split('<<<BREAK>>>')
print('num parts:', len(parts))
ok = True
for i, p in enumerate(parts, 1):
    stripped = p.strip('\n')
    n = len(stripped)
    status = 'OK' if n <= 280 else 'TOO LONG'
    if n > 280:
        ok = False
    print(f'part {i}: {n} chars [{status}]')
    print('---')
    print(stripped)
    print('---')
print('separator exact:', content.count('<<<BREAK>>>') == len(parts) - 1)
print('all <= 280:', ok)
