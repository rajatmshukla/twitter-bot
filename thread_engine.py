#!/usr/bin/env python3
"""Benchmark/teardown thread engine for @first_sauce_lab.

Rajat's call 2026-09-11, after Antigravity's reach audit: the account's root
posts were all second-hand news, which is the lowest-ranked content class on X
and pulled ~9 views each. This engine writes the opposite kind of post — a
first-person teardown of a model that actually dropped, in the first-sauce
voice, as a short thread.

THE RULE THAT MATTERS: this engine may only state numbers it actually fetched.
Every draft is checked token-by-token against the fact bundle; a number that
cannot be traced back to HuggingFace, OpenRouter or a page we fetched means
the thread is thrown away, not posted. There is no fallback that invents a
figure. Two failed checks and it refuses to post and says so.

Facts come from:
  - HuggingFace /api/models/<id>  (params, downloads, likes, license, created)
  - OpenRouter   /api/v1/models   (context length, pricing) when listed

Dry by default. `--post` is required to actually publish.

  python3 thread_engine.py --model Qwen/Qwen3-32B          # dry
  python3 thread_engine.py --auto --post                   # newest drop, live
  python3 thread_engine.py --model X --post --allow-unsafe-numbers
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.request

BOT = r"C:\Users\Rajat\twitter-bot"
SCRIPTS = r"C:\Users\Rajat\AppData\Local\hermes\scripts"
for p in (BOT, SCRIPTS):
    if p not in sys.path:
        sys.path.append(p)

import browser_guard  # noqa: E402
import browser_thread  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

POSTS_LOG = os.path.join(BOT, "logs", "posts.log")
OR_URL = "https://openrouter.ai/api/v1/models"
HF_MODEL = "https://huggingface.co/api/models/{}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}


def log(line):
    os.makedirs(os.path.dirname(POSTS_LOG), exist_ok=True)
    with open(POSTS_LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {line}\n")


def fetch_json(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ---------------------------------------------------------------- fact bundle

def hf_facts(model_id):
    """Whatever HuggingFace will tell us about this repo. No guesses."""
    try:
        d = fetch_json(HF_MODEL.format(model_id))
    except Exception as e:
        return {}, f"hf fetch failed: {type(e).__name__}"
    f = {"hf_id": d.get("id") or d.get("modelId")}
    for k in ("downloads", "likes", "pipeline_tag", "library_name", "createdAt",
              "lastModified", "private", "gated"):
        if d.get(k) is not None:
            f[f"hf_{k}"] = d[k]
    tags = d.get("tags") or []
    if tags:
        f["hf_tags"] = ", ".join(str(t) for t in tags[:25])
    card = d.get("cardData") or {}
    for k in ("license", "language", "base_model", "datasets"):
        if card.get(k) is not None:
            f[f"card_{k}"] = card[k]
    cfg = d.get("config") or {}
    for k in ("model_type", "architectures", "hidden_size", "num_hidden_layers",
              "max_position_embeddings", "vocab_size", "torch_dtype"):
        if cfg.get(k) is not None:
            f[f"cfg_{k}"] = cfg[k]
    saf = d.get("safetensors") or {}
    if saf.get("total") is not None:
        f["params_total"] = saf["total"]
    return f, None


def or_facts(model_id):
    """OpenRouter listing for the same model, if it is on the platform."""
    try:
        data = fetch_json(OR_URL)
    except Exception as e:
        return {}, f"openrouter fetch failed: {type(e).__name__}"
    slug = model_id.lower()
    base = slug.split("/")[-1]
    best = None
    for m in data.get("data", []):
        mid = str(m.get("id", "")).lower()
        if mid == slug or mid.endswith("/" + base) or base in mid:
            best = m
            break
    if not best:
        return {}, "not listed on openrouter"
    f = {"or_id": best.get("id"), "or_name": best.get("name")}
    if best.get("context_length") is not None:
        f["or_context_length"] = best["context_length"]
    top = best.get("top_provider") or {}
    if top.get("max_completion_tokens") is not None:
        f["or_max_completion_tokens"] = top["max_completion_tokens"]
    if top.get("context_length") is not None:
        f["or_provider_context"] = top["context_length"]
    pricing = best.get("pricing") or {}
    for k in ("prompt", "completion", "request", "image"):
        if pricing.get(k) not in (None, "", "0"):
            f[f"or_price_{k}"] = pricing[k]
    mod = best.get("architecture") or {}
    if mod.get("modality"):
        f["or_modality"] = mod["modality"]
    return f, None


def facts_text(f):
    """Flat text used for both the prompt and the number check."""
    lines = []
    for k, v in sorted(f.items()):
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        lines.append(f"{k}: {v}")
    return "\n".join(lines)


def add_intervals(facts):
    """Turn fetched timestamps into fetched intervals.

    "the first four days" is arithmetic on createdAt, not an invented figure,
    so the difference belongs in the bundle where the check can see it. Without
    this, the gate rejects a true statement for being derived.
    """
    def parse(s):
        try:
            return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except Exception:
            return None

    created = parse(facts.get("hf_createdAt"))
    modified = parse(facts.get("hf_lastModified"))
    now = datetime.datetime.now(datetime.timezone.utc)
    if created:
        facts["derived_days_since_created"] = (now - created).days
    if created and modified:
        facts["derived_days_created_to_modified"] = (modified - created).days
    return facts


def build_facts(model_id):
    hf, hf_err = hf_facts(model_id)
    orf, or_err = or_facts(model_id)
    facts = dict(hf)
    facts.update(orf)
    facts["source_model_id"] = model_id
    facts = add_intervals(facts)
    errs = [e for e in (hf_err, or_err) if e]
    return facts, errs


# ------------------------------------------------------------ candidate pick

def pick_newest():
    """Newest plausible frontier drop from HF trending."""
    import model_monitor as mm
    try:
        raw = mm.fetch(mm.TRENDING_URL)
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception as e:
        return None, f"trending fetch failed: {type(e).__name__}"
    # /api/trending returns {"recentlyTrending": [{"repoData": {...}}]}. Older
    # shapes were a flat list; handle both rather than guess (2026-09-11).
    items = data.get("recentlyTrending") if isinstance(data, dict) else data
    items = items or []
    best = None
    for it in items:
        if not isinstance(it, dict):
            continue
        rd = it.get("repoData") or it
        mid = rd.get("id") or rd.get("modelId") or ""
        if not mid:
            continue
        if not any(mid.startswith(a) for a in mm.AUTHOR_OK):
            continue
        if any(v.lower() in mid.lower() for v in mm.VARIANT_MARKERS):
            continue
        if (rd.get("downloads") or 0) < mm.MIN_DOWNLOADS:
            continue
        created = rd.get("lastModified") or rd.get("createdAt") or ""
        if best is None or created > best[1]:
            best = (mid, created)
    if not best:
        return None, "no eligible model found in trending"
    return best[0], None


# ------------------------------------------------------------------- drafting

SYSTEM = """You write for the X account @first_sauce_lab. Voice: one real
person who ships and breaks technology for a living. Dry, specific, unimpressed
by marketing. Never a hype man. Plain punctuation. No em dashes, no hashtags,
no links, no emoji, no "let's dive in", no throat clearing.

Write a SHORT THREAD of 3 posts about the model described in the FACTS below.

Post 1: the hook. What dropped, and the one number that actually matters to
someone who has to run it.
Post 2: the teardown. What the spec sheet buries, what it costs, or what a
builder will hit in production. Use only facts given.
Post 3: the verdict. A blunt, useful rule of thumb for whether to switch.

HARD RULES:
- Use ONLY the numbers and claims present in FACTS. Never invent, estimate,
  round into a new figure, or add a benchmark number that is not there.
- If FACTS are thin, write a shorter, blunter thread. Do not pad.
- Each post must be under 270 characters. One idea per post.
- Never use an em dash or an en dash. Use a comma, a colon or a full stop.
- Never use these phrases: "let's", "here's the", "tear it down", "dive in",
  "buckle up", "thread:", "quick thread". They read as hype-bot filler.
- No post may end with a question.

Return ONLY JSON: {"posts": ["...", "...", "..."]}"""


def _deepseek_call(prompt, max_tokens=900):
    import news_monitor as nm
    key = nm.load_deepseek_key()
    if not key:
        return None, "no DEEPSEEK_API_KEY available"
    out = nm._deepseek(prompt, max_tokens=max_tokens)
    if not out:
        return None, "deepseek returned nothing"
    return out, None


NUM_RE = re.compile(r"\d[\d,.]*")


def numbers_in(text):
    out = set()
    for m in NUM_RE.findall(text):
        t = m.strip(".,").replace(",", "")
        if t:
            out.add(t.lower())
    return out


def allowed_numbers(facts, model_id=""):
    """Every number the draft is allowed to state.

    Not only the raw figures: a faithful rounding is fine ("67.5K downloads"
    for 67550), and a number that comes from the model's own name is fine
    ("2B" in MiniCPM5-2B). What stays blocked is any magnitude we never
    fetched — which is exactly what an invented benchmark number looks like.
    """
    allow = set(numbers_in(facts_text(facts)))
    allow |= numbers_in(model_id or "")
    for n in list(allow):
        try:
            v = float(n)
        except ValueError:
            continue
        for mult, suf in ((1e3, "k"), (1e6, "m"), (1e9, "b")):
            for dp in (0, 1, 2):
                q = round(v / mult, dp)
                if q > 0:
                    allow.add(f"{q:g}{suf}")
                    allow.add(f"{q:g}")
    return allow


def has_symbol(p):
    """First emoji/symbol in the post, or None.

    Written as an ord() walk instead of a regex escape table so the rule
    survives being edited by anything that mangles backslashes. Curly quotes
    are allowed through; emoji, arrows, checkmarks and 🧵 are not.
    """
    for ch in p:
        o = ord(ch)
        if o > 0x2000 and not (0x2018 <= o <= 0x201F):
            return ch
    return None


BANNED_STARTS = ("let me", "here is the")
# Phrases that read as hype-bot wherever they appear, not just at the start.
BANNED_ANYWHERE = ("let's ", "tear it down", "dive in", "buckle up",
                   "quick thread", "thread:", "here's the")
COMPETITOR_RE = re.compile(r"\b(qwen|llama|gemma|mistral|gpt|claude|grok)\b", re.I)


def check_style(posts, facts, max_len=270):
    """Things that read as bot output, break the platform, or invent context."""
    hay = facts_text(facts).lower()
    bad = []
    for i, p in enumerate(posts, 1):
        if len(p) > max_len:
            bad.append(f"post {i} is {len(p)} chars, over the {max_len} limit")
        sym = has_symbol(p)
        if sym:
            bad.append(f"post {i} contains the symbol {sym!r}")
        if "#" in p:
            bad.append(f"post {i} has a hashtag")
        if "http" in p.lower():
            bad.append(f"post {i} has a URL")
        low = p.lower().lstrip()
        for b in BANNED_STARTS:
            if low.startswith(b):
                bad.append(f"post {i} opens with {b!r}")
        for b in BANNED_ANYWHERE:
            if b in low:
                bad.append(f"post {i} contains the hype phrase {b!r}")
        # Family name only: "Llama-architecture" is fine when the facts list
        # LlamaForCausalLM, while "Qwen2.5-3B" is an outside comparison.
        for m in COMPETITOR_RE.findall(p):
            if m.lower() not in hay:
                bad.append(f"post {i} names {m!r}, which is not in the facts")
    return bad


def sanitize(posts):
    """Mechanical repairs, applied before validation.

    Rajat's rule is no em or en dashes. Swapping one for a comma changes no
    fact and no meaning, so it is repaired instead of rejected — otherwise the
    engine burns its retries on a punctuation habit. Everything else (emoji,
    hashtags, invented numbers, outside comparisons) is still rejected.
    """
    out = []
    for p in posts:
        for code in (0x2014, 0x2013):
            dash = chr(code)
            p = p.replace(" " + dash + " ", ", ").replace(dash, ", ")
        p = re.sub(r"\s*,\s*,", ", ", p)
        # DeepSeek appends a thread emoji most runs. Dropping a symbol changes
        # no fact and no meaning, so repair it rather than burn a retry.
        p = "".join(ch for ch in p
                    if not (ord(ch) > 0x2000
                            and not (0x2018 <= ord(ch) <= 0x201F)))
        p = re.sub(r"[ \t]+", " ", p)
        p = re.sub(r"\s+([.,!?])", r"\1", p)
        out.append(p.strip())
    return out


def trim_to(p, limit=270):
    """Cut an over-long post back to the limit at a clean boundary.

    Called only when the model overshoots, which DeepSeek does most runs. This
    loses a clause; it never invents one, and it never cuts mid-word.
    """
    if len(p) <= limit:
        return p
    window = p[:limit]
    for sep in (". ", "! ", "? ", "; ", ", ", " "):
        i = window.rfind(sep)
        if i > limit * 0.5:
            cut = window[:i].rstrip()
            if sep.strip() in (".", "!", "?"):
                return cut + sep.strip()
            return cut + "."
    return window.rstrip() + "."


def check_numbers(posts, facts, model_id=""):
    """Numbers in the draft that cannot be traced back to the facts.

    Matching is by number, not by substring. An earlier version also accepted
    any number that appeared anywhere inside the flattened fact text, which let
    "7B+" through because some digit 7 existed in a dataset name. A magnitude
    the draft asserts has to be a magnitude we actually fetched.
    """
    allow = allowed_numbers(facts, model_id)
    bad = []
    for post in posts:
        for n in numbers_in(post):
            if n not in allow:
                bad.append(n)
    return sorted(set(bad))


def draft_thread(facts, model_id, allow_unsafe=False):
    base = (f"FACTS\n{facts_text(facts)}\n\n"
            f"TASK: write the 3-post teardown thread for {model_id}.")
    prompt, err = base, None
    bad = []
    for attempt in (1, 2, 3):
        out, err = _deepseek_call(prompt)
        if not out:
            return None, f"drafting failed: {err}"
        posts = _parse_posts(out)
        if not posts:
            prompt = (base + "\n\nYour last reply was not valid JSON. "
                      "Return ONLY {\"posts\": [...]}.")
            continue
        posts = [trim_to(p) for p in sanitize([p for p in posts if p.strip()])][:4]
        # Numbers are never waived. The flag relaxes only the style rules
        # (emoji, length, hype phrases), because those are taste. Publishing an
        # unverified figure is the one thing this engine must not do.
        problems = check_numbers(posts, facts, model_id)
        if not allow_unsafe:
            problems = problems + check_style(posts, facts)
        if not problems:
            return posts, None
        prompt = (base + "\n\nYour last draft had these problems:\n- "
                  + "\n- ".join(problems)
                  + "\nRewrite it. Every post under 270 characters. Only "
                    "numbers that appear in FACTS. No emoji, no hashtags, no "
                    "links, no comparisons to models not in FACTS.")
        bad = problems
    return None, ("draft kept breaking the rules after a rewrite "
                  f"({'; '.join(bad) or 'unparsed reply'}); refused to post "
                  "rather than publish an invented figure")


def _parse_posts(out):
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return []
    try:
        d = json.loads(m.group(0))
    except Exception:
        return []
    posts = d.get("posts") or []
    out = []
    for p in posts:
        # The model sometimes returns [{"text": "..."}] instead of ["..."],
        # which would otherwise post a Python dict repr as the tweet.
        if isinstance(p, dict):
            p = p.get("text") or p.get("post") or p.get("content") or ""
        out.append(str(p))
    return out


# ------------------------------------------------------------------- posting

def latest_post_id(page, handle):
    page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded", timeout=60_000)
    browser_thread.human_delay(3, 5)
    return page.evaluate("""(h) => {
        const a = [...document.querySelectorAll(`a[href*="/${h}/status/"]`)]
            .map(x => x.getAttribute('href'))
            .filter(Boolean);
        if (!a.length) return null;
        return a[0].split('/status/')[1].split('?')[0];
    }""", handle)


def as_id(result):
    """post_one's return shape has varied; take an id out of whatever it gives."""
    if isinstance(result, str) and result.isdigit():
        return result
    if isinstance(result, (list, tuple)):
        for x in result:
            if isinstance(x, str) and x.isdigit():
                return x
    return None


def post_thread(posts):
    if not browser_guard.hold("thread_engine"):
        print(f"stand down: profile busy ({browser_guard.busy_reason()})")
        return 0
    ids = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            r"C:\Users\Rajat\twitter-bot\browser-profile", headless=True,
            viewport={"width": 1280, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        verdict, detail = browser_guard.classify_session(page)
        if verdict != "ok":
            print(f"session {verdict} ({detail}); not posting")
            ctx.close()
            return 0 if verdict == "busy" else 2
        parent = None
        for i, text in enumerate(posts, 1):
            res = browser_thread.post_one(page, text, reply_to=parent)
            new_id = as_id(res)
            if not new_id:
                # post_one's contract has changed before; the profile is truth.
                new_id = latest_post_id(page, "first_sauce_lab")
            if not new_id:
                print(f"post {i} did not confirm; stopping the thread here")
                break
            # a chained reply must be newer than its parent
            if parent and new_id == parent:
                print(f"post {i} returned the parent id; stopping")
                break
            ids.append(new_id)
            parent = new_id
            print(f"posted {i}/{len(posts)}: {new_id}")
            browser_thread.human_delay(3, 6)
        ctx.close()
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="HF repo id")
    ap.add_argument("--auto", action="store_true", help="pick newest trending drop")
    ap.add_argument("--post", action="store_true", help="actually publish")
    ap.add_argument("--allow-unsafe-style", dest="allow_unsafe_numbers",
                    action="store_true",
                    help="relax the style rules only; the number check always runs")
    a = ap.parse_args()

    model_id = a.model
    if not model_id:
        if not a.auto:
            print("need --model <hf repo id> or --auto")
            return 1
        model_id, err = pick_newest()
        if not model_id:
            print(f"auto pick failed: {err}")
            return 1
        print(f"auto picked: {model_id}")

    facts, errs = build_facts(model_id)
    if not facts or len(facts) < 3:
        print(f"facts too thin for {model_id}: {errs}")
        return 1
    for e in errs:
        print(f"note: {e}")
    print(f"facts: {len(facts)} fields")
    print("--- fact bundle ---")
    print(facts_text(facts))
    print("--- end facts ---")

    posts, err = draft_thread(facts, model_id, allow_unsafe=a.allow_unsafe_numbers)
    if not posts:
        print(f"REFUSED: {err}")
        return 1

    print(f"\n--- thread draft ({len(posts)} posts) ---")
    for i, t in enumerate(posts, 1):
        print(f"[{i}] ({len(t)} chars) {t}")
    bad = check_numbers(posts, facts, model_id) + check_style(posts, facts)
    print(f"\nrules not satisfied: {bad or 'none'}")

    if not a.post:
        print("\nDRY RUN: nothing posted. Add --post to publish.")
        return 0
    if check_numbers(posts, facts, model_id):
        # Never waived, not even by the flag: this is the integrity gate.
        print("REFUSED: numbers not traceable to facts; nothing posted")
        return 1
    if bad and not a.allow_unsafe_numbers:
        print("REFUSED: untraceable numbers; not posting")
        return 1

    ids = post_thread(posts)
    if ids:
        log(f"THREAD POSTED [{model_id}] {' '.join(ids)}")
        print(f"\nthread live: https://x.com/first_sauce_lab/status/{ids[0]}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
