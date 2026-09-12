#!/usr/bin/env python3
"""Offline check of the new filter/gate logic. No browser, no posting, no network.

Covers the paths that decide whether a real person gets an auto-reply, which is
the part worth being sure about before this runs unattended.
"""
import sys, os, datetime
sys.path.append(r"C:\Users\Rajat\twitter-bot")
import mentions_guy as mg
import reply_guy_direct as rgd

fails = []


def check(label, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: got {got!r} want {want!r}")
    if not ok:
        fails.append(label)


now = datetime.datetime.now(datetime.timezone.utc)
def iso(hours_ago):
    return (now - datetime.timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")

print("== freshness window ==")
check("2h old is answerable", mg.fresh(iso(2)), True)
check("60h old is answerable", mg.fresh(iso(60)), True)
check("5 days old is stale", mg.fresh(iso(120)), False)
check("unparseable is stale", mg.fresh(""), False)

print("\n== junk / spam filter ==")
check("plain question passes", bool(mg.is_junk("which model would you use for this")), False)
check("crypto shill blocked", bool(mg.is_junk("check my dm for 100x crypto")), True)
check("follow-back bait blocked", bool(mg.is_junk("follow me back and I follow you")), True)
check("telegram link blocked", bool(mg.is_junk("join t.me/airdrop now")), True)
check("bare handle is junk", bool(mg.is_junk("@first_sauce_lab")), True)
check("handle + real words passes", bool(mg.is_junk("@first_sauce_lab which model?")), False)
check("two bare handles are junk", bool(mg.is_junk("@first_sauce_lab @someone")), True)

print("\n== thanks-opener gate (the draft that failed review) ==")
for bad in ["Thanks. I try not to overrate them myself.",
            "Thank you, appreciate it!",
            "thx for the follow",
            "Great point, though I would add that scale is not everything.",
            "Means a lot coming from you."]:
    check(f"rejects {bad[:34]!r}", bool(mg.THANKS_OPENERS.match(bad)), True)
for good in ["It gets very real when you're the last one holding the pager.",
             "The latency is the part nobody budgets for.",
             "That is the version I keep failing at too."]:
    check(f"allows {good[:34]!r}", bool(mg.THANKS_OPENERS.match(good)), False)

print("\n== news pool quality filter ==")
check("english passes", rgd._news_quality_ok("OpenAI brought back Paul Christiano to the board."), True)
check("cjk blocked", rgd._news_quality_ok("DeepSeek继续带动大模型创新，价格地板被砸穿了"), False)
check("link drop blocked", rgd._news_quality_ok("GPT-6 vs GPT-5.6 https://t.co/abc"), False)
check("empty blocked", rgd._news_quality_ok(""), False)

print("\n== headline -> search phrase ==")
t = ["OpenAI brings back Paul Christiano to its board", "Introducing GPT-5.6 Sol",
     "Anthropic ships a new eval suite", "Weekly roundup of AI news"]
want = {"OpenAI brings back Paul Christiano to its board": "Paul Christiano",
        "Introducing GPT-5.6 Sol": "GPT-5.6 Sol"}
for title in t:
    got = rgd._search_phrase(title, ["gpt-5.6", "claude", "gemini"])
    print(f"   {title!r} -> {got!r}")
    if title in want:
        check(f"phrase for {title[:28]!r}", got, want[title])
check("single-cap headline yields no query", rgd._search_phrase("Anthropic ships a new eval suite", []), "")

print("\n== browser lock round-trip ==")
import reply_guy as rg
rg.release_browser_lock()
check("lock free initially", os.path.exists(rg.LOCK_FILE), False)
check("acquire succeeds", rg.acquire_browser_lock(), True)
check("second acquire blocked", rg.acquire_browser_lock(), False)
rg.release_browser_lock()
check("lock released", os.path.exists(rg.LOCK_FILE), False)

print(f"\n{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
