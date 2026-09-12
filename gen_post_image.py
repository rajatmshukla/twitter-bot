#!/usr/bin/env python3
"""Generate First Sauce Labs X post images — text-forward relatable cards.

Visual system: black canvas, neon-green accent (#3DFF6E), Bahnschrift headlines,
Consolas kickers/captions. Two layouts: 'card' (kicker + big line + subline)
and 'split' (two-panel meme with optional meter bar).

Usage: python3 gen_post_image.py            # generate whole batch
       python3 gen_post_image.py <name>     # generate one card by name
Output: C:/Users/Rajat/twitter-bot/assets/posts/<name>.png  (1600x900)
"""
import os, sys, textwrap
from PIL import Image, ImageDraw, ImageFont

ASSETS = r"C:\Users\Rajats\twitter-bot\assets"  # fixed below
ASSETS = r"C:\Users\Rajat\twitter-bot\assets"
OUT = os.path.join(ASSETS, "posts")
FONTS = r"C:\Windows\Fonts"
W, H = 1600, 900

BLACK = (10, 10, 10)
PANEL_A = (13, 13, 13)
PANEL_B = (17, 17, 17)
GREEN = (61, 255, 110)
WHITE = (245, 245, 245)
GRAY = (138, 138, 138)
DIM = (70, 70, 70)

def font(name, size):
    return ImageFont.truetype(os.path.join(FONTS, name), size)

HEADLINE_FONT = "arialbd.ttf"   # static font — variable fonts (bahnschrift) render
                                # unreliably under PIL 12 (drawn width != measured)
MONO_FONT = "consola.ttf"

def wrap(draw, text, fnt, max_w):
    words = text.split()
    lines, cur = [], ""
    for w_ in words:
        t = (cur + " " + w_).strip()
        if draw.textlength(t, font=fnt) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines

def draw_kicker(d, x, y, text):
    d.rectangle([x, y + 14, x + 18, y + 26], fill=GREEN)
    f = font(MONO_FONT, 34)
    d.text((x + 34, y), text, font=f, fill=GRAY)

def draw_caption(d, x, y, text, fill=DIM):
    f = font(MONO_FONT, 28)
    d.text((x, y), text, font=f, fill=fill)

def draw_headline(d, cx, y, lines, fnt, colors, max_w):
    # cx is the CENTER x; left edge = cx - width/2  (NOT (cx - width)/2)
    for i, (line, col) in enumerate(zip(lines, colors)):
        lw = d.textlength(line, font=fnt)
        d.text((cx - lw / 2, y), line, font=fnt, fill=col)
        y += fnt.size + 26
    return y

def card(name, kicker, lines, colors, sub, caption="first sauce labs — lab notes"):
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)
    draw_kicker(d, 90, 80, kicker)
    d.rectangle([90, H - 130, 90 + 300, H - 128], fill=GREEN)
    fnt = font(HEADLINE_FONT, 110)
    wrapped = []
    for ln, col in zip(lines, colors):
        wrapped.extend((w, col) for w in wrap(d, ln, fnt, 1350))
    y = draw_headline(d, W / 2, 300, [w for w, _ in wrapped],
                      fnt, [c for _, c in wrapped], 1350)
    if sub:
        f = font(MONO_FONT, 40)
        sw = d.textlength(sub, font=f)
        d.text(((W - sw) / 2, y + 60), sub, font=f, fill=GRAY)
    draw_caption(d, 90, H - 110, caption)
    return img

def split(name, left_lines, right_lines, meter=None, caption="first sauce labs — lab notes"):
    """meter: (label, pct) drawn under right panel as a fill bar."""
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W // 2, H], fill=PANEL_A)
    d.rectangle([W // 2, 0, W, H], fill=PANEL_B)
    d.rectangle([W // 2 - 3, 0, W // 2 + 3, H], fill=GREEN)
    draw_kicker(d, 70, 70, "me:") if False else None
    # left panel
    f = font(MONO_FONT, 34)
    d.text((70, 70), "me:", font=f, fill=GRAY)
    fnt = font(HEADLINE_FONT, 90)
    lw_max = W // 2 - 160
    y = 200
    for ln in left_lines:
        for w_ in wrap(d, ln, fnt, lw_max):
            d.text((70, y), w_, font=fnt, fill=WHITE)
            y += fnt.size + 18
    # right panel
    d.text((W // 2 + 70, 70), "the context window:", font=f, fill=GRAY)
    y = 200
    for ln in right_lines:
        for w_ in wrap(d, ln, fnt, lw_max):
            d.text((W // 2 + 70, y), w_, font=fnt, fill=WHITE)
            y += fnt.size + 18
    if meter:
        label, pct = meter
        bx, by = W // 2 + 70, 640
        bw, bh = lw_max, 46
        d.text((bx, by - 44), label, font=font(MONO_FONT, 32), fill=GRAY)
        d.rectangle([bx, by, bx + bw, by + bh], outline=DIM, width=3)
        d.rectangle([bx, by, bx + int(bw * pct), by + bh], fill=GREEN)
        pct_txt = f"{int(pct * 100)}%"
        pt = font(MONO_FONT, 30)
        d.text((bx + bw - d.textlength(pct_txt, font=pt) - 12, by + 6),
               pct_txt, font=pt, fill=BLACK)
    draw_caption(d, 90, H - 110, caption)
    return img

BATCH = {
    "context_window": lambda: split(
        "context_window",
        ["the prompt", "is fine.", "just post it."],
        ["wait.", "actually.", "hold on."],
        meter=("context: 196k / 200k tokens", 0.98),
    ),
    "hallucination": lambda: card(
        "hallucination",
        "LAB NOTES",
        ["it's not", "hallucinating.", "it's being", "creative."],
        [WHITE, GREEN, WHITE, GREEN],
        "— someone who ships anyway",
    ),
    "confident_nonsense": lambda: card(
        "confident_nonsense",
        "LAB NOTES",
        ["prompt: act as an expert.", "output: confident nonsense."],
        [GRAY, GREEN],
        "we've all been here.",
    ),
    "dinner": lambda: split(
        "dinner",
        ["me at dinner:", "i work in ai."],
        ["them:", "so you type", "into a computer?"],
    ),
    "we_move": lambda: card(
        "we_move",
        "FIRST SAUCE LABS",
        ["day 47.", "the algorithm", "sees me."],
        [WHITE, GRAY, GREEN],
        "we move.",
    ),
    "relatable_20260811": lambda: card(
        "relatable_20260811",
        "LAB NOTES",
        ["eval says", "98%.", "prod says", "no."],
        [WHITE, GREEN, WHITE, GREEN],
        "both numbers are real. somehow.",
    ),
    "relatable_20260812": lambda: card(
        "relatable_20260812",
        "LAB NOTES",
        ["agent status:", "almost done.", "almost done."],
        [GRAY, GREEN, GREEN],
        "day three. still going.",
    ),
    "relatable_20260813": lambda: card(
        "relatable_20260813",
        "LAB NOTES",
        ["the gpu bill arrived.", "the model got smarter.", "i got poorer."],
        [WHITE, GREEN, WHITE],
        "the numbers check out. mine don't.",
    ),
    "relatable_20260814": lambda: card(
        "relatable_20260814",
        "LAB NOTES",
        ["i set temperature", "to 0.", "it still had", "opinions."],
        [WHITE, GREEN, WHITE, GREEN],
        "determinism is a lie.",
    ),
    "relatable_20260815": lambda: card(
        "relatable_20260815",
        "LAB NOTES",
        ["me: the bug", "is in the prompt.", "model: you're right.", "it's the prompt."],
        [WHITE, GREEN, WHITE, GREEN],
        "the bug was in the code. it always was.",
    ),
    "relatable_20260816": lambda: card(
        "relatable_20260816",
        "LAB NOTES",
        ["one more example", "in the prompt.", "that'll fix it."],
        [WHITE, GREEN, WHITE],
        "it did not fix it.",
    ),
    "relatable_20260817": lambda: card(
        "relatable_20260817",
        "LAB NOTES",
        ["prompt: be concise.", "output: an essay", "on being concise."],
        [GRAY, GREEN, WHITE],
        "the model has thoughts on brevity.",
    ),
    "relatable_20260818": lambda: card(
        "relatable_20260818",
        "LAB NOTES",
        ["the model", "apologized.", "then doubled", "down."],
        [WHITE, GREEN, WHITE, GREEN],
        "sorry doesn't fix the json.",
    ),
    "relatable_20260819": lambda: card(
        "relatable_20260819",
        "LAB NOTES",
        ["it worked", "yesterday.", "the model", "updated itself."],
        [WHITE, GREEN, WHITE, GREEN],
        "nothing is pinned. nothing is safe.",
    ),
}

def verify_bounds(path, border=26):
    """Check that no ink sits within `border` px of the canvas edge (overflow/clip).
    Known allowed edge elements: green divider of split layouts (center, not edge)."""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    bg = {(10, 10, 10), (13, 13, 13), (17, 17, 17)}
    hits = []
    for y in range(h):
        for x in list(range(border)) + list(range(w - border, w)):
            if px[x, y] not in bg:
                hits.append((x, y, px[x, y]))
    for x in range(w):
        for y in list(range(border)) + list(range(h - border, h)):
            if px[x, y] not in bg:
                hits.append((x, y, px[x, y]))
    return hits

def main():
    os.makedirs(OUT, exist_ok=True)
    names = sys.argv[1:] or list(BATCH)
    ok = True
    for n in names:
        if n not in BATCH:
            print(f"unknown card: {n}")
            continue
        img = BATCH[n]()
        p = os.path.join(OUT, f"{n}.png")
        img.save(p)
        hits = verify_bounds(p)
        # green divider at center (x=797..800) is by design; ignore it
        real = [h for h in hits if not (795 <= h[0] <= 805)]
        status = "CLEAN" if not real else f"EDGE HITS {len(real)} e.g. {real[:3]}"
        if real:
            ok = False
        print(f"{status}  {p}")
    if not ok:
        print("!! LAYOUT CHECK FAILED — inspect before posting")
        sys.exit(2)

if __name__ == "__main__":
    main()
