"""The self-critic: scores candidate layouts the way the rubric does.

Stage 1 (geometric, every candidate):
    readability  - each text's size vs. what its role needs on this canvas
    hierarchy    - the source's type order survives (headline stays biggest)
    imagery      - hero and logo keep a presence comparable to the source
    collisions   - unintended overlaps between content units
    composition  - content fills the canvas, balanced around the DNA axis
    faithfulness - cost of every element that had to step aside

Stage 2 (pixels, top candidates only):
    the candidate is rendered with the official renderer (text off) and,
    for every text, the real pixels underneath are measured for WCAG
    contrast against the text colour and for busyness.
"""

import math
from pathlib import Path

from . import fonts as F
from .geometry import area, center, inter, union

ROLE_W = {"headline": 3.0, "cta": 2.0, "cta_text": 2.0, "body": 1.5, "badge": 0.8,
          "sticker": 0.6, "legal": 0.35, "group": 1.0}
DROP_COST = {"legal": 0.3, "sticker": 0.45, "badge": 0.35, "group": 0.7, "body": 1.1,
             "hero": 1.8, "accent": 0.05, "decoration": 0.05, "panel": 0.1}
CONTENT_SKIP = {"decoration", "panel", "background"}


def geometric(plan, ctx):
    A, W, H = ctx.A, ctx.W, ctx.H
    pen = 0.0
    parts = {}

    # readability ---------------------------------------------------------
    num = den = 0.0
    sizes = []
    for eid, uid, role, fs, overflow, lines in plan.texts:
        if uid in plan.dropped:
            continue
        if role in ("cta", "badge", "sticker") and lines > 1:
            pen += 1.2 * (lines - 1)
        if role in ("headline", "body") and lines > 1:
            # typographic rag: avoid one-word-per-line columns and headlines
            # that balloon far past their source line count
            el = A.els[eid]
            words = len(el["content"].split())
            if words >= 3 and words / lines < 2.0:
                pen += 0.6 if role == "headline" else 0.3
            if role == "headline":
                src_lines = F.layout(el, el["font_size"], el["width"]).lines
                if lines > 2 * src_lines + 1:
                    pen += 0.25 * (lines - 2 * src_lines - 1)
        src = A.els[eid]["font_size"]
        target = max(ctx.floor(role, src) * 1.25, src * ctx.S * 0.9)
        r = min(1.0, fs / max(target, 1.0))
        w = ROLE_W.get(role, 1.0)
        num += w * r ** 1.5
        den += w
        if overflow:
            pen += 3.0
        sizes.append((src, fs, role))
    read = num / den if den else 1.0
    parts["read"] = read

    # hierarchy -----------------------------------------------------------
    hier = 0.0
    for i in range(len(sizes)):
        for j in range(len(sizes)):
            si, fi, ri = sizes[i]
            sj, fj, rj = sizes[j]
            if "legal" in (ri, rj):
                continue
            if si > sj * 1.08 and fi < fj:
                hier += 0.6
    pen += hier
    parts["hier"] = -hier

    # imagery -------------------------------------------------------------
    def presence(role, want):
        src = sum(area(u.vis) for u in A.flow if u.role == role) / (A.W0 * A.H0)
        if src <= 0:
            return 1.0
        out = sum(area(r) for uid, rl, r in plan.vis if rl == role) / (W * H)
        return min(1.0, out / (want * src))

    hero = presence("hero", 0.75)
    logo = presence("logo", 0.55)
    for uid, rl, r in plan.vis:
        if rl == "logo" and uid not in plan.dropped and r[3] < ctx.logo_min_h * 0.98:
            pen += 2.0 * (1 - r[3] / ctx.logo_min_h)
    parts["hero"], parts["logo"] = hero, logo

    # brand proportion: logo vs headline keeps roughly the source relation
    head = next((u for u in A.flow if u.role == "headline"), None)
    lg = next((u for u in A.flow if u.role == "logo"), None)
    if head and lg and lg.uid not in plan.dropped:
        hfs = next((t[3] for t in plan.texts if t[1] == head.uid), None)
        lh = next((r[3] for uid, rl, r in plan.vis if uid == lg.uid), None)
        if hfs and lh:
            r = (lh / hfs) / max(lg.vis[3] / head.text["font_size"], 1e-6)
            if r > 1.6:
                pen += 1.2 * (r / 1.6 - 1)
                parts["brand"] = -round(1.2 * (r / 1.6 - 1), 2)

    # collisions ----------------------------------------------------------
    content = [(uid, role, r) for uid, role, r in plan.vis if role not in CONTENT_SKIP and uid not in plan.dropped]
    coll = 0.0
    for i in range(len(content)):
        for j in range(i + 1, len(content)):
            ui, ri, a = content[i]
            uj, rj, b = content[j]
            if ui == uj or frozenset((ui, uj)) in plan.intended:
                continue
            ov = inter(a, b)
            if ov <= 0:
                continue
            frac = ov / max(1.0, min(area(a), area(b)))
            if frac > 0.01:
                coll += 4.0 * frac + 1.0
    coll += 1.5 * plan.collisions
    pen += coll
    parts["coll"] = -coll

    # composition ---------------------------------------------------------
    rects = [r for _, _, r in content]
    comp = 0.0
    fill = 1.0
    U = union(rects) if rects else None
    if U is not None:
        iw, ih = W - 2 * ctx.mx, H - 2 * ctx.my
        fill = min(1.0, U[2] / (0.8 * iw)) * min(1.0, U[3] / (0.8 * ih))
        cx, cy = center(U)
        if A.align == "center":
            comp += 2.0 * abs(cx - W / 2) / W
        comp += 1.5 * abs(cy - H / 2) / H
        for r in rects:
            if r[0] < -1 or r[1] < -1 or r[0] + r[2] > W + 1 or r[1] + r[3] > H + 1:
                comp += 2.0
            elif min(r[0], r[1], W - r[0] - r[2], H - r[1] - r[3]) < 0.35 * ctx.m:
                comp += 0.15
    pen += comp
    parts["fill"], parts["comp"] = fill, -comp

    # faithfulness --------------------------------------------------------
    drop = 0.0
    for u in A.units:
        if u.uid in plan.dropped and u.role != "missing":
            drop += DROP_COST.get(u.role, 0.5)
    pen += drop
    parts["drop"] = -drop

    plan.score = 3.0 * read + 1.6 * hero + 1.2 * logo + 1.0 * fill - pen
    plan.parts = parts
    return plan.score


# ---------------------------------------------------------------------------
# Pixel stage
# ---------------------------------------------------------------------------

def _rgba(color):
    try:
        inner = str(color).split("(", 1)[1].rstrip(")")
        p = [float(v) for v in inner.split(",")]
        return p[0], p[1], p[2], (p[3] if len(p) > 3 else 1.0)
    except (IndexError, ValueError):
        return 0.0, 0.0, 0.0, 1.0


def _lum(r, g, b):
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


_RENDER = None


def _renderer():
    global _RENDER
    if _RENDER is None:
        from renderer.converter.convert import _get_renderer

        root = Path(__file__).resolve().parents[2]
        _RENDER = (_get_renderer(str(root / "brand_kit" / "fonts")), str(root / "brand_kit" / "assets"))
    return _RENDER


def render_backdrop(design, max_size=512):
    """Render everything except text, with the official renderer."""
    import io

    from PIL import Image

    from renderer.converter.convert import convert_chameleon_to_crello

    renderer, assets_dir = _renderer()
    example, _ = convert_chameleon_to_crello(design, renderer.font_manager, assets_dir)
    png = renderer.render(example, max_size=max_size, render_text=False, format="png")
    return Image.open(io.BytesIO(png)).convert("RGB")


def pixel(plan, design, ctx):
    """Adjust plan.score with contrast / busyness measured on real pixels."""
    from PIL import ImageStat

    try:
        img = render_backdrop(design)
    except Exception:
        return plan.score
    s = img.width / ctx.W
    els = {e["id"]: e for e in design["elements"]}
    vis = {uid: r for uid, role, r in plan.vis}
    pen = 0.0
    worst = 99.0
    for eid, uid, role, fs, _, _ in plan.texts:
        if uid in plan.dropped or eid not in els:
            continue
        e = els[eid]
        if role in ("cta", "badge", "sticker"):
            # label sits on its own button shape: measure the button
            g = plan.geom[eid]
            rect = (g["x"], g["y"], g["w"], g["h"])
        else:
            rect = vis.get(uid) or (e["x"], e["y"], e["width"], e["height"])
        x0, y0 = max(0, int(rect[0] * s)), max(0, int(rect[1] * s))
        x1, y1 = min(img.width, int((rect[0] + rect[2]) * s) + 1), min(img.height, int((rect[1] + rect[3]) * s) + 1)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        crop = img.crop((x0, y0, x1, y1))
        st = ImageStat.Stat(crop)
        br, bg_, bb = st.mean[:3]
        busy = sum(st.stddev[:3]) / 3.0 / 255.0
        tr, tg, tb, ta = _rgba(e.get("color"))
        # text alpha blends toward the backdrop
        tr, tg, tb = (tr * ta + br * (1 - ta), tg * ta + bg_ * (1 - ta), tb * ta + bb * (1 - ta))
        l1, l2 = _lum(tr, tg, tb), _lum(br, bg_, bb)
        cr = (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)
        worst = min(worst, cr)
        need = 3.0 if fs >= 24 else 4.5
        w = ROLE_W.get(role, 1.0)
        if cr < need:
            pen += w * 0.6 * (need - cr) / need
        if busy > 0.14:
            pen += w * 1.5 * (busy - 0.14)
    plan.parts["contrast_min"] = round(worst, 2)
    plan.parts["pixel_pen"] = -round(pen, 3)
    plan.score -= pen
    return plan.score
