# LegolasBot setup — browser-first (free), API optional

## Option A: Browser automation (FREE — recommended to start)

No developer portal, no API, no monthly fee. The bot drives the X web UI.

1. **Create the X account** (new or existing, yours).
   - Handle: original, AI-insider spirit. No impersonation.
   - Profile: name, bio (leaker persona), avatar, banner.
2. **Log in once** (you type your credentials — the bot never sees them):
   ```
   cd ~/twitter-bot
   python3 browser_login.py
   ```
   A real browser window opens at x.com/login. Log in yourself, wait until you
   see your home timeline, close the window. Session is saved to
   `~/twitter-bot/browser-profile/`.
3. **Verify**:
   ```
   python3 browser_post.py --check
   ```
   → "SESSION OK". From here the cron posts drafts automatically (9am/1pm/6pm ET).

Risks: X can occasionally force re-login or flag automated behavior. If the bot
stops posting, run browser_login.py again. Keep cadence human (2-3/day, already
the schedule).

## Option B: API (paid, ~$100/mo, most reliable)

Only if/when the account earns enough to justify it.
1. https://developer.x.com → create app, permissions "Read and Write".
2. Put keys in `.env` (X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET).
3. The cron auto-detects keys and switches to the API automatically.

## Step 3: Enable posting
Nothing to do — posting is automatic once a session or keys exist.
To dry-run only, just don't log in: drafts queue up, nothing publishes.

## Kill switch
Tell me "stop posting" — I pause the posting cron. Always works.

## Money rails (Phase 2+)
- Newsletter: Beehiiv or Substack (Rajat creates, I write).
- Affiliate programs: AI tools with 20-40% recurring (Rajat approves each).
- X Ads Revenue Sharing: Premium ~$8/mo + 500 followers + 5M impressions/3mo.
