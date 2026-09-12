#!/usr/bin/env python3
"""Render banner and avatar HTML templates to PNG image assets using Playwright.

Invocation:
    python render_profile_assets.py

Inputs:
    assets/banner.html (rendered at 1500x500 viewport)
    assets/avatar.html (rendered at 400x400 viewport)

Outputs:
    assets/banner.png (1500x500 header banner image)
    assets/avatar.png (400x400 profile avatar image)

Side effects:
    Launches headless Chromium and overwrites PNG image assets on disk.
    It is not clear from this file if an automated scheduler triggers it.
"""
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
        # Enforce 1:1 scale factor so output image dimensions match viewport exactly
        page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
        page.goto(url, wait_until="networkidle", timeout=60_000)
        # Allow web fonts and layout styling to stabilize after networkidle
        page.wait_for_timeout(500)
        page.locator("body").screenshot(path=out)
        print("saved:", out)
    browser.close()
