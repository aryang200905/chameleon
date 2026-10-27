"""Independent output verifier - grades engine output with the *renderer's*
own code, not the engine's mirror of it.

    python -m tools.verify            # every design x every catalogue format
    python -m tools.verify level3     # filter designs by filename

Checks, per output:
    schema      validates against chameleon_design_schema_v0.9.json
    overflow    visible text whose wrapped ink (renderer wrap + metrics,
                letter_spacing included) spills past its box
    collision   visible content that overlaps when it should not
                (text/text, text/image, partial text/opaque shape, image/image)
    tiny        visible text rendered below a legible size for the canvas
    mutation    resize() modified its input
    determinism two runs give different output
"""

import copy
import json
import sys
from pathlib import Path

import jsonschema

from engine.chameleon import assets as AS
from engine.resize import resize
from renderer.converter.convert import _get_renderer
from renderer.converter.wrap import char_widths, wrap_text
from tools.suite import CATALOGUE

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "chameleon_design_schema_v0.9.json").read_text())
FM = _get_renderer(str(ROOT / "brand_kit" / "fonts")).font_manager


def _alpha(color):
    try:
        p = str(color).split("(", 1)[1].rstrip(")").split(",")
        return float(p[3]) if len(p) > 3 else 1.0
    except (IndexError, ValueError):
        return 1.0


def _style(e):
    bold = str(e.get("font_weight") or "regular").lower() == "bold"
    italic = e.get("font_style") == "italic"
    weight = "bold" if bold else "regular"
    style = "bolditalic" if bold and italic else "bold" if bold else "italic" if italic else "regular"
    return weight, style


def text_ink(e):
    """(rect, n_lines, widest_line) exactly as the renderer lays the text out."""
    import skia

    weight, style = _style(e)
    size = int(e["font_size"])
    content = str(e.get("content", ""))
    widths = char_widths(FM, e["font_family"], weight, style, float(e["font_size"]), content)
    wrapped = wrap_text(content, widths, float(e["width"]))
    ls = float(e.get("letter_spacing") or 0)
    font = skia.Font(skia.Typeface.MakeFromData(FM.lookup(e["font_family"], weight, style)), size)
    m = font.getMetrics()
    lines = wrapped.split("\n")
    lw = []
    for ln in lines:
        g = font.textToGlyphs(ln)
        lw.append(sum(font.getWidths(g)) + ls * max(0, len(g) - 1))
    widest = max(lw) if lw else 0.0
    lh = float(e.get("line_height") or 1.2)
    h = (len(lines) - 1) * size * lh + (m.fDescent - m.fAscent)
    align = e.get("text_align", "left")
    off = {"center": (e["width"] - widest) / 2, "right": e["width"] - widest}.get(align, 0.0)
    return (e["x"] + off, e["y"], widest, h), len(lines), widest


def visible(e):
    if float(e.get("opacity", 1) if e.get("opacity") is not None else 1) <= 0.01:
        return False
    if e["type"] == "text":
        return _alpha(e.get("color")) > 0.01 and str(e.get("content", "")).strip() != ""
    if e["type"] == "shape":
        return _alpha(e.get("fill_color")) * float(e.get("opacity", 1) or 1) > 0.01
    return AS.exists(e.get("asset_id"))


def inter(a, b):
    ix = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    iy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return ix * iy if ix > 0 and iy > 0 else 0.0


def check(source, out):
    probs = []
    try:
        jsonschema.validate(out, SCHEMA)
    except jsonschema.ValidationError as exc:
        probs.append(f"schema: {exc.message[:120]}")
    # overlaps the designer already had (a price sticker on its product)
    # are intentional layering, not collisions
    intended = {frozenset(p[:2]) for p in _collisions(source, [])}
    for pair in _collisions(out, probs):
        if frozenset(pair[:2]) not in intended:
            probs.append("collision {}({}) x {}({}) {:.0%}".format(*pair[2:]))
    return probs


def _collisions(design, probs):
    W, H = design["canvas"]["width"], design["canvas"]["height"]
    els = [e for e in design["elements"] if visible(e)]
    rects, pairs = [], []
    for e in els:
        if e["type"] == "text":
            if e.get("rotation"):
                continue
            r, n, widest = text_ink(e)
            if widest > e["width"] + 1:
                probs.append(f"overflow-x {e['id']}: line {widest:.0f}px > box {e['width']:.0f}px")
            if r[3] > e["height"] + 1:
                probs.append(f"overflow-y {e['id']}: ink {r[3]:.0f}px > box {e['height']:.0f}px ({n} lines)")
            if r[1] + r[3] > H + 1 or r[0] < -1 or r[0] + r[2] > W + 1:
                probs.append(f"text-off-canvas {e['id']}")
            floor = max(7, 0.012 * min(W, H))
            if e["font_size"] < floor:
                probs.append(f"tiny {e['id']}: {e['font_size']}px on {W}x{H}")
            rects.append((e, "text", r))
        elif e["type"] == "image":
            if e["width"] >= W - 1 and e["height"] >= H - 1:
                continue
            rects.append((e, "image", AS.visible_rect(e, e["x"], e["y"], e["width"], e["height"])))
        else:
            if e["width"] >= W - 1 and e["height"] >= H - 1:
                continue
            solid = _alpha(e.get("fill_color")) * float(e.get("opacity", 1) or 1) >= 0.5
            if solid and not e.get("rotation"):
                rects.append((e, "shape", (e["x"], e["y"], e["width"], e["height"])))
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            (a, ka, ra), (b, kb, rb) = rects[i], rects[j]
            ov = inter(ra, rb)
            if ov <= 0:
                continue
            small = min(ra[2] * ra[3], rb[2] * rb[3]) or 1.0
            if ov / small < 0.03:
                continue
            same_group = a.get("group_id") and a.get("group_id") == b.get("group_id")
            if {ka, kb} == {"text", "shape"}:
                t, s = (ra, rb) if ka == "text" else (rb, ra)
                if same_group or inter(t, s) >= 0.97 * t[2] * t[3]:
                    continue  # label inside its button / panel
            elif {ka, kb} == {"shape"} or ({ka, kb} == {"image", "shape"}):
                continue  # layered graphics are allowed
            pairs.append((a["id"], b["id"], a["id"], ka, b["id"], kb, ov / small))
    return pairs


def main():
    filt = sys.argv[1] if len(sys.argv) > 1 else ""
    files = sorted((ROOT / "input").glob("*.json")) + sorted((ROOT / "tests" / "designs").glob("*.json"))
    files = [f for f in files if filt in f.name]
    total = bad = 0
    for f in files:
        source = json.loads(f.read_text())["source"]
        for name, w, h in CATALOGUE:
            snap = copy.deepcopy(source)
            out = resize(source, {"width": w, "height": h})
            probs = check(source, out)
            if source != snap:
                probs.append("mutation: resize() changed its input")
            if json.dumps(resize(source, {"width": w, "height": h})) != json.dumps(out):
                probs.append("determinism: second run differs")
            total += 1
            if probs:
                bad += 1
                print(f"[{f.stem} -> {name} {w}x{h}]")
                for p in probs:
                    print("    " + p)
    print(f"{total} outputs checked, {bad} with findings")


if __name__ == "__main__":
    main()
