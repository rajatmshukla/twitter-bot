# twitter-bot

Automation for the [@first_sauce_lab](https://x.com/first_sauce_lab) X account.

Most of this code drives the real X web UI with Playwright against a persistent,
logged-in Chromium profile. The official API is used for plain single posts and
nothing else, and only when keys are present.

## Why the browser

Write access to X's API costs about $100 a month. The web UI path costs nothing
and reaches things the API does not: quote posts, reposts, threads, and the
notifications timeline. The trade is fragility. Selectors break when X ships a
redesign, and X can force a re-login at any time.

## How a run works

1. `browser_guard.py` takes a lock on the profile so two processes cannot drive
   one session at once, then classifies what the page actually shows. Every
   engine asks it first.
2. The engine opens the persistent profile, does its work, and closes.
3. Anything that publishes writes a line to `logs/`.

## Layout

| Area | Files |
| --- | --- |
| Session | `browser_login.py`, `browser_guard.py` |
| Publish | `post.py` (API v2, dry-run by default), `post_next.py` (FIFO consumer of `drafts/`), `browser_post.py`, `browser_thread.py`, `browser_share.py` (repost and quote), `post_gemini37.py` |
| Replies | `reply_guy.py`, `reply_guy_direct.py` |
| Mentions | `mentions_guy.py` |
| Growth | `follow_accounts.py`, `unfollow_maintenance.py`, `thread_engine.py` |
| Read and inspect | `profile_audit.py`, `profile_probe.py`, `profile_stats.py`, `scrape_notifications.py`, `scrape_replies.py`, `list_following.py`, `delete_tweets.py`, `pin_and_scrape.py` |
| Media | `gen_post_image.py`, `render_profile_assets.py` |
| Draft queue | `queue.py`, `follow_pool.json` |
| Probes and tests | `scripts/` |

`scripts/` holds one-shot probes, diagnostics, and the test suite. Each file's
module docstring says what it does and what it needs, so read the top of a file
before running it.

## Setup

1. Python 3.11 or newer. Tested on 3.12.
2. `pip install -r requirements.txt` then `playwright install chromium`.
3. Log in once, yourself:

   ```
   python3 browser_login.py
   ```

   A real Chromium window opens at x.com. Type your own credentials. The code
   never sees them. Close the window once your home timeline renders. The session
   is saved to `browser-profile/`, outside version control.
4. Verify:

   ```
   python3 browser_post.py --check
   ```

   A working session prints the confirmation. Without one, the engines refuse to
   run rather than posting into a logged-out page.

For API publishing instead, put `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`,
and `X_ACCESS_SECRET` in `.env`. `post.py` picks them up.

## What is not in this repository

`browser-profile/` (the live session: cookies and local storage), `.env` (API
keys), `logs/`, `drafts/`, and `tmp/`. All are in `.gitignore`. No credential
appears anywhere in the tracked files.

## Tests

Offline, safe to run anywhere:

```
python3 scripts/test_dead_move_gate.py
python3 scripts/test_engagement_filters.py
python3 scripts/test_run_budget.py
python3 scripts/check_thread.py
```

These drive the live account. Each one says so in its own docstring, and
`test_share_live.py` really does repost and quote on the account:

```
scripts/test_mentions_contention.py
scripts/test_session_verdicts.py
scripts/test_share_lane.py
scripts/test_share_live.py
scripts/verify_share_post.py
```

`scripts/verify_comment_only.py` compares a tracked file's syntax tree against a
git revision with docstrings stripped. It proves a documentation pass changed
nothing else.

## Two modules live outside this repository

A few scripts import `news_monitor` and `model_monitor`, which sit in the
operator's Hermes scripts directory rather than here. Those scripts will not run
from a fresh clone until those two modules are pointed at a path you control.

## The reply gate

`reply_guy_direct.py` screens every drafted reply against `DEAD_MOVE_RE`, a
pattern that refuses a family of stock constructions, the kind where a phrase
gets named and then described as doing work. `scripts/test_dead_move_gate.py`
measures it: the refused set, the sentences that must survive, and the
false-positive rate against the published corpus. Tightening that pattern is
deliberate. Loosening it is a decision, not a cleanup.
