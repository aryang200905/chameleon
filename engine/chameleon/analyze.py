"""Read a source design's "DNA".

No semantic labels arrive with the design, so roles are inferred from
geometry, typography, stacking and grouping:

    background  full-bleed image/shape
    decoration  shape without text (or shape-only group)
    panel       large shape that other content sits on
    logo        brand lockup image
    hero        main image(s)
    accent      small non-logo image
    missing     image whose asset file is absent (hidden gracefully)
    headline    most prominent text
    body        other text
    cta_text    short, accented call-to-action text without a button shape
    legal       fine print
    cta         button (shape + label) - the most prominent one
    badge       other shape + label chips
    sticker     rotated chip (ribbon, stamp)
    group       any other designer-grouped unit (kept together, scaled as one)
"""

import math
import statistics
from dataclasses import dataclass, field

from . import assets as AS
from . import fonts as F
from .geometry import area, center, contains_point, inter, rot_aabb, union, v_overlap

PRIORITY = {
    "headline": 100, "logo": 95, "cta": 90, "cta_text": 88, "hero": 85,
    "body": 60, "group": 55, "badge": 40, "sticker": 35, "legal": 25,
    "accent": 15, "decoration": 10, "panel": 10, "background": 100, "missing": 0,
}


@dataclass
class Unit:
    uid: str
    members: list
    kind: str                       # text | image | shape | chip | group
    role: str = ""
    box: tuple = (0, 0, 0, 0)       # union of member boxes (source px)
    vis: tuple = (0, 0, 0, 0)       # visible footprint (source px)
    z: int = 0
    rotation: float = 0.0
    corner: str = None              # 'tl' | 'tr' | 'bl' | 'br' when pinned to a corner
    text: dict = None
    shape: dict = None
    image: dict = None
    rel_w: float = 1.0              # share of the source content column width
    children: list = field(default_factory=list)  # panels only
    anchor: str = "center"          # horizontal anchor inside the source canvas
    attach: str = None              # uid of the hero this badge sits on
    attach_rel: tuple = None        # (cx, cy) relative to that hero's visible box, size ratio

    @property
    def priority(self):
        return PRIORITY.get(self.role, 50)


@dataclass
class Row:
    units: list
    gap_before: float = 0.0         # visible gap to previous row in source px
    horizontal: bool = False
    spread: bool = False            # small units pinned left / centre / right (header rows)

    @property
    def is_image(self):
        return all(u.kind == "image" and u.role == "hero" for u in self.units)


@dataclass
class Analysis:
    W0: float
    H0: float
    els: dict                       # id -> normalized element
    order: list                     # ids in source order
    units: list
    flow: list                      # units laid out by templates
    rows: list
    backgrounds: list
    decorations: list
    panels: list
    corners: list
    accents: list
    hidden: list                    # always hidden (missing assets)
    attached: list = field(default_factory=list)
    align: str = "center"
    margin_ratio: float = 0.05
    pin_top: bool = False
    pin_bottom: bool = False
    unit_of: dict = field(default_factory=dict)   # element id -> Unit

    def by_role(self, *roles):
        return [u for u in self.units if u.role in roles]


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def normalize(elements):
    out = []
    for i, el in enumerate(elements):
        e = dict(el)
        e["id"] = str(e.get("id", f"el_{i}"))
        for k in ("x", "y"):
            e[k] = _num(e.get(k))
        for k in ("width", "height"):
            e[k] = max(0.0, _num(e.get(k)))
        e["rotation"] = _num(e.get("rotation"))
        op = e.get("opacity")
        e["opacity"] = 1.0 if op is None else _num(op, 1.0)
        if e.get("z_index") is None:
            e["z_index"] = i
        if e.get("type") == "text":
            e["font_size"] = max(1.0, _num(e.get("font_size"), 16.0))
            e["content"] = str(e.get("content", ""))
        out.append(e)
    return out


def _alpha(color):
    try:
        inner = str(color).split("(", 1)[1].rstrip(")")
        parts = [p.strip() for p in inner.split(",")]
        return float(parts[3]) if len(parts) > 3 else 1.0
    except (IndexError, ValueError):
        return 1.0


def _box(e):
    return (e["x"], e["y"], e["width"], e["height"])


def _aabb(e):
    if not e["rotation"]:
        return _box(e)
    w, h = rot_aabb(e["width"], e["height"], e["rotation"])
    cx, cy = e["x"] + e["width"] / 2, e["y"] + e["height"] / 2
    return (cx - w / 2, cy - h / 2, w, h)


def text_vis(e):
    lay = F.layout(e, e["font_size"], e["width"])
    align = e.get("text_align", "left")
    off = {"center": (e["width"] - lay.width) / 2, "right": e["width"] - lay.width}.get(align, 0.0)
    return (e["x"] + off, e["y"], lay.width, lay.height)


def _is_serif(e):
    return "serif" in str(e.get("font_family", "")).lower() and "sans" not in str(e.get("font_family", "")).lower()


def analyze(source):
    canvas = source["canvas"]
    W0, H0 = float(canvas["width"]), float(canvas["height"])
    els_list = normalize(source["elements"])
    els = {e["id"]: e for e in els_list}
    canvas_rect = (0, 0, W0, H0)
    CA = W0 * H0
    minwh = min(W0, H0)

    # ---- backgrounds -----------------------------------------------------
    bg_ids = set()
    for e in els_list:
        if e.get("type") in ("image", "shape") and not e["rotation"]:
            if inter(_box(e), canvas_rect) >= 0.85 * CA:
                bg_ids.add(e["id"])

    # ---- units: explicit groups, then implicit "label on shape" chips ----
    groups, singles = {}, []
    for e in els_list:
        if e["id"] in bg_ids:
            continue
        gid = e.get("group_id")
        if gid:
            groups.setdefault(str(gid), []).append(e)
        else:
            singles.append(e)

    used = set()
    texts = [e for e in singles if e.get("type") == "text"]
    shapes = [e for e in singles if e.get("type") == "shape" and e.get("shape_type") != "line"]
    implicit = []
    for t in texts:
        tc = center(_box(t))
        best = None
        for s in shapes:
            if s["id"] in used or s["z_index"] > t["z_index"]:
                continue
            sb = _box(s)
            if area(sb) > 0.15 * CA or area(sb) <= 0:
                continue
            if contains_point(sb, tc) and inter(sb, text_vis(t)) >= 0.6 * max(1.0, area(text_vis(t))):
                if best is None or area(sb) < area(_box(best)):
                    best = s
        if best is not None:
            used.update({best["id"], t["id"]})
            implicit.append([best, t])

    raw_units = [(f"g:{gid}", mem) for gid, mem in groups.items()]
    raw_units += [(f"c:{m[1]['id']}", m) for m in implicit]
    raw_units += [(e["id"], [e]) for e in singles if e["id"] not in used]

    units = []
    for uid, mem in raw_units:
        mem = sorted(mem, key=lambda e: e["z_index"])
        u = Unit(uid=uid, members=mem, kind="group")
        u.box = union([_aabb(e) for e in mem]) or (0, 0, 0, 0)
        u.z = max(e["z_index"] for e in mem)
        u.rotation = next((e["rotation"] for e in mem if e["rotation"]), 0.0)
        ts = [e for e in mem if e.get("type") == "text"]
        ss = [e for e in mem if e.get("type") == "shape"]
        ims = [e for e in mem if e.get("type") == "image"]
        if len(mem) == 1:
            u.kind = mem[0]["type"]
            u.text = mem[0] if ts else None
            u.shape = mem[0] if ss else None
            u.image = mem[0] if ims else None
        elif len(ts) == 1 and len(ss) == 1 and not ims and contains_point(_box(ss[0]), center(_box(ts[0])), 2):
            u.kind, u.text, u.shape = "chip", ts[0], ss[0]
        # visible footprint
        vis = []
        for e in mem:
            if e.get("type") == "text":
                vis.append(text_vis(e) if not e["rotation"] else _aabb(e))
            elif e.get("type") == "image":
                vis.append(AS.visible_rect(e, *_box(e)))
            else:
                vis.append(_aabb(e))
        u.vis = union(vis) or u.box
        units.append(u)

    bg_units = []
    for bid in sorted(bg_ids, key=lambda i: els[i]["z_index"]):
        u = Unit(uid=bid, members=[els[bid]], kind=els[bid]["type"], role="background")
        u.box = u.vis = _box(els[bid])
        u.z = els[bid]["z_index"]
        bg_units.append(u)

    # ---- roles -------------------------------------------------------------
    for u in units:
        if area(u.box) <= 0 and u.kind in ("shape", "image"):
            # zero-size graphics draw nothing in the renderer; zero-size text
            # still draws (it is not clipped to its box), so it stays content
            u.role = "missing"
        elif u.kind == "shape":
            sh = u.shape
            thin = sh.get("shape_type") == "rectangle" and sh["height"] <= 0.012 * H0 and sh["width"] >= 0.2 * W0
            u.role = "divider" if (sh.get("shape_type") == "line" or thin) and not sh["rotation"] else "decoration"
        elif u.kind == "group" and all(e.get("type") == "shape" for e in u.members):
            u.role = "decoration"

    # panels: big shapes that other content sits on
    for u in units:
        if u.role != "decoration" or u.kind != "shape":
            continue
        if u.shape.get("shape_type") != "rectangle" or area(u.box) < 0.08 * CA:
            continue
        if u.shape["opacity"] * _alpha(u.shape.get("fill_color")) < 0.5:
            continue
        kids = [o for o in units if o is not u and o.role != "decoration" and o.z > u.z
                and contains_point(u.box, center(o.vis))]
        if kids:
            u.role = "panel"
            u.children = [k.uid for k in kids]

    # images
    imgs = [u for u in units if u.kind == "image" and u.role != "missing"]
    logo = None
    named = [u for u in imgs if "logo" in str(u.image.get("asset_id", "")).lower()]
    if named:
        logo = min(named, key=lambda u: u.vis[1])
    else:
        cands = [u for u in imgs if area(u.box) <= 0.08 * CA and u.box[2] >= 1.3 * u.box[3]
                 and (center(u.box)[1] < 0.22 * H0 or center(u.box)[1] > 0.85 * H0)]
        if cands and len(imgs) > 1:
            logo = min(cands, key=lambda u: area(u.box))
    if logo:
        logo.role = "logo"
    others = sorted([u for u in imgs if u is not logo], key=lambda u: -area(u.box))
    have_hero = False
    for u in others:
        missing = not AS.exists(u.image.get("asset_id"))
        if missing and not (area(u.box) >= 0.15 * CA and not have_hero):
            u.role = "missing"
        elif area(u.vis) < 0.012 * CA and have_hero:
            u.role = "accent"
        else:
            u.role = "hero"
            have_hero = True

    # texts
    tunits = [u for u in units if u.kind == "text" and u.role != "missing"]
    if tunits:
        def prom(u):
            t = u.text
            return t["font_size"] * (1.12 if str(t.get("font_weight")) == "bold" else 1.0) * (1.05 if _is_serif(t) else 1.0)

        head = max(tunits, key=lambda u: (prom(u), -u.box[1]))
        head.role = "headline"
        hfs = head.text["font_size"]
        hcolor = head.text.get("color")
        for u in tunits:
            if u is head:
                continue
            t = u.text
            words = len(t["content"].split())
            small = t["font_size"] <= 0.5 * hfs and t["font_size"] / minwh <= 0.016
            if small and (len(t["content"]) >= 40 or center(u.box)[1] > 0.8 * H0):
                u.role = "legal"
            elif words <= 5 and str(t.get("font_weight")) == "bold" and t.get("color") != hcolor and u.box[1] > head.box[1]:
                u.role = "cta_text"
            else:
                u.role = "body"

    # chips
    chips = [u for u in units if u.kind == "chip" and u.role != "missing"]
    flat = [u for u in chips if not u.rotation]
    for u in chips:
        u.role = "sticker" if u.rotation else "badge"
    if flat:
        cta = max(flat, key=lambda u: (area(u.box), u.box[1]))
        if len(flat) > 1 or area(cta.box) >= 0.02 * CA:
            cta.role = "cta"
    for u in units:
        if u.kind == "group" and not u.role:
            u.role = "group"
        if not u.role:
            u.role = "body" if u.kind == "text" else "group"

    # corner pins (small tags hugging two edges)
    tol = 0.12 * minwh
    for u in units:
        if u.role not in ("badge", "sticker", "accent", "missing", "group"):
            continue
        if area(u.box) > 0.07 * CA:
            continue
        x, y, w, h = u.box
        left, right = x < tol, W0 - (x + w) < tol
        top, bottom = y < tol, H0 - (y + h) < tol
        if (left or right) and (top or bottom):
            u.corner = ("t" if top else "b") + ("l" if left else "r")
        elif u.role == "sticker":
            cx, cy = center(u.box)
            u.corner = ("t" if cy < H0 / 2 else "b") + ("l" if cx < W0 / 2 else "r")

    # badges sitting on a hero image travel with it (price stickers, seals)
    heroes = [u for u in units if u.role == "hero"]
    attached = []
    for u in units:
        if u.kind != "chip" or u.role not in ("badge", "sticker") or u.corner and u.role == "sticker":
            continue
        best = None
        for h in heroes:
            hb = h.box
            grown = (hb[0] - 0.08 * hb[2], hb[1] - 0.08 * hb[3], hb[2] * 1.16, hb[3] * 1.16)
            ov = inter(u.vis, grown) / max(1.0, area(u.vis))
            if ov >= 0.5 and (best is None or ov > best[0]):
                best = (ov, h)
        if best:
            h = best[1]
            cx, cy = center(u.box)
            hv = h.vis
            u.attach = h.uid
            u.attach_rel = ((cx - hv[0]) / max(hv[2], 1e-6), (cy - hv[1]) / max(hv[3], 1e-6),
                            u.box[3] / max(hv[3], 1e-6))
            u.corner = None
            attached.append(u)

    decorations = [u for u in units if u.role == "decoration"]
    panels = [u for u in units if u.role == "panel"]
    accents = [u for u in units if u.role == "accent"]
    hidden = [u for u in units if u.role == "missing"]
    corners = [u for u in units if u.corner and u.role not in ("missing", "accent")]
    flow = [u for u in units if u.role not in ("decoration", "panel", "accent", "missing")
            and u not in corners and u not in attached]

    # ---- design DNA ----------------------------------------------------------
    votes = {}
    for u in flow:
        if u.text is not None and u.kind == "text":
            a = u.text.get("text_align", "left")
            votes[a] = votes.get(a, 0) + u.text["font_size"] * max(1, len(u.text["content"])) ** 0.5
    for u in flow:
        if u.kind in ("image", "chip", "group"):
            cx = center(u.vis)[0]
            a = "center" if abs(cx - W0 / 2) < 0.08 * W0 else ("left" if cx < W0 / 2 else "right")
            votes[a] = votes.get(a, 0) + 10
    align = max(votes, key=votes.get) if votes else "center"

    dists = []
    for u in flow:
        x, y, w, h = u.vis
        dists.append(min(max(0, x), max(0, y), max(0, W0 - x - w), max(0, H0 - y - h)))
    margin_ratio = min(0.08, max(0.035, (min(dists) / minwh) if dists else 0.05))

    col_w = max([u.box[2] for u in flow if u.kind == "text"] + [1.0])
    for u in flow:
        if u.kind == "text":
            u.rel_w = min(1.0, max(0.55, u.box[2] / col_w))
        elif u.role == "divider":
            u.rel_w = min(1.0, max(0.2, u.box[2] / col_w))
        cx = center(u.vis)[0]
        u.anchor = "left" if cx < W0 / 3 else ("right" if cx > 2 * W0 / 3 else "center")

    rows = _rows(flow, W0)
    pin_top = bool(rows) and rows[0].units[0].role != "headline" and min(u.vis[1] for u in rows[0].units) < 0.15 * H0 and len(rows) > 1
    pin_bottom = bool(rows) and len(rows) > 2 and max(u.vis[1] + u.vis[3] for u in rows[-1].units) > 0.86 * H0

    unit_of = {e["id"]: u for u in units + bg_units for e in u.members}
    return Analysis(W0=W0, H0=H0, els=els, order=[e["id"] for e in els_list], units=units,
                    flow=flow, rows=rows, backgrounds=bg_units, decorations=decorations,
                    panels=panels, corners=corners, accents=accents, hidden=hidden,
                    attached=attached, align=align, margin_ratio=margin_ratio,
                    pin_top=pin_top, pin_bottom=pin_bottom, unit_of=unit_of)


def _rows(flow, W0):
    """Reading-order rows. Units stay side by side when they are a set of
    chips (badge row), a set of images (gallery), or a few small units
    pinned to different sides (a header like logo-left / date-right)."""
    items = sorted(flow, key=lambda u: (u.vis[1], u.vis[0]))
    clusters = []
    for u in items:
        if clusters:
            span = union([o.vis for o in clusters[-1]])
            ov = v_overlap(span, u.vis)
            if ov >= 0.5 * min(span[3], u.vis[3]):
                clusters[-1].append(u)
                continue
        clusters.append([u])

    rows = []
    for cl in clusters:
        cl.sort(key=lambda u: u.vis[0])
        kinds = {u.kind for u in cl}
        small = all(u.vis[2] <= 0.45 * W0 and u.role not in ("headline", "hero", "divider") for u in cl)
        if len(cl) > 1 and (kinds <= {"chip"} or kinds <= {"image"} and all(u.role == "hero" for u in cl)):
            rows.append(Row(cl, horizontal=True))
        elif 1 < len(cl) <= 3 and small and sum(u.vis[2] for u in cl) <= 0.75 * W0 \
                and len({u.anchor for u in cl}) == len(cl):
            rows.append(Row(cl, horizontal=True, spread=True))
        else:
            for u in cl:
                rows.append(Row([u]))
    prev_bottom = None
    for r in rows:
        top = min(u.vis[1] for u in r.units)
        r.gap_before = 0.0 if prev_bottom is None else max(0.0, top - prev_bottom)
        prev_bottom = max(u.vis[1] + u.vis[3] for u in r.units)
    return rows
