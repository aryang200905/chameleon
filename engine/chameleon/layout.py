"""Candidate layouts.

Every template "moves the furniture" instead of stretching it:

    stack  - one centred (or DNA-aligned) column, images flex to fill
    split  - media on one side, the text column on the other
    row    - banner: logo | hero | text column | CTA in a single line

Each template is parameterised by a type scale `k` (font sizes relative to
the source), a drop set (lowest-priority units that step aside when the
canvas is too small) and a few variant switches. The engine generates many
candidates and lets the critic pick.
"""

import math
from dataclasses import dataclass, field

from . import assets as AS
from . import fonts as F
from .geometry import area, center, inter, rot_aabb, union

FLOOR_ABS = {"headline": 12, "body": 9, "cta_text": 10, "cta": 9, "badge": 8, "sticker": 8,
             "legal": 7, "group": 7}
FLOOR_REL = {"headline": 0.028, "body": 0.016, "cta_text": 0.017, "cta": 0.016, "badge": 0.013,
             "sticker": 0.013, "legal": 0.0085, "group": 0.01}


@dataclass
class Ctx:
    A: object
    W: float
    H: float
    relax: float = 1.0      # < 1 loosens readability floors (last-resort pass)
    extreme: bool = False   # allow the deepest drop set (degradation ladder)

    def __post_init__(self):
        A = self.A
        self.S = math.sqrt(self.W * self.H / (A.W0 * A.H0))
        self.Smin = min(self.W / A.W0, self.H / A.H0)
        self.Smax = max(self.W / A.W0, self.H / A.H0)
        self.minwh = min(self.W, self.H)
        self.m = max(4.0, A.margin_ratio * self.minwh)
        if self.W / self.H > 3 or self.H / self.W > 3:
            # extreme ratios: margin from the short side would be cramped
            self.m = max(self.m, 0.1 * self.minwh)
        self.mx = self.m
        self.my = self.m

    def floor(self, role, src_fs):
        f = max(FLOOR_ABS.get(role, 8), FLOOR_REL.get(role, 0.012) * self.minwh) * self.relax
        return max(4, int(round(min(f, max(src_fs * 2.0, 6)))))

    @property
    def logo_min_h(self):
        """Smallest visible logo height that still reads as a brand mark."""
        return max(16.0, 0.045 * self.minwh)


@dataclass
class Block:
    w: float
    h: float
    items: list = field(default_factory=list)   # (eid, dx, dy, w, h, extra)
    vis: list = field(default_factory=list)     # (uid, role, dx, dy, w, h)
    texts: list = field(default_factory=list)   # (eid, uid, role, fs, overflow, lines)

    def add(self, other, dx, dy):
        for eid, x, y, w, h, ex in other.items:
            self.items.append((eid, x + dx, y + dy, w, h, ex))
        for uid, role, x, y, w, h in other.vis:
            self.vis.append((uid, role, x + dx, y + dy, w, h))
        self.texts.extend(other.texts)

    def vis_extent(self):
        r = union([(x, y, w, h) for _, _, x, y, w, h in self.vis])
        return r or (0, 0, self.w, self.h)

    @property
    def bleed(self):
        """How far element boxes reach past the block's visible bounds
        (transparent padding of images): (left, top, right, bottom)."""
        if not self.items:
            return 0.0, 0.0, 0.0, 0.0
        return (max(0.0, -min(x for _, x, _, _, _, _ in self.items)),
                max(0.0, -min(y for _, _, y, _, _, _ in self.items)),
                max(0.0, max(x + w for _, x, _, w, _, _ in self.items) - self.w),
                max(0.0, max(y + h for _, _, y, _, h, _ in self.items) - self.h))


class Plan:
    def __init__(self, W, H, name):
        self.W, self.H, self.name = W, H, name
        self.geom = {}
        self.vis = []          # (uid, role, rect)
        self.texts = []        # (eid, uid, role, fs, overflow, lines)
        self.dropped = set()
        self.collisions = 0
        self.intended = set()  # pairs of uids allowed to overlap (badge on its hero)
        self.score = None
        self.parts = {}

    def put(self, block, x, y):
        for eid, dx, dy, w, h, ex in block.items:
            g = {"x": x + dx, "y": y + dy, "w": w, "h": h}
            g.update(ex)
            self.geom[eid] = g
        for uid, role, dx, dy, w, h in block.vis:
            self.vis.append((uid, role, (x + dx, y + dy, w, h)))
        self.texts.extend(block.texts)

    def settle(self, A):
        """Clamp every element box into the canvas (as the emitter will) and
        move the matching visible footprints, so the critic judges the
        positions that will really be rendered."""
        W, H = self.W, self.H
        shifts = {}
        for eid, g in self.geom.items():
            if g.get("hidden"):
                continue
            rot = float(A.els[eid].get("rotation") or 0)
            bw, bh = rot_aabb(g["w"], g["h"], rot)
            bw, bh = max(bw, g["w"]), max(bh, g["h"])
            cx, cy = g["x"] + g["w"] / 2, g["y"] + g["h"] / 2
            ncx = min(max(cx, bw / 2), W - bw / 2) if bw <= W else W / 2
            ncy = min(max(cy, bh / 2), H - bh / 2) if bh <= H else H / 2
            dx, dy = ncx - cx, ncy - cy
            if abs(dx) > 0.01 or abs(dy) > 0.01:
                g["x"] += dx
                g["y"] += dy
                u = A.unit_of.get(eid)
                if u is not None and len(u.members) == 1:
                    shifts[u.uid] = (dx, dy)
        if shifts:
            self.vis = [(uid, role, (r[0] + shifts[uid][0], r[1] + shifts[uid][1], r[2], r[3]))
                        if uid in shifts else (uid, role, r) for uid, role, r in self.vis]


# --------------------------------------------------------------------------
# Block builders
# --------------------------------------------------------------------------

def text_block(u, k, wrap_w, ctx, max_w=None):
    el = u.text
    src = el["font_size"]
    floor = ctx.floor(u.role, src)
    fs = max(floor, int(round(src * k)), 1)
    wrap_w = max(1.0, wrap_w)
    lay = F.layout(el, fs, wrap_w)
    if lay.overflow and max_w and max_w > wrap_w:
        # an unbreakable word (date, URL, spaced eyebrow) may widen its box
        # toward the full column before the type has to shrink
        wrap_w = min(max_w, max(wrap_w, F.layout(el, fs, 1e6).width + 1.0))
        lay = F.layout(el, fs, wrap_w)
    while lay.overflow and fs > floor:
        fs -= 1
        lay = F.layout(el, fs, wrap_w)
    # still too wide at the readability floor: small beats clipped
    while lay.overflow and fs > 4:
        fs -= 1
        lay = F.layout(el, fs, wrap_w)
    align = el.get("text_align", "left")
    vx = {"center": (wrap_w - lay.width) / 2, "right": wrap_w - lay.width}.get(align, 0.0)
    b = Block(wrap_w, lay.height)
    b.items.append((el["id"], 0.0, 0.0, wrap_w, lay.height, {"font_size": fs}))
    b.vis.append((u.uid, u.role, vx, 0.0, min(lay.width, wrap_w), lay.height))
    b.texts.append((el["id"], u.uid, u.role, fs, lay.overflow, lay.lines))
    return b


def image_block(u, vis_h, max_vis_w, max_box_h=None):
    el = u.image
    fx, fy, fw, fh = AS.visible_frac(el)
    bw0, bh0 = max(el["width"], 1e-6), max(el["height"], 1e-6)
    a_box = bw0 / bh0
    va = (fw * bw0) / (fh * bh0)
    vh = max(1.0, vis_h)
    if max_box_h is not None:
        vh = min(vh, max_box_h * fh)
    vw = vh * va
    if vw > max_vis_w:
        vw = max(1.0, max_vis_w)
        vh = vw / va
    box_h = vh / fh
    box_w = box_h * a_box
    b = Block(vw, vh)
    b.items.append((el["id"], -fx * box_w, -fy * box_h, box_w, box_h, {}))
    b.vis.append((u.uid, u.role, 0.0, 0.0, vw, vh))
    return b


def chip_block(u, s, max_w, ctx):
    sh, t = u.shape, u.text
    src = t["font_size"]
    floor = ctx.floor(u.role, src)
    fs = max(floor, int(round(src * s)), 1)
    src_lay = F.layout(t, src, 1e6)
    pad_src = max(0.0, (sh["width"] - src_lay.width) / 2.0)
    vpad_ratio = max(0.18, (sh["height"] - src_lay.height) / 2.0 / max(src, 1))

    def build(fs, tight=False):
        se = fs / src
        lay = F.layout(t, fs, 1e6)
        pad = 0.7 * fs if tight else max(pad_src * se, 0.7 * fs)
        w = lay.width + 2 * pad if tight else max(sh["width"] * se, lay.width + 2 * pad)
        h = max(sh["height"] * se, lay.height + 2 * vpad_ratio * fs)
        return se, lay, w, h, pad

    se, lay, w, h, pad = build(fs)
    if w > max_w:  # compress the side padding before touching the type
        se, lay, w, h, pad = build(fs, tight=True)
    while w > max_w and fs > floor:
        fs -= 1
        se, lay, w, h, pad = build(fs, tight=True)
    wrapped = False
    if w > max_w:  # still too wide at the floor: let the label wrap
        w = max(max_w, 2 * pad + 1)
        lay = F.layout(t, fs, w - 2 * pad)
        h = max(h, lay.height + 2 * vpad_ratio * fs)
        wrapped = True
    r_src = float(sh.get("corner_radius") or 0)
    if r_src >= 0.45 * sh["height"] and sh["height"] > 0:
        radius = h / 2.0
    else:
        radius = min(r_src * se, h / 2.0, w / 2.0)

    align = t.get("text_align", "left")
    if align == "center" or wrapped:
        lx, lw = 0.0, w
        if wrapped:
            lx, lw = pad, w - 2 * pad
    else:
        lx, lw = pad, w - 2 * pad
    if lay.lines == 1 and not u.rotation:
        # optical centring on the cap height, not the ascent box
        ly = h / 2.0 + lay.cap / 2.0 + lay.ascent
    else:
        ly = (h - lay.height) / 2.0
    ly = max(0.0, min(ly, h - lay.height)) if lay.height <= h else (h - lay.height) / 2.0

    aw, ah = rot_aabb(w, h, u.rotation)
    ox, oy = (aw - w) / 2.0, (ah - h) / 2.0
    b = Block(aw, ah)
    b.items.append((sh["id"], ox, oy, w, h, {"corner_radius": radius}))
    b.items.append((t["id"], ox + lx, oy + ly, lw, lay.height, {"font_size": fs}))
    b.vis.append((u.uid, u.role, 0.0, 0.0, aw, ah))
    b.texts.append((t["id"], u.uid, u.role, fs, lay.overflow and not wrapped, lay.lines))
    return b


def group_block(u, s, max_w, ctx):
    x0, y0, w0, h0 = u.box
    if w0 > 0 and w0 * s > max_w:
        s = max_w / w0
    b = Block(w0 * s, h0 * s)
    for e in u.members:
        ex = {}
        if e.get("type") == "text":
            ex["font_size"] = max(1, int(round(e["font_size"] * s)))
            b.texts.append((e["id"], u.uid, u.role, ex["font_size"], False, 1))
        if e.get("type") == "shape" and e.get("corner_radius"):
            ex["corner_radius"] = float(e["corner_radius"]) * s
        b.items.append((e["id"], (e["x"] - x0) * s, (e["y"] - y0) * s, e["width"] * s, e["height"] * s, ex))
    b.vis.append((u.uid, u.role, 0.0, 0.0, b.w, b.h))
    return b


def divider_block(u, aw, ctx, rel=True):
    e = u.members[0]
    w = aw * (u.rel_w if rel else 1.0)
    h = max(1.0, min(e["height"], e["height"] * max(ctx.S, 0.5)))
    b = Block(w, h)
    b.items.append((e["id"], 0.0, 0.0, w, h, {}))
    b.vis.append((u.uid, u.role, 0.0, 0.0, w, h))
    return b


def spread_row(blocks, anchors, rw, gap):
    """Header-style row: each block keeps its source side (left/centre/right)."""
    if sum(b.w for b in blocks) + gap * (len(blocks) - 1) > rw:
        return hrow(blocks, rw, gap)
    h = max(b.h for b in blocks)
    out = Block(rw, h)
    xl, xr = 0.0, rw
    for b, a in zip(blocks, anchors):
        if a == "left":
            out.add(b, xl, (h - b.h) / 2)
            xl += b.w + gap
    for b, a in reversed(list(zip(blocks, anchors))):
        if a == "right":
            out.add(b, xr - b.w, (h - b.h) / 2)
            xr -= b.w + gap
    for b, a in zip(blocks, anchors):
        if a == "center":
            out.add(b, (rw - b.w) / 2, (h - b.h) / 2)
    return out


def unit_block(u, k, aw, ctx, rel=True):
    if u.role == "divider":
        return divider_block(u, aw, ctx, rel)
    if u.kind == "text":
        return text_block(u, k, aw * (u.rel_w if rel else 1.0), ctx, max_w=aw)
    if u.kind == "chip":
        return chip_block(u, k, aw, ctx)
    if u.kind == "image":
        vh = u.vis[3] * k
        if u.role == "logo":
            vh = max(vh, ctx.logo_min_h)
        return image_block(u, vh, aw)
    return group_block(u, k, aw, ctx)


def hrow(blocks, max_w, gap, align="center"):
    """Lay blocks left-to-right, wrapping onto new lines when needed."""
    lines, cur, cur_w = [], [], 0.0
    for b in blocks:
        add = b.w if not cur else gap + b.w
        if cur and cur_w + add > max_w:
            lines.append(cur)
            cur, cur_w = [b], b.w
        else:
            cur.append(b)
            cur_w += add
    if cur:
        lines.append(cur)
    width = min(max_w, max(sum(b.w for b in ln) + gap * (len(ln) - 1) for ln in lines))
    height = sum(max(b.h for b in ln) for ln in lines) + gap * (len(lines) - 1)
    out = Block(width, height)
    y = 0.0
    for ln in lines:
        lw = sum(b.w for b in ln) + gap * (len(ln) - 1)
        lh = max(b.h for b in ln)
        x = {"center": (width - lw) / 2, "right": width - lw}.get(align, 0.0)
        for b in ln:
            out.add(b, x, y + (lh - b.h) / 2)
            x += b.w + gap
        y += lh + gap
    return out


# --------------------------------------------------------------------------
# The flexible column
# --------------------------------------------------------------------------

def stack(rows, rw, rh, k, ctx, rel=True, pin_top=False, pin_bottom=False, align="center",
          img_cap=0.6, img_boost=1.0, room=None):
    """Stack rows top to bottom inside rw x rh. Text/chips are sized by k;
    image rows flex into whatever height is left (capped). `room` is the
    space available above/below the region for image padding to bleed
    into. Returns a Block of exactly rw x rh, or None if it cannot fit."""
    room = ctx.my if room is None else room
    if not rows:
        return None
    fixed = []
    for r in rows:
        if r.is_image:
            fixed.append(None)
            continue
        if r.horizontal and r.spread:
            bl = [unit_block(u, k, rw * 0.5, ctx, rel) for u in r.units]
            fixed.append(spread_row(bl, [u.anchor for u in r.units], rw, 0.04 * rw))
        elif r.horizontal:
            bl = [unit_block(u, k, rw, ctx, rel) for u in r.units]
            avg_h = sum(b.h for b in bl) / len(bl)
            fixed.append(hrow(bl, rw, 0.35 * avg_h, align))
        else:
            fixed.append(unit_block(r.units[0], k, rw, ctx, rel))

    gmin = max(2.0, 0.018 * rh)
    gmax = max(gmin, 0.07 * rh)
    gaps = [min(gmax, max(gmin, r.gap_before * k)) for r in rows[1:]]
    used = sum(b.h for b in fixed if b is not None) + sum(gaps)
    R = rh - used
    if R < -0.5:
        return None
    if any(b is not None and b.w > rw + 0.5 for b in fixed):
        return None

    # image rows share the remaining height
    img_rows = [i for i, r in enumerate(rows) if r.is_image]
    heights = {}
    if img_rows:
        hs = {i: max(u.vis[3] for u in rows[i].units) for i in img_rows}
        tot = sum(hs.values()) or 1.0
        for i in img_rows:
            r = rows[i]
            gap = 0.06 * rw
            va = sum(u.vis[2] / max(u.vis[3], 1e-6) for u in r.units)
            # transparent padding at both ends must still land on the canvas
            fx0, _, fw0, _ = AS.visible_frac(r.units[0].image)
            fx1, _, fw1, _ = AS.visible_frac(r.units[-1].image)
            va0 = r.units[0].vis[2] / max(r.units[0].vis[3], 1e-6)
            va1 = r.units[-1].vis[2] / max(r.units[-1].vis[3], 1e-6)
            bleed = va0 * fx0 / fw0 + va1 * (1 - fx1 - fw1) / fw1
            span = rw + 2 * ctx.mx if align == "center" else rw + ctx.mx
            cap_w = min((rw - gap * (len(r.units) - 1)) / max(va, 1e-6),
                        (span - gap * (len(r.units) - 1)) / max(va + bleed, 1e-6))
            cap = min(cap_w, img_cap * rh, hs[i] * ctx.Smax * 1.8 * img_boost)
            heights[i] = max(0.0, min(cap, R * hs[i] / tot))
            if heights[i] < max(6.0, 0.06 * rh):
                return None
        for i in img_rows:
            r = rows[i]
            bl = [image_block(u, heights[i], rw) for u in r.units]
            fixed[i] = hrow(bl, rw, 0.06 * rw, "center")
    # transparent padding of the first/last rows must stay on the canvas
    pad_top = max(0.0, fixed[0].bleed[1] - room)
    pad_bot = max(0.0, fixed[-1].bleed[3] - room)
    L = rh - sum(b.h for b in fixed) - sum(gaps) - pad_top - pad_bot
    if L < -0.5:
        return None
    L = max(0.0, L)

    # spread leftover: first let gaps breathe, then honour the source anchoring
    if gaps:
        sg = sum(gaps)
        grow = min(L, sg * 1.2)
        gaps = [g + grow * g / sg for g in gaps] if sg > 0 else gaps
        L -= grow
    top = 0.0
    n = len(rows)
    if pin_top and pin_bottom and n >= 3:
        gaps[0] += L / 2
        gaps[-1] += L / 2
    elif pin_top and n >= 2:
        gaps[0] += L / 2
    elif pin_bottom and n >= 2:
        gaps[-1] += L / 2
        top = L / 2
    else:
        top = L / 2

    out = Block(rw, rh)
    y = top + pad_top
    for i, b in enumerate(fixed):
        if i > 0:
            y += gaps[i - 1]
        x = {"center": (rw - b.w) / 2, "right": rw - b.w}.get(align, 0.0)
        out.add(b, x, y)
        y += b.h
    return out


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------

def _rows_without(rows, skip):
    from .analyze import Row

    out = []
    for r in rows:
        units = [u for u in r.units if u.uid not in skip]
        if units:
            out.append(Row(units, gap_before=r.gap_before, horizontal=r.horizontal and len(units) > 1,
                           spread=r.spread and len(units) > 1))
    return out


def tpl_stack(ctx, k, drop, inline=(), img_cap=0.6):
    A, W, H = ctx.A, ctx.W, ctx.H
    rows = _rows_without(A.rows, drop)
    if not rows:
        return None
    blk = stack(rows, W - 2 * ctx.mx, H - 2 * ctx.my, k, ctx, rel=True,
                pin_top=A.pin_top, pin_bottom=A.pin_bottom, align=A.align, img_cap=img_cap)
    if blk is None:
        return None
    p = Plan(W, H, f"stack/cap{img_cap}")
    p.put(blk, ctx.mx, ctx.my)
    return p


def _inline_rows(A, rows, inline):
    """Put corner tags (e.g. a 'NEW' badge) at the top of a text column."""
    from .analyze import Row

    tags = [u for u in A.corners if u.uid in inline and u.kind == "chip" and not u.rotation]
    if not tags:
        return rows
    return [Row(tags, horizontal=len(tags) > 1)] + rows


def _media_block(heroes, mw, mh, max_box_h=None):
    if len(heroes) == 1:
        return image_block(heroes[0], mh, mw, max_box_h=max_box_h)
    best = None
    for horizontal in (True, False):
        if horizontal:
            gap = 0.05 * mw
            va = sum(u.vis[2] / u.vis[3] for u in heroes)
            h = min(mh, (mw - gap * (len(heroes) - 1)) / va)
            b = hrow([image_block(u, h, mw) for u in heroes], mw, gap)
        else:
            gap = 0.05 * mh
            h_each = (mh - gap * (len(heroes) - 1)) / len(heroes)
            b = Block(mw, mh)
            y = 0.0
            col = []
            for u in heroes:
                col.append(image_block(u, h_each, mw))
            tot = sum(c.h for c in col) + gap * (len(col) - 1)
            b = Block(max(c.w for c in col), tot)
            for c in col:
                b.add(c, (b.w - c.w) / 2, y)
                y += c.h + gap
        a = sum(w * h for _, _, _, _, w, h in b.vis)
        if best is None or a > best[0]:
            best = (a, b)
    return best[1]


def tpl_split(ctx, k, drop, side="left", frac=0.45, inline=()):
    A, W, H = ctx.A, ctx.W, ctx.H
    heroes = [u for u in A.flow if u.role == "hero" and u.uid not in drop]
    if not heroes:
        return None
    m = ctx.m
    gap = max(m, 0.04 * W)
    inner_w, inner_h = W - 2 * m, H - 2 * m
    mw = inner_w * frac - gap / 2
    tw = inner_w - mw - gap
    if tw < 0.25 * W or mw < 0.15 * W:
        return None
    media = _media_block(heroes, mw, inner_h, max_box_h=H)
    rows = _rows_without(A.rows, drop | {u.uid for u in heroes})
    rows = _inline_rows(A, rows, inline)
    if not rows:
        return None
    col = stack(rows, tw, inner_h, k, ctx, rel=False, align=A.align)
    if col is None:
        return None
    p = Plan(W, H, f"split/{side}/{frac}")
    ext = col.vis_extent()
    # pull the two halves together around the canvas centre, keeping the
    # media's transparent padding on the canvas
    content_w = media.w + gap + ext[2]
    lo = max(m, media.bleed[0]) if side == "left" else m
    hi = W - (max(m, media.bleed[2]) if side == "right" else m)
    if content_w > hi - lo + 0.5:
        return None
    left = lo + (hi - lo - content_w) / 2
    if side == "left":
        p.put(media, left, m + (inner_h - media.h) / 2)
        p.put(col, left + media.w + gap - ext[0], m)
    else:
        p.put(col, left - ext[0], m)
        p.put(media, left + ext[2] + gap, m + (inner_h - media.h) / 2)
    return p


def tpl_row(ctx, k, drop, order="LHTC", logo_frac=0.7, inline=()):
    A, W, H = ctx.A, ctx.W, ctx.H
    m = ctx.m
    inner_h = H - 2 * m
    logo = next((u for u in A.flow if u.role == "logo" and u.uid not in drop), None)
    heroes = [u for u in A.flow if u.role == "hero" and u.uid not in drop]
    cta = next((u for u in A.flow if u.role == "cta" and u.uid not in drop), None)

    slots = []
    in_col = set()
    for ch in order:
        if ch == "L" and logo:
            vh = max(inner_h * logo_frac, min(ctx.logo_min_h, inner_h))
            slots.append(("L", image_block(logo, vh, 0.28 * W, max_box_h=H)))
            in_col.add(logo.uid)
        elif ch == "H" and heroes:
            if len(heroes) == 1:
                slots.append(("H", image_block(heroes[0], inner_h, 0.3 * W, max_box_h=H)))
            else:
                slots.append(("H", _media_block(heroes, 0.3 * W, inner_h)))
            in_col.update(u.uid for u in heroes)
        elif ch == "C" and cta:
            sh_h = max(cta.shape["height"], 1)
            # the CTA has its own slot: a run-on headline forcing k down must
            # not take the button label below legibility with it, and on
            # short banners the button may grow toward full height
            legible = FLOOR_ABS["cta"] / max(cta.text["font_size"], 1)
            cap = max(0.55 * inner_h / sh_h, min(legible, 0.95 * inner_h / sh_h))
            s = min(max(k * 1.1, legible), cap)
            slots.append(("C", chip_block(cta, s, 0.38 * W, ctx)))
            in_col.add(cta.uid)
        elif ch == "T":
            slots.append(("T", None))
    rows = _rows_without(A.rows, drop | in_col)
    rows = _inline_rows(A, rows, inline)
    if not rows:
        slots = [sl for sl in slots if sl[0] != "T"]
    n = len(slots)
    if n == 0:
        return None
    # transparent image padding at either end must stay on the canvas
    left = max(m, slots[0][1].bleed[0] if slots[0][1] is not None else 0.0)
    right = max(m, slots[-1][1].bleed[2] if slots[-1][1] is not None else 0.0)
    usable = W - left - right
    gap = max(m, 0.025 * W)
    fixed_w = sum(b.w for _, b in slots if b is not None)
    col, ext = None, None
    if rows:
        tw = usable - fixed_w - gap * (n - 1)
        if tw < 0.18 * W:
            return None
        col = stack(rows, tw, inner_h, k, ctx, rel=False, align=A.align)
        if col is None:
            return None
        ext = col.vis_extent()
    widths = [(ext[2] if b is None else b.w) for _, b in slots]
    free = usable - sum(widths)
    if free < -0.5:
        return None
    g = free / (n - 1) if n > 1 else 0.0
    g = min(g, max(gap * 3, 0.06 * W))
    x = left + max(0.0, (usable - sum(widths) - g * (n - 1)) / 2)
    p = Plan(W, H, f"row/{order}/{logo_frac}")
    for (name, b), w in zip(slots, widths):
        if b is None:
            p.put(col, x - ext[0], m)
        else:
            p.put(b, x, m + (inner_h - b.h) / 2)
        x += w + g
    return p


# --------------------------------------------------------------------------
# Extras: background, decorations, panels, corner tags, hidden units
# --------------------------------------------------------------------------

def _anchor_box(ctx, box, keep_aspect=True, scale=None):
    A, W, H = ctx.A, ctx.W, ctx.H
    x, y, w, h = box
    s = ctx.S if scale is None else scale
    nw, nh = w * s, h * s
    cap = 0.95 * min(W, H) if keep_aspect else None
    if keep_aspect and max(nw, nh) > cap:
        f = cap / max(nw, nh)
        nw, nh = nw * f, nh * f
    nw, nh = min(nw, W), min(nh, H)
    cx = (x + w / 2) / A.W0 * W
    cy = (y + h / 2) / A.H0 * H
    nx = min(max(0.0, cx - nw / 2), W - nw)
    ny = min(max(0.0, cy - nh / 2), H - nh)
    return nx, ny, nw, nh


def _fill_alpha(e):
    try:
        p = str(e.get("fill_color")).split("(", 1)[1].rstrip(")").split(",")
        a = float(p[3]) if len(p) > 3 else 1.0
    except (IndexError, ValueError):
        a = 1.0
    return a * float(e.get("opacity", 1.0))


def _clear_of(rect, occupied, ctx):
    """Nearest position for a small decoration that keeps it off content.
    Translucent washes may layer behind content; opaque dots and sparkles
    sitting on a headline read as a collision. Returns rect unchanged when
    no free spot is close by."""
    W, H = ctx.W, ctx.H
    x, y, w, h = rect

    def hits(r):
        return any(inter(r, o) > 0.02 * area(r) for o in occupied)

    if not hits(rect):
        return rect
    reach = 0.3 * ctx.minwh
    gap = 0.01 * ctx.minwh
    for f in (1.0, 0.75, 0.55):
        sw, sh = w * f, h * f
        cx, cy = x + w / 2, y + h / 2
        sx, sy = cx - sw / 2, cy - sh / 2
        # grid around the anchor, plus snaps just outside each blocker's edges
        step = max(4.0, 0.02 * ctx.minwh)
        cands = [(sx + dx * step * i, sy + dy * step * i)
                 for i in range(1, int(reach / step) + 1)
                 for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, -1), (-1, 1), (1, 1))]
        for o in occupied:
            if inter((sx, sy, sw, sh), o) > 0:
                cands += [(sx, o[1] - sh - gap), (sx, o[1] + o[3] + gap),
                          (o[0] - sw - gap, sy), (o[0] + o[2] + gap, sy)]
        best = None
        for nx, ny in cands:
            nx, ny = min(max(0.0, nx), W - sw), min(max(0.0, ny), H - sh)
            dist = (nx - sx) ** 2 + (ny - sy) ** 2
            if dist > reach ** 2 or (best is not None and dist >= best[0]):
                continue
            r = (nx, ny, sw, sh)
            if not hits(r):
                best = (dist, r)
        if best is not None:
            return best[1]
    return rect


def place_extras(plan, ctx, drop, inline=()):
    A, W, H = ctx.A, ctx.W, ctx.H
    for u in A.backgrounds:
        plan.geom[u.members[0]["id"]] = {"x": 0.0, "y": 0.0, "w": W, "h": H}

    occupied = [r for uid, role, r in plan.vis]

    # corner tags and stickers
    for u in sorted(A.corners, key=lambda u: -u.priority):
        if u.uid in drop or u.uid in inline:
            continue
        placed = False
        base = ctx.S
        if u.text is not None:
            base = max(base, ctx.floor(u.role, u.text["font_size"]) / u.text["font_size"])
        corner_order = [u.corner, u.corner[0] + ("l" if u.corner[1] == "r" else "r"),
                        ("b" if u.corner[0] == "t" else "t") + u.corner[1]]
        last = None
        for f in (1.0, 0.82, 0.68):
            if u.kind == "chip":
                b = chip_block(u, base * f, 0.45 * W, ctx)
            elif u.kind == "image":
                b = image_block(u, u.vis[3] * base * f, 0.45 * W)
            else:
                b = group_block(u, base * f, 0.45 * W, ctx)
            mm = ctx.m * (0.5 if u.role == "sticker" else 1.0)
            for c in corner_order:
                x = mm if c[1] == "l" else W - mm - b.w
                y = mm if c[0] == "t" else H - mm - b.h
                rect = (x, y, b.w, b.h)
                last = last or (b, x, y)
                if b.w > W - 2 * mm or b.h > H - 2 * mm:
                    continue
                if all(inter(rect, o) <= 0.02 * area(rect) for o in occupied):
                    plan.put(b, x, y)
                    occupied.append(rect)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            b, x, y = last
            x = min(max(0.0, x), max(0.0, W - b.w))
            y = min(max(0.0, y), max(0.0, H - b.h))
            plan.put(b, x, y)
            plan.collisions += 1

    # badges that sit on a hero follow it
    placed_vis = {uid: r for uid, role, r in plan.vis}
    for u in A.attached:
        if u.uid in drop:
            continue
        hv = placed_vis.get(u.attach)
        if hv is None:
            drop = set(drop) | {u.uid}
            continue
        rx, ry, ratio = u.attach_rel
        s = hv[3] * ratio / max(u.box[3], 1e-6)
        b = chip_block(u, s, 0.4 * W, ctx)
        x = min(max(0.0, hv[0] + rx * hv[2] - b.w / 2), W - b.w)
        y = min(max(0.0, hv[1] + ry * hv[3] - b.h / 2), H - b.h)
        plan.put(b, x, y)
        plan.intended.add(frozenset((u.uid, u.attach)))

    # decorations: keep their relative anchor and stay behind content;
    # small opaque ones step off the content they would otherwise cover
    content = [r for uid, role, r in plan.vis if role not in ("decoration", "panel", "accent")]
    for u in A.decorations + A.accents:
        if u.uid in drop:
            continue
        solid = (u.role == "decoration" and area(u.box) < 0.1 * A.W0 * A.H0
                 and max(_fill_alpha(e) for e in u.members) >= 0.3)
        if len(u.members) == 1:
            e = u.members[0]
            if e.get("type") == "shape" and e.get("shape_type") == "line":
                nx = e["x"] / A.W0 * W
                nw = min(W - nx, e["width"] * W / A.W0)
                ny = (e["y"] + e["height"] / 2) / A.H0 * H
                nh = max(1.0, e["height"] * ctx.S)
                plan.geom[e["id"]] = {"x": max(0.0, nx), "y": min(max(0.0, ny - nh / 2), H - nh), "w": max(1.0, nw), "h": nh}
                continue
            nx, ny, nw, nh = _anchor_box(ctx, (e["x"], e["y"], e["width"], e["height"]))
            if solid and not e.get("rotation"):
                nx, ny, nw, nh = _clear_of((nx, ny, nw, nh), content, ctx)
            g = {"x": nx, "y": ny, "w": nw, "h": nh}
            if e.get("corner_radius"):
                g["corner_radius"] = float(e["corner_radius"]) * nw / max(e["width"], 1e-6)
            plan.geom[e["id"]] = g
            if u.role == "accent":
                plan.vis.append((u.uid, u.role, AS.visible_rect(e, nx, ny, nw, nh)))
        else:
            nx, ny, nw, nh = _anchor_box(ctx, u.box)
            b = group_block(u, nw / max(u.box[2], 1e-6), W, ctx)
            if solid:
                nx, ny, cw, _ = _clear_of((nx, ny, b.w, b.h), content, ctx)
                if cw < b.w - 0.01:
                    b = group_block(u, cw / max(u.box[2], 1e-6), W, ctx)
            plan.put(b, nx, ny)

    # panels follow their children
    placed = {uid: r for uid, role, r in plan.vis}
    for u in A.panels:
        e = u.members[0]
        kids = [placed[c] for c in u.children if c in placed]
        if not kids or u.uid in drop:
            nx, ny, nw, nh = _anchor_box(ctx, u.box, keep_aspect=False)
            plan.geom[e["id"]] = {"x": nx, "y": ny, "w": nw, "h": nh}
            if not kids:
                continue
        src_kids = union([x.vis for x in A.units if x.uid in u.children])
        pad = 0.0
        if src_kids:
            pad = max(0.0, min(src_kids[0] - u.box[0], src_kids[1] - u.box[1],
                               u.box[0] + u.box[2] - src_kids[0] - src_kids[2],
                               u.box[1] + u.box[3] - src_kids[1] - src_kids[3])) * ctx.S
        r = union(kids)
        if r is None:
            continue
        x0, y0 = max(0.0, r[0] - pad), max(0.0, r[1] - pad)
        x1, y1 = min(W, r[0] + r[2] + pad), min(H, r[1] + r[3] + pad)
        g = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
        if e.get("corner_radius"):
            g["corner_radius"] = float(e["corner_radius"]) * ctx.S
        plan.geom[e["id"]] = g

    # dropped / missing units: kept in the file, invisible, inside the canvas
    hidden_units = [u for u in A.units if u.uid in drop or u.role == "missing"]
    for u in hidden_units:
        plan.dropped.add(u.uid)
        nx, ny, nw, nh = _anchor_box(ctx, u.box)
        s = nw / max(u.box[2], 1e-6)
        for e in u.members:
            if e["id"] in plan.geom and u.role in ("panel",):
                continue
            ex, ey = nx + (e["x"] - u.box[0]) * s, ny + (e["y"] - u.box[1]) * s
            g = {"x": ex, "y": ey, "w": e["width"] * s, "h": e["height"] * s, "hidden": True}
            if e.get("type") == "text":
                g["font_size"] = max(1, int(round(e["font_size"] * s)))
            plan.geom[e["id"]] = g
    return plan
