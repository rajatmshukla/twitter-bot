#!/usr/bin/env python3
"""Session diagnostic: why did check_session fail? Prints url, title, markers."""
import sys, time
BOT = r"C:\Users\Rajat\twitter-bot"
sys.path.append(BOT)
from playwright.sync_api import sync_playwright
import browser_post

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        browser_post.PROFILE, headless=True,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60_000)
    time.sleep(6)
    print("url      :", page.url)
    print("title    :", page.title())
    print("markers  :", page.evaluate("""() => ({
        navPost: !!document.querySelector('[data-testid="SideNav_NewTweet_Button"]'),
        textarea: !!document.querySelector('[data-testid="tweetTextarea_0"]'),
        loginLink: !!document.querySelector('a[href="/login"]'),
        bodyHead: document.body.innerText.slice(0, 300).replace(/\\n+/g, ' | '),
    })"""))
    page.screenshot(path=r"C:\Users\Rajat\twitter-bot\tmp\session_diag.png")
    ctx.close()
print("screenshot: tmp/session_diag.png")
