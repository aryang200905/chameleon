"""Pixel-aware knowledge of the brand assets.

Brand PNGs carry a lot of transparent padding (the bottle's visible art is
~43% x 69% of its canvas). Laying out raw boxes wastes that space and
mis-measures collisions, so the engine works with each image's *visible*
footprint: the alpha bounding box, mapped through the element's `fit`.
"""

from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = ROOT / "brand_kit" / "assets"


@lru_cache(maxsize=None)
def info(asset_id):
    """(exists, img_w, img_h, (l, t, r, b) visible fractions of the image)."""
    if not asset_id:
        return False, 1, 1, (0.0, 0.0, 1.0, 1.0)
    path = ASSETS_DIR / str(asset_id)
    if not path.is_file():
        return False, 1, 1, (0.0, 0.0, 1.0, 1.0)
    try:
        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                alpha = im.convert("RGBA").split()[-1].point(lambda a: 255 if a > 16 else 0)
                bbox = alpha.getbbox()
                if bbox:
                    l, t, r, b = bbox
                    return True, w, h, (l / w, t / h, r / w, b / h)
            return True, w, h, (0.0, 0.0, 1.0, 1.0)
    except Exception:
        return True, 1, 1, (0.0, 0.0, 1.0, 1.0)


def exists(asset_id):
    return info(asset_id)[0]


def visible_frac(el):
    """Visible rect of an image element as fractions of its own box:
    (fx, fy, fw, fh). Depends only on the box aspect + fit, so it is
    constant under uniform scaling."""
    ok, iw, ih, (l, t, r, b) = info(el.get("asset_id"))
    bw, bh = float(el.get("width") or 1), float(el.get("height") or 1)
    if bw <= 0 or bh <= 0:
        return 0.0, 0.0, 1.0, 1.0
    fit = el.get("fit", "contain")
    if not ok or fit == "cover":
        return 0.0, 0.0, 1.0, 1.0
    if fit == "fill":
        return l, t, r - l, b - t
    s = min(bw / iw, bh / ih)
    dw, dh = iw * s, ih * s
    ox, oy = (bw - dw) / 2.0, (bh - dh) / 2.0
    x0, y0 = ox + l * dw, oy + t * dh
    x1, y1 = ox + r * dw, oy + b * dh
    return x0 / bw, y0 / bh, max(1e-3, (x1 - x0) / bw), max(1e-3, (y1 - y0) / bh)


def visible_rect(el, x, y, w, h):
    fx, fy, fw, fh = visible_frac(el)
    return (x + fx * w, y + fy * h, fw * w, fh * h)
