#!/usr/bin/env python3
"""Full profile audit for @first_sauce_lab.

Collects profile statistics and engagement metrics: follower and following counts,
bio header text, avatar and banner presence, pinned post status, per-post engagement
(views, likes, sample text) for up to 10 recent posts, and 7-day post impressions.
Impressions are extracted from the profile 7-day banner when present, or summed
across up to 8 individual post /analytics pages as a fallback.

Invocation:
    CLI or cron:
        python3 profile_audit.py
    Invoked daily every morning by impressions_cron.
    Acquires browser_guard lock to prevent collisions with concurrent browser bots.

Inputs and Outputs:
    Reads:
        - Persistent browser cookies and session state from browser-profile.
        - Profile and tweet DOM elements on x.com.
    Writes:
        - Emits JSON summary of profile and recent posts to stdout.
        - Appends timestamped audit record row to logs/impressions.csv.
        - Prints diagnostic messages to stderr when profile is busy or logged out.

Live Account Effects:
    Opens persistent browser session, visits the profile page, and optionally
    navigates up to 8 post analytics pages with randomized pauses (1.5-2.5s).
    Read-only operation; does not post, follow, like, or mutate account state.
"""
import os, time, random, json, csv, datetime, sys, re, atexit
from playwright.sync_api import sync_playwright

BOT = r"C:\Users\Rajat\twitter-bot"
PROFILE = os.path.join(BOT, "browser-profile")
USER = "first_sauce_lab"
CSV = os.path.join(BOT, "logs", "impressions.csv")

def num(s):
    """Parse a human-readable metric string with K/M multipliers into an integer.

    Handles numeric strings with commas, decimal points, and optional K/M
    magnitude suffixes (e.g. '1.5K' -> 1500, '2M' -> 2000000).

    Args:
        s: Raw string representation of a count.

    Returns:
        Parsed integer value, or 0 if parsing fails.

    Side effects:
        None. Pure string parsing function.
    """
    if not s:
        return 0
    s = s.strip().upper()
    m = re.match(r"^([\d.,]+)\s*([KM]?)$", s)
    if not m:
        return 0
    v = float(m.group(1).replace(",", ""))
    mult = {"": 1, "K": 1000, "M": 1000000}.get(m.group(2), 1)
    return int(v * mult)

def sum_post_impressions(page, arts):
    """Calculate aggregate impressions by visiting individual post analytics pages.

    Acts as a fallback when X's 7-day impressions banner is missing from the
    profile header. Iterates through up to 8 visible post articles, visits each
    post's /analytics URL, parses the Impressions metric, and sums them.

    Args:
        page: Playwright Page instance with active session.
        arts: Playwright Locator representing visible tweet article elements.

    Returns:
        Tuple of (formatted_sum, source_tag), e.g. ('1.2K', 'SUM').
        Returns ('', 'SUM') if all analytics pages fail.

    Side effects:
        Navigates page to up to 8 post /analytics pages, sleeps 1.5-2.5s per page,
        and consumes X analytics page requests.
    """
    total, n_ok = 0, 0
    # Cap at 8 posts to keep analytics page crawling within a ~20s timeframe.
    for i in range(min(arts.count(), 8)):
        try:
            a = arts.nth(i)
            links = a.locator('a[href*="/analytics"]')
            href = links.first.get_attribute("href") if links.count() else ""
            if not href:
                continue
            page.goto("https://x.com" + href, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(random.uniform(1.5, 2.5))
            m = re.search(r"Impressions\s*([\d.,]+[KM]?)", page.inner_text("body"))
            if m:
                total += num(m.group(1))
                n_ok += 1
        except Exception:
            continue
    if n_ok == 0:
        return "", "SUM"
    if total >= 1_000_000:
        return f"{total / 1e6:.1f}M", "SUM"
    if total >= 1000:
        return f"{total / 1000:.1f}K", "SUM"
    return str(total), "SUM"


# Take the shared profile lock before opening the profile (2026-09-11).
# impressions_cron runs this every morning, and this script opens the same
# persistent profile every other engine uses. Without the lock it collides
# exactly like the 13:41 incident: the second opener reads a logged-out shell
# and reports a dead login that is not dead. Stand down quietly instead.
sys.path.insert(0, BOT)
import browser_guard

if not browser_guard.acquire("profile_audit"):
    print(json.dumps({"skipped": "PROFILE BUSY",
                      "reason": browser_guard.busy_reason()}))
    print("profile audit: standing down, profile busy", file=sys.stderr)
    sys.exit(0)

atexit.register(browser_guard.release)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, headless=True, viewport={"width": 1280, "height": 1000},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(f"https://x.com/{USER}", wait_until="domcontentloaded", timeout=60_000)
    time.sleep(random.uniform(3, 5))

    # logged in check
    if page.locator('[data-testid="SideNav_NewTweet_Button"]').count() == 0:
        msg = json.dumps({"error": "NOT LOGGED IN"})
        print(msg)
        print(msg, file=sys.stderr)  # wrapper alert must carry the reason
        ctx.close()
        sys.exit(2)

    result = {"user": USER, "ts": datetime.datetime.now().isoformat(timespec="minutes")}

    # header text (bio, name, counts)
    col = page.locator('[data-testid="primaryColumn"]').first
    header = col.inner_text()
    result["header"] = header[:600]

    # X's own 7-day impressions line, e.g. "1.6K impressions on your posts in
    # the last 7 days". This is the KPI Rajat cares about. X rotates this UI
    # variant on/off (gone since ~2026-08-21), so when the line is missing we
    # fall back to summing per-post Impressions from each visible post's
    # /analytics page (below, after the per-post loop). The banner is tried
    # first, re-read once, then the whole body.
    imp_pat = re.compile(r"([\d.,]+[KM]?)\s*(?:impressions|views)\s+on your posts")
    m_imp = imp_pat.search(header)
    if not m_imp:
        time.sleep(4)
        header = col.inner_text()
        m_imp = imp_pat.search(header)
    if not m_imp:
        m_imp = imp_pat.search(page.inner_text("body"))
    if m_imp:
        result["impressions_7d"] = m_imp.group(1)
        result["impressions_source"] = "BANNER"
    else:
        result["impressions_7d"] = ""
        result["impressions_source"] = "SUM"  # resolved after the post loop

    # counts
    for label in ["Following", "Followers"]:
        try:
            el = page.locator(f'a[href="/{USER}/{label.lower()}"][role="link"]').first
            result[label.lower()] = el.inner_text() if el.count() else ""
        except Exception:
            pass
    # regex fallback straight from the header text ("14 Following / 3 Followers")
    for label in ["Following", "Followers"]:
        if not result.get(label.lower()):
            m = re.search(r"([\d,]+)\s*" + label, header)
            if m:
                result[label.lower()] = m.group(1)

    # avatar/banner presence
    imgs = page.locator(f'img[src*="profile_images"]')
    result["avatar_banner_imgs"] = imgs.count()

    # pinned badge
    result["pinned"] = page.locator('span:has-text("Pinned")').count() > 0

    # per-post engagement
    arts = page.locator('article[data-testid="tweet"]')
    # Sample up to 10 most recent posts to assess recent reach without excessive scrolling.
    n = min(arts.count(), 10)
    posts = []
    total_views = 0
    for i in range(n):
        try:
            a = arts.nth(i)
            # Full inner text: engagement metrics row sits at the bottom of the article.
            full = a.inner_text()
            links = a.locator('a[href*="/status/"]')
            href = links.first.get_attribute("href") if links.count() else ""
            m = re.search(r"/status/(\d+)", href or "")
            tid = m.group(1) if m else ""
            views = 0
            m2 = re.search(r"([\d.,]+[KM]?)\s*views", full)
            if not m2:
                try:
                    vl = a.locator('[aria-label*="views"]').first.get_attribute("aria-label") or ""
                    m2 = re.search(r"([\d.,]+[KM]?)", vl)
                except Exception:
                    pass
            if m2:
                views = num(m2.group(1))
            # likes
            like_btn = a.locator('[data-testid="like"]')
            likes = 0
            if like_btn.count():
                lbl = like_btn.first.get_attribute("aria-label") or ""
                m3 = re.search(r"([\d.,]+[KM]?)", lbl.replace("likes", "").strip())
                if m3:
                    likes = num(m3.group(1))
            posts.append({"id": tid, "views": views, "likes": likes,
                          "text": full[:120].replace("\n", " | ")})
            total_views += views
        except Exception as e:
            posts.append({"err": str(e)})
    result["posts"] = posts
    result["total_views_sample"] = total_views

    print(json.dumps(result, indent=1))

    # banner missing -> sum per-post impressions from /analytics pages
    if result.get("impressions_source") == "SUM":
        imp, src = sum_post_impressions(page, arts)
        result["impressions_7d"] = imp
        result["impressions_source"] = src

    # append impressions row
    with open(CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([result["ts"], result.get("followers", ""),
                    result.get("following", ""), result.get("impressions_7d", ""),
                    total_views, n, result.get("impressions_source", "PROFILE")])
    ctx.close()
