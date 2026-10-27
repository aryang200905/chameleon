"""Render a design's backdrop (everything except text) with PIL only.

Mirrors the official pipeline's raster rules (renderer/converter/assets.py
and shapes.py: contain/cover/fill emulation, shape rasterisation, element
opacity x fill alpha, rotation about the centre, white canvas, z order) so
the critic measures the same pixels the judge will see, without importing
cr_renderer at all.
"""

import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = ROOT / "brand_kit" / "assets"
_RGBA = re.compile(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)")


def _rgba(value, default=(200, 200, 200, 1.0)):
    m = _RGBA.match(str(value or "").strip())
    if not m:
        return default
    r, g, b = (int(float(m.group(i))) for i in (1, 2, 3))
    return r, g, b, float(m.group(4)) if m.group(4) is not None else 1.0


@lru_cache(maxsize=16)
def _asset(asset_id):
    path = ASSETS_DIR / str(asset_id)
    if not asset_id or not path.is_file():
        return None
    return Image.open(path).convert("RGBA")


def _image(el, w, h):
    img = _asset(el.get("asset_id"))
    if img is None:  # missing asset: grey placeholder, like the renderer
        return Image.new("RGBA", (w, h), (214, 214, 214, 255))
    fit = el.get("fit", "contain")
    if fit == "contain":
        s = min(w / img.width, h / img.height)
        nw, nh = max(1, round(img.width * s)), max(1, round(img.height * s))
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        r = img.resize((nw, nh), Image.BILINEAR)
        out.paste(r, ((w - nw) // 2, (h - nh) // 2), r)
        return out
    if fit == "cover":
        s = max(w / img.width, h / img.height)
        nw, nh = max(1, round(img.width * s)), max(1, round(img.height * s))
        r = img.resize((nw, nh), Image.BILINEAR)
        left, top = (nw - w) // 2, (nh - h) // 2
        return r.crop((left, top, left + w, top + h))
    return img.resize((w, h), Image.BILINEAR)


def _shape(el, w, h, scale):
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    r, g, b, _ = _rgba(el.get("fill_color"))
    kind = el.get("shape_type", "rectangle")
    if kind == "ellipse":
        d.ellipse([0, 0, w - 1, h - 1], fill=(r, g, b, 255))
    elif kind == "line":
        sr, sg, sb, _ = _rgba(el.get("stroke_color") or el.get("fill_color"), (0, 0, 0, 1.0))
        sw = max(1, round((el.get("stroke_width") or 1) * scale))
        d.line([0, h // 2, w - 1, h // 2], fill=(sr, sg, sb, 255), width=sw)
    else:
        rad = max(0, min((el.get("corner_radius") or 0) * scale, w / 2, h / 2))
        if rad > 0:
            d.rounded_rectangle([0, 0, w - 1, h - 1], radius=rad, fill=(r, g, b, 255))
        else:
            d.rectangle([0, 0, w - 1, h - 1], fill=(r, g, b, 255))
    return out


def render_backdrop(design, max_size=512):
    W, H = float(design["canvas"]["width"]), float(design["canvas"]["height"])
    s = min(1.0, max_size / max(W, H))
    canvas = Image.new("RGBA", (max(1, round(W * s)), max(1, round(H * s))), (255, 255, 255, 255))
    for el in sorted(design["elements"], key=lambda e: e.get("z_index", 0)):
        t = el.get("type")
        if t not in ("image", "shape"):
            continue
        w = max(1, round(float(el.get("width", 1)) * s))
        h = max(1, round(float(el.get("height", 1)) * s))
        alpha = float(el.get("opacity", 1.0) if el.get("opacity") is not None else 1.0)
        if t == "shape":
            alpha *= _rgba(el.get("fill_color"))[3]
            img = _shape(el, w, h, s)
        else:
            img = _image(el, w, h)
        if alpha <= 0.003:
            continue
        if alpha < 1.0:
            a = img.split()[-1].point(lambda v, k=alpha: int(v * k))
            img.putalpha(a)
        x, y = float(el.get("x", 0)) * s, float(el.get("y", 0)) * s
        rot = float(el.get("rotation") or 0)
        if rot:
            cx, cy = x + w / 2, y + h / 2
            img = img.rotate(-rot, resample=Image.BILINEAR, expand=True)
            x, y = cx - img.width / 2, cy - img.height / 2
        canvas.alpha_composite(img, (int(round(x)), int(round(y)))) if x >= 0 and y >= 0 else \
            canvas.paste(img, (int(round(x)), int(round(y))), img)
    return canvas.convert("RGB")
