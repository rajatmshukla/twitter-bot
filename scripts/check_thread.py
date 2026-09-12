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
