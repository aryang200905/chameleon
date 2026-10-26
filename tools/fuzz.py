"""Fuzz the engine with random, awkward designs.

    python -m tools.fuzz [n]

Counts crashes, fallbacks to the safe fit, and hard-rule violations.
"""

import contextlib
import io
import random
import sys
import time

from engine.chameleon.engine import run
from tools.suite import CATALOGUE, hard_rules

ASSETS = ["solstice_bg_gradient.png", "solstice_bottle.png", "solstice_logo_lockup.png", "nope_missing.png"]
WORDS = ("golden hour botanical sparkling limited batch summer citrus wild honey sunset "
         "crafted small bright evening garden festival tickets free entry").split()


def rand_text(r, n):
    return " ".join(r.choice(WORDS) for _ in range(n)).capitalize()


def rand_design(r):
    W0, H0 = r.choice([(1080, 1080), (1080, 1350), (1080, 1920), (1920, 1080), (1200, 628), (728, 90), (300, 600)])
    els = []
    if r.random() < 0.85:
        els.append({"id": "bg", "type": r.choice(["image", "shape"]), "x": 0, "y": 0, "width": W0, "height": H0,
                    "z_index": 0, "asset_id": "solstice_bg_gradient.png", "fit": "cover",
                    "shape_type": "rectangle", "fill_color": "rgba(40,20,50,1)"})
    for i in range(r.randint(1, 18)):
        t = r.choice(["text", "text", "text", "image", "shape", "chip"])
        w = r.uniform(0.05, 0.95) * W0
        h = r.uniform(0.02, 0.4) * H0
        x = r.uniform(-0.1, 1.0) * W0
        y = r.uniform(-0.1, 1.0) * H0
        base = {"id": f"e{i}", "x": x, "y": y, "width": w, "height": h, "z_index": r.randint(1, 9)}
        if r.random() < 0.15:
            base["rotation"] = r.uniform(-30, 30)
        if r.random() < 0.2:
            base["opacity"] = r.choice([None, 0.3, 1])
        if t == "text":
            base.update(type="text", content=rand_text(r, r.choice([0, 1, 3, 8, 25, 60])),
                        font_family=r.choice(["Solstice Serif", "Solstice Sans", "Comic Neue"]),
                        font_size=r.choice([8, 12, 18, 24, 40, 72, 140]),
                        font_weight=r.choice(["regular", "bold"]), color="rgba(255,255,255,1)",
                        text_align=r.choice(["left", "center", "right"]))
            els.append(base)
        elif t == "image":
            base.update(type="image", asset_id=r.choice(ASSETS), fit=r.choice(["contain", "cover", "fill"]))
            els.append(base)
        elif t == "shape":
            base.update(type="shape", shape_type=r.choice(["rectangle", "ellipse", "line"]),
                        fill_color="rgba(232,106,58,%.2f)" % r.random(), corner_radius=r.choice([0, 10, 60]))
            els.append(base)
        else:
            g = f"g{i}"
            els.append(dict(base, id=f"e{i}_bg", type="shape", shape_type="rectangle", fill_color="rgba(232,106,58,1)",
                            corner_radius=h / 2, group_id=g, height=min(h, 0.1 * H0)))
            els.append({"id": f"e{i}_lb", "type": "text", "x": x, "y": y + 2, "width": w, "height": min(h, 0.1 * H0) - 4,
                        "z_index": 9, "content": rand_text(r, r.randint(1, 4)), "font_family": "Solstice Sans",
                        "font_size": r.choice([12, 20, 28]), "color": "rgba(255,255,255,1)", "text_align": "center",
                        "group_id": g})
    return {"canvas": {"width": W0, "height": H0}, "elements": els}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    r = random.Random(7)
    crashes = bad = 0
    t0 = time.time()
    for i in range(n):
        src = rand_design(r)
        name, w, h = r.choice(CATALOGUE)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                out = run(src, {"width": w, "height": h})
        except Exception as exc:
            crashes += 1
            print(f"#{i} {name}: {type(exc).__name__}: {exc}")
            continue
        probs = hard_rules(src, out)
        if probs:
            bad += 1
            print(f"#{i} {name}: " + "; ".join(probs[:3]))
    print(f"{n} designs in {time.time() - t0:.1f}s - engine exceptions (would use safe fit): {crashes}, rule violations: {bad}")


if __name__ == "__main__":
    main()
