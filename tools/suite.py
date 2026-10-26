"""Local stress test: every design x a catalogue of real platform formats.

    python -m tools.suite                 # all designs in input/ and tests/designs/
    python -m tools.suite level2          # only designs whose filename contains "level2"

Writes output/suite/<design>__sheet.png (a labelled contact sheet) and
prints any hard-rule violations (ids, off-canvas, image proportions).
"""

import io
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from engine.main import self_check
from engine.resize import resize
from renderer.converter.convert import _get_renderer, convert_chameleon_to_crello

CATALOGUE = [
    ("Instagram Post", 1080, 1080),
    ("Instagram Portrait", 1080, 1350),
    ("Instagram Story", 1080, 1920),
    ("Pinterest Pin", 1000, 1500),
    ("A4 Poster", 1240, 1754),
    ("Facebook Post", 1200, 630),
    ("X Post", 1600, 900),
    ("YouTube Thumbnail", 1280, 720),
    ("Twitter Header", 1500, 500),
    ("Facebook Cover", 820, 312),
    ("Web Banner", 1600, 400),
    ("LinkedIn Banner", 1584, 396),
    ("Billboard", 970, 250),
    ("Leaderboard", 728, 90),
    ("Mobile Banner", 320, 50),
    ("Medium Rectangle", 300, 250),
    ("Half Page", 300, 600),
    ("Wide Skyscraper", 160, 600),
]

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "brand_kit" / "assets"
FONTS = ROOT / "brand_kit" / "fonts"


def render(design, max_size):
    r = _get_renderer(str(FONTS))
    ex, _ = convert_chameleon_to_crello(design, r.font_manager, str(ASSETS))
    return Image.open(io.BytesIO(r.render(ex, max_size=max_size, format="png"))).convert("RGB")


def hard_rules(source, out):
    probs = self_check(source, out)
    src = {e["id"]: e for e in source["elements"]}
    W, H = out["canvas"]["width"], out["canvas"]["height"]
    for e in out["elements"]:
        s = src.get(e["id"])
        if not s:
            continue
        if e["type"] == "image" and s.get("fit", "contain") != "cover" and not (
                e["width"] >= W - 1 and e["height"] >= H - 1):
            a0 = s["width"] / max(s["height"], 1e-6)
            a1 = e["width"] / max(e["height"], 1e-6)
            if abs(a1 - a0) / a0 > 0.05:
                probs.append(f"{e['id']}: aspect {a0:.3f} -> {a1:.3f}")
        if e["type"] == "text":
            if e.get("content") != s.get("content") or e.get("font_family") != s.get("font_family"):
                probs.append(f"{e['id']}: content/font_family changed")
            if not isinstance(e.get("font_size"), int):
                probs.append(f"{e['id']}: non-integer font_size")
    return probs


def main():
    filt = sys.argv[1] if len(sys.argv) > 1 else ""
    files = sorted((ROOT / "input").glob("*.json")) + sorted((ROOT / "tests" / "designs").glob("*.json"))
    files = [f for f in files if filt in f.name]
    outdir = ROOT / "output" / "suite"
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype(str(FONTS / "SolsticeSans-Bold.ttf"), 14)
    except Exception:
        font = ImageFont.load_default()
    total_bad = 0
    for f in files:
        task = json.loads(f.read_text())
        source = task["source"]
        tiles = []
        t0 = time.time()
        for name, w, h in CATALOGUE:
            out = resize(source, {"width": w, "height": h})
            probs = hard_rules(source, out)
            if probs:
                total_bad += 1
                print(f"  [{f.stem} -> {name} {w}x{h}] " + "; ".join(probs))
            tiles.append((f"{name} {w}x{h}", render(out, 360), bool(probs)))
        dt = time.time() - t0
        src_img = render(source, 360)
        tiles.insert(0, ("SOURCE %dx%d" % (source["canvas"]["width"], source["canvas"]["height"]), src_img, False))
        cols, cell = 5, 380
        rows = (len(tiles) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * cell, rows * (cell + 24)), (236, 232, 226))
        d = ImageDraw.Draw(sheet)
        for i, (label, img, bad) in enumerate(tiles):
            cx, cy = (i % cols) * cell, (i // cols) * (cell + 24)
            img.thumbnail((cell - 20, cell - 20))
            sheet.paste(img, (cx + (cell - img.width) // 2, cy + 24 + (cell - 20 - img.height) // 2))
            d.text((cx + 10, cy + 4), label, fill=(200, 30, 30) if bad else (40, 30, 50), font=font)
        sheet.save(outdir / f"{f.stem}__sheet.png")
        print(f"{f.stem}: {len(CATALOGUE)} targets in {dt:.1f}s -> output/suite/{f.stem}__sheet.png")
    print("violations:", total_bad)


if __name__ == "__main__":
    main()
