import sys, re, html
from html.parser import HTMLParser

class TextExtract(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0
        self.in_pre = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','noscript','svg','head'):
            self.skip += 1
        if tag == 'pre':
            self.in_pre += 1
            self.parts.append('\n```\n')
        if tag in ('p','div','br','li','tr','h1','h2','h3','h4','table'):
            self.parts.append('\n')
        if tag in ('td','th'):
            self.parts.append(' | ')
    def handle_endtag(self, tag):
        if tag in ('script','style','noscript','svg','head'):
            self.skip = max(0, self.skip-1)
        if tag == 'pre':
            self.in_pre = max(0, self.in_pre-1)
            self.parts.append('\n```\n')
        if tag in ('p','div','li','tr','h1','h2','h3','h4'):
            self.parts.append('\n')
    def handle_data(self, data):
        if self.skip == 0:
            self.parts.append(data)

def extract(path, maxlen=200000):
    with open(path, encoding='utf-8', errors='replace') as f:
        src = f.read()
    p = TextExtract()
    p.feed(src)
    text = ''.join(p.parts)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    return text[:maxlen]

if __name__ == '__main__':
    print(extract(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 200000))
