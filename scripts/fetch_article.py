import re, sys, urllib.request

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    return urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'replace')

url = sys.argv[1]
data = fetch(url)

# meta description
m = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', data)
if m:
    print('META-DESC:', m.group(1)[:800])
    print('---')

# JSON-LD articleBody
for j in re.findall(r'<script type="application/ld\+json">(.*?)</script>', data, re.S):
    if 'articleBody' in j:
        b = re.search(r'"articleBody"\s*:\s*"((?:[^"\\]|\\.)*)"', j)
        if b:
            body = b.group(1).replace('\\n', '\n').replace('\\"', '"').replace('\\u2019', "'").replace('\\u2014', '-').replace('\\u201c', '"').replace('\\u201d', '"')
            print('BODY:', body[:2500])
            print('---')
            break

# fallback: visible paragraphs
paras = re.findall(r'<p[^>]*>(.*?)</p>', data, re.S)
txt = []
for p in paras:
    t = re.sub(r'<[^>]+>', '', p).strip()
    if len(t) > 60:
        txt.append(t)
if txt:
    print('PARAS:')
    print('\n'.join(txt[:25])[:3000])
