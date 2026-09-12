#!/usr/bin/env python3
"""Check whether text files fit within Twitter's 280-character post limit.

Usage:
    python scripts/check_len.py <file1> [<file2> ...]

What it reads:
Reads one or more text files provided via command-line arguments (sys.argv[1:]),
decoding them as UTF-8 and stripping leading/trailing whitespace.

What it measures:
Measures the character length of each file's stripped content against the 280-character
standard post cap on X.

What the printed verdict means:
For each file, prints:
    <file_path> -> <N> chars
If N > 280, prints an additional warning line:
    OVER LIMIT by <N - 280>
This verdict indicates that the file content exceeds Twitter's limit and cannot be posted
as a single tweet without trimming. Note that the script always exits with code 0.
"""
import sys
for p in sys.argv[1:]:
    with open(p, encoding='utf-8') as f:
        t = f.read().strip()
    print(p, '->', len(t), 'chars')
    if len(t) > 280:
        print('  OVER LIMIT by', len(t) - 280)
