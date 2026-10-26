"""Small rectangle helpers. A rect is a tuple (x, y, w, h)."""

import math


def area(r):
    return max(0.0, r[2]) * max(0.0, r[3])


def inter(a, b):
    ix = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    iy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    if ix <= 0 or iy <= 0:
        return 0.0
    return ix * iy


def union(rects):
    rects = [r for r in rects if r[2] > 0 and r[3] > 0]
    if not rects:
        return None
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return (x0, y0, x1 - x0, y1 - y0)


def center(r):
    return (r[0] + r[2] / 2.0, r[1] + r[3] / 2.0)


def contains_point(r, p, tol=0.0):
    return (r[0] - tol <= p[0] <= r[0] + r[2] + tol) and (r[1] - tol <= p[1] <= r[1] + r[3] + tol)


def rot_aabb(w, h, deg):
    """Size of the axis-aligned box around a w x h box rotated by deg."""
    if not deg:
        return w, h
    t = math.radians(deg)
    c, s = abs(math.cos(t)), abs(math.sin(t))
    return w * c + h * s, w * s + h * c


def v_overlap(a, b):
    return min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
