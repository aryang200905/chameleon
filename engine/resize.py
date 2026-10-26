"""
CHAMELEON engine entry point.

`resize()` hands the design to the layout engine in engine/chameleon/:
analyze the design's DNA -> generate candidate re-layouts -> score them
with a rubric-shaped critic (geometry first, then real rendered pixels) ->
emit the winner in the source schema, repaired against every hard rule.

If anything unexpected happens, a conservative proportional fit runs
instead, so the engine always returns a valid design and never crashes.
See README.md for the full write-up.
"""

import copy
import sys
import traceback
from typing import Any


def resize(source: dict[str, Any], target_canvas: dict[str, Any]) -> dict[str, Any]:
    try:
        from engine.chameleon.engine import run

        return run(source, target_canvas)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return _safe_fit(source, target_canvas)


def _safe_fit(source: dict[str, Any], target_canvas: dict[str, Any]) -> dict[str, Any]:
    """Uniform scale-to-fit, centred, background stretched to full bleed."""
    W, H = float(target_canvas["width"]), float(target_canvas["height"])
    W0, H0 = float(source["canvas"]["width"]), float(source["canvas"]["height"])
    s = min(W / W0, H / H0)
    ox, oy = (W - W0 * s) / 2, (H - H0 * s) / 2
    out = []
    for el in source["elements"]:
        e = copy.deepcopy(el)
        x, y = float(e.get("x", 0)), float(e.get("y", 0))
        w, h = float(e.get("width", 0)), float(e.get("height", 0))
        if e.get("type") in ("image", "shape") and w >= 0.9 * W0 and h >= 0.9 * H0:
            e["x"], e["y"], e["width"], e["height"] = 0.0, 0.0, W, H
        else:
            nw, nh = min(w * s, W), min(h * s, H)
            e["width"], e["height"] = nw, nh
            e["x"] = min(max(0.0, ox + x * s), W - nw)
            e["y"] = min(max(0.0, oy + y * s), H - nh)
        if e.get("type") == "text":
            e["font_size"] = max(1, int(float(e.get("font_size", 16)) * s))
        out.append(e)
    return {"canvas": {"width": int(W), "height": int(H)}, "elements": out}
