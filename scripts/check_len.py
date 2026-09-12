import sys
for p in sys.argv[1:]:
    with open(p, encoding='utf-8') as f:
        t = f.read().strip()
    print(p, '->', len(t), 'chars')
    if len(t) > 280:
        print('  OVER LIMIT by', len(t) - 280)
