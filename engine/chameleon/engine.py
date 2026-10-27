"""Generate -> critique -> render-check -> emit."""

import math

from . import critic
from .analyze import analyze
from .emit import emit
from .layout import Ctx, Plan, place_extras, tpl_row, tpl_split, tpl_stack

TOP_PIXEL = 6


def _search(f, lo, hi, iters=14):
    """Largest k in [lo, hi] for which f(k) is feasible (geometric bisection)."""
    if f(hi) is not None:
        return hi
    if f(lo) is None:
        return None
    for _ in range(iters):
        mid = math.sqrt(lo * hi)
        if f(mid) is not None:
            lo = mid
        else:
            hi = mid
    return lo


def drop_variants(ctx):
    A, W, H = ctx.A, ctx.W, ctx.H
    seq = [
        [u for u in A.units if u.role == "legal"],
        [u for u in A.units if u.role == "sticker"],
        [u for u in A.units if u.role == "badge"],
        [u for u in A.units if u.role == "group"],
        [u for u in A.units if u.role == "body"],
    ]
    extreme = ctx.extreme or W / H >= 5 or H / W >= 5 or min(W, H) <= 120
    if extreme:
        seq.append([u for u in A.units if u.role == "hero"])
    if ctx.extreme:
        seq.append([u for u in A.units if u.role in ("cta_text", "divider")])
    out, acc = [frozenset()], set()
    for step in seq:
        if step:
            acc |= {u.uid for u in step}
            out.append(frozenset(acc))
    return out


def templates(ctx):
    a = ctx.W / ctx.H
    out = []
    if a <= 2.6:
        out += [(tpl_stack, {"img_cap": c}) for c in (0.42, 0.6)]
    if a >= 0.75:
        out += [(tpl_split, {"side": s, "frac": f}) for s in ("left", "right") for f in (0.4, 0.5)]
    if a >= 2.2:
        out += [(tpl_row, {"order": o, "logo_frac": lf})
                for o in ("LHTC", "HLTC", "LTHC", "LTC", "HTC") for lf in (0.32, 0.55, 0.8)]
    return out


def candidates(ctx):
    A = ctx.A
    if not A.rows:
        # nothing to flow (background / decoration only): re-anchor extras
        p = Plan(ctx.W, ctx.H, "extras-only")
        place_extras(p, ctx, set(), ())
        p.settle(A)
        p.k = 1.0
        return [p]
    tags = tuple(u.uid for u in A.corners if u.kind == "chip" and not u.rotation)
    k_hi = max(0.05, 1.15 * ctx.S)
    out = []
    for drop in drop_variants(ctx):
        for tpl, kw in templates(ctx):
            inl_opts = [()]
            if tags and tpl is not tpl_stack:
                inl_opts.append(tuple(t for t in tags if t not in drop))
            for inl in inl_opts:
                def f(k, tpl=tpl, kw=kw, inl=inl, drop=drop):
                    return tpl(ctx, k, set(drop), inline=inl, **kw)

                kmax = _search(f, 0.02, k_hi)
                if kmax is None:
                    continue
                for kk in (kmax, kmax * 0.86, kmax * 0.72):
                    p = f(kk)
                    if p is None:
                        continue
                    place_extras(p, ctx, set(drop), inl)
                    p.settle(A)
                    p.k = kk
                    out.append(p)
    return out


def run(source, target_canvas, debug=False):
    W, H = float(target_canvas["width"]), float(target_canvas["height"])
    A = analyze(source)
    ctx = Ctx(A, W, H)
    cands = candidates(ctx)
    for relax, extreme in ((0.75, False), (0.55, True), (0.35, True), (0.2, True)):
        if cands:
            break
        # degradation ladder: loosen readability floors and allow deeper
        # drops rather than giving up on a designed layout
        ctx = Ctx(A, W, H, relax=relax, extreme=extreme)
        cands = candidates(ctx)
    if not cands:
        raise RuntimeError("no feasible layout")
    for p in cands:
        critic.geometric(p, ctx)
    cands.sort(key=lambda p: -p.score)
    # keep the pixel stage diverse: best plan per template family
    seen, top = set(), []
    for p in cands:
        fam = p.name.split("/")[0] + str(sorted(p.dropped))
        if fam in seen and len(top) >= 3:
            continue
        seen.add(fam)
        top.append(p)
        if len(top) >= TOP_PIXEL:
            break
    designs = {}
    for p in top:
        designs[id(p)] = emit(source, A, p, W, H)
        critic.pixel(p, designs[id(p)], ctx)
    best = max(top, key=lambda p: p.score)
    best.considered = len(cands)
    if debug:
        return designs[id(best)], best, top, A
    return designs[id(best)]
