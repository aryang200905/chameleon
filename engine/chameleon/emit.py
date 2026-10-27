"""Write a plan back into the source schema and enforce the hard rules:
every id exactly once, nothing off-canvas (rotated elements included),
images in proportion, integer font sizes, scaled corner radii."""

import copy
import re

from .geometry import rot_aabb


def _transparent(color):
    m = re.match(r"\s*rgba?\(([^)]*)\)", str(color or ""))
    if not m:
        return "rgba(0,0,0,0)"
    parts = [p.strip() for p in m.group(1).split(",")]
    return "rgba({},{},{},0)".format(*(parts[:3] + ["0"] * (3 - len(parts[:3]))))


def emit(source, A, plan, W, H):
    out = []
    for i, el in enumerate(source["elements"]):
        e = copy.deepcopy(el)
        eid = A.order[i]
        n = A.els[eid]
        g = plan.geom.get(eid)
        if g is None:  # safety net: should not happen
            s = min(W / A.W0, H / A.H0)
            g = {"x": n["x"] * s, "y": n["y"] * s, "w": n["width"] * s, "h": n["height"] * s}
            if n.get("type") == "text":
                g["font_size"] = n["font_size"] * s
        e["x"], e["y"], e["width"], e["height"] = g["x"], g["y"], max(1.0, g["w"]), max(1.0, g["h"])
        if n.get("type") == "text":
            e["font_size"] = max(1, int(g.get("font_size", n["font_size"])))
        if "corner_radius" in g and n.get("type") == "shape":
            e["corner_radius"] = round(max(0.0, g["corner_radius"]), 2)
        if g.get("hidden"):
            e["opacity"] = 0.0
            if n.get("type") == "text":
                # the renderer ignores element opacity for text; the colour's
                # alpha is what it honours
                e["color"] = _transparent(e.get("color"))
                for run in e.get("style_runs") or []:
                    if "color" in run:
                        run["color"] = _transparent(run["color"])
        _repair(e, n, A, W, H)
        out.append(e)
    return {"canvas": {"width": int(W), "height": int(H)}, "elements": out}


_NUMERIC = ("rotation", "opacity", "z_index", "font_size", "line_height", "letter_spacing",
            "stroke_width", "corner_radius")


def _sanitize(e):
    """Sloppy input (null numbers, opacity > 1) must not leak into an
    output that fails the schema: drop nulls so defaults apply, clamp."""
    for k in _NUMERIC:
        if k in e and (not isinstance(e[k], (int, float)) or isinstance(e[k], bool)):
            del e[k]
    if "opacity" in e:
        e["opacity"] = min(1.0, max(0.0, float(e["opacity"])))
    if "z_index" in e and not isinstance(e["z_index"], int):
        e["z_index"] = int(round(e["z_index"]))
    for k in ("stroke_width", "corner_radius", "line_height"):
        if k in e and e[k] < 0:
            e[k] = 0
    if "font_weight" in e:
        e["font_weight"] = "bold" if str(e["font_weight"]).strip().lower() in _BOLD else "regular"
    for k, allowed in _ENUMS.items():
        if k in e:
            v = str(e[k]).strip().lower()
            if v in allowed:
                e[k] = v
            elif k == "shape_type":
                e[k] = "rectangle"  # required for shapes: never drop it
            else:
                del e[k]


_BOLD = {"bold", "semibold", "extrabold", "black"}
_ENUMS = {"font_style": ("normal", "italic"), "text_align": ("left", "center", "right"),
          "fit": ("contain", "cover", "fill"), "shape_type": ("rectangle", "ellipse", "line")}


def _repair(e, n, A, W, H):
    _sanitize(e)
    # images keep their source proportions (full-bleed backgrounds excepted)
    is_bg = e["width"] >= W - 0.5 and e["height"] >= H - 0.5 and e["x"] <= 0.5 and e["y"] <= 0.5
    if n.get("type") == "image" and n.get("fit", "contain") != "cover" and not is_bg and n["height"] > 0 and n["width"] > 0:
        a = n["width"] / n["height"]
        cx, cy = e["x"] + e["width"] / 2, e["y"] + e["height"] / 2
        if e["width"] / e["height"] > a:
            e["width"] = e["height"] * a
        else:
            e["height"] = e["width"] / a
        e["x"], e["y"] = cx - e["width"] / 2, cy - e["height"] / 2

    rot = float(n.get("rotation") or 0)
    aw, ah = rot_aabb(e["width"], e["height"], rot)
    bw, bh = max(aw, e["width"]), max(ah, e["height"])
    if bw > W or bh > H:
        f = min(W / bw, H / bh) * 0.999
        cx, cy = e["x"] + e["width"] / 2, e["y"] + e["height"] / 2
        e["width"] *= f
        e["height"] *= f
        if n.get("type") == "text":
            e["font_size"] = max(1, int(e["font_size"] * f))
        if "corner_radius" in e and n.get("type") == "shape":
            e["corner_radius"] = round(float(e["corner_radius"]) * f, 2)
        e["x"], e["y"] = cx - e["width"] / 2, cy - e["height"] / 2
        bw, bh = bw * f, bh * f
    cx, cy = e["x"] + e["width"] / 2, e["y"] + e["height"] / 2
    cx = min(max(cx, bw / 2), W - bw / 2)
    cy = min(max(cy, bh / 2), H - bh / 2)
    e["x"], e["y"] = cx - e["width"] / 2, cy - e["height"] / 2

    for k in ("x", "y", "width", "height"):
        e[k] = round(float(e[k]), 2)
    e["x"] = min(max(0.0, e["x"]), round(W - e["width"], 2))
    e["y"] = min(max(0.0, e["y"]), round(H - e["height"], 2))
