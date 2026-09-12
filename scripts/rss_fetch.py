"""Fetch and parse RSS feed items from a given URL via HTTP request.

Extracts titles, links, and descriptions for up to 15 items and prints them to stdout.
CLI usage: python scripts/rss_fetch.py [feed_url]
"""
import re, sys, urllib.request

def fetch(url):
    """Retrieve feed content over HTTP with a browser User-Agent header and return decoded text."""
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    return urllib.request.urlopen(req, timeout=25).read().decode('utf-8', 'replace')

url = sys.argv[1] if len(sys.argv) > 1 else 'https://techcrunch.com/category/artificial-intelligence/feed/'
data = fetch(url)
items = re.findall(r'<item>(.*?)</item>', data, re.S)
for it in items[:15]:
    t = re.search(r'<title>(.*?)</title>', it, re.S)
    l = re.search(r'<link>(.*?)</link>', it, re.S)
    d = re.search(r'<description>(.*?)</description>', it, re.S)
    def clean(s):
        """Strip HTML tags and convert common HTML entities to plain characters."""
        s = re.sub(r'<[^>]+>', '', s)
        s = re.sub(r'&amp;', '&', s)
        s = re.sub(r'&#8217;|&#39;', "'", s)
        s = re.sub(r'&#8216;', "'", s)
        s = re.sub(r'&#8230;', '...', s)
        s = re.sub(r'&#8220;|&#8221;', '"', s)
        s = re.sub(r'&#8212;', '-', s)
        s = re.sub(r'&lt;', '<', s)
        s = re.sub(r'&gt;', '>', s)
        return s.strip()
    print('TITLE:', clean(t.group(1)) if t else '')
    print('LINK:', l.group(1) if l else '')
    print('DESC:', clean(d.group(1))[:400] if d else '')
    print('---')
