#!/usr/bin/env python3
"""Render banner + avatar HTML to PNGs via headless Chromium."""
from playwright.sync_api import sync_playwright

JOBS = [
    ("file:///C:/Users/Rajat/twitter-bot/assets/banner.html",
     r"C:\Users\Rajat\twitter-bot\assets\banner.png", 1500, 500),
    ("file:///C:/Users/Rajat/twitter-bot/assets/avatar.html",
     r"C:\Users\Rajat\twitter-bot\assets\avatar.png", 400, 400),
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for url, out, w, h in JOBS:
        page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
        page.goto(url, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(500)
        page.locator("body").screenshot(path=out)
        print("saved:", out)
    browser.close()
