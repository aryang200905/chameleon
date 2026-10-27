"""Chameleon Studio - a live viewer for the engine.

    python -m tools.studio            # then open http://127.0.0.1:5051

Drag the canvas edge and the design re-lays itself out as you drag; every
element glides to its new place. X-ray shows the roles the engine inferred,
"Why" shows the decision it made, "All formats" lays the design out for the
whole platform catalogue at once. Standard library only; not shipped in
the scoring image.
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine.chameleon import fonts as F
from engine.chameleon.engine import run
from engine.resize import _safe_fit
from tools.suite import CATALOGUE

ROOT = Path(__file__).resolve().parents[1]
PAGE = Path(__file__).with_name("studio.html")
DESIGN_DIRS = [ROOT / "input", ROOT / "tests" / "designs"]
STATIC = {"/fonts/": ROOT / "brand_kit" / "fonts", "/assets/": ROOT / "brand_kit" / "assets"}
TYPES = {".ttf": "font/ttf", ".png": "image/png", ".jpg": "image/jpeg", ".html": "text/html; charset=utf-8"}
LOCK = threading.Lock()

TEMPLATE_NAMES = {
    "stack": "Single column",
    "split": "Media beside text",
    "row": "Banner row",
    "extras-only": "Decor only",
}
SLOT_NAMES = {"L": "Logo", "H": "Hero", "T": "Text", "C": "CTA"}


def designs():
    out = {}
    for d in DESIGN_DIRS:
        for f in sorted(d.glob("*.json")):
            out[f.stem] = f
    return out


def describe(name):
    """'row/LHTC/0.32' -> 'Banner row: Logo · Hero · Text · CTA'."""
    parts = name.split("/")
    label = TEMPLATE_NAMES.get(parts[0], parts[0])
    if parts[0] == "row" and len(parts) > 2:
        label += ": " + " · ".join(SLOT_NAMES.get(c, c) for c in parts[1])
        if "L" in parts[1]:
            label += f" (logo {float(parts[2]):.0%})"
    elif parts[0] == "split" and len(parts) > 2:
        label += f": media {parts[1]}, {float(parts[2]):.0%}"
    return label


def wrapped_lines(el):
    """Text as the renderer will break it, so the browser preview matches."""
    weight, style = F._style(el)
    content = str(el.get("content", ""))
    widths = F._widths(el.get("font_family", "Solstice Sans"), weight, style, int(el["font_size"]), content)
    return F.wrap(content, widths, float(el["width"]))


def resize_explained(source, w, h):
    try:
        with LOCK:
            design, best, top, A = run(source, {"width": w, "height": h}, debug=True)
    except Exception as exc:  # the shipped resize() falls back the same way
        return {"design": _safe_fit(source, {"width": w, "height": h}), "why": {"fallback": str(exc)}}
    for e in design["elements"]:
        if e["type"] == "text":
            e["_wrapped"] = wrapped_lines(e)
    roles = {}
    for eid in A.order:
        u = A.unit_of.get(eid)
        roles[eid] = {"role": u.role if u else "?", "unit": u.uid if u else eid}
    stepped = sorted({A.unit_of[eid].uid for eid in A.order
                      if A.unit_of.get(eid) and A.unit_of[eid].uid in best.dropped
                      and A.unit_of[eid].role != "missing"})
    why = {
        "template": describe(best.name),
        "raw": best.name,
        "scale": round(best.k, 3),
        "score": round(best.score, 3),
        "parts": {k: round(v, 3) if isinstance(v, float) else v for k, v in best.parts.items()},
        "considered": getattr(best, "considered", None),
        "finalists": [{"template": describe(p.name), "score": round(p.score, 3)} for p in
                      sorted(top, key=lambda p: -p.score)],
        "stepped_aside": [{"unit": uid, "role": next(u.role for u in A.units if u.uid == uid)} for uid in stepped],
        "missing": [u.uid for u in A.hidden],
        "align": A.align,
    }
    return {"design": design, "roles": roles, "why": why}


def render_png(design):
    from renderer.converter.convert import _get_renderer, convert_chameleon_to_crello

    r = _get_renderer(str(ROOT / "brand_kit" / "fonts"))
    clean = {"canvas": design["canvas"],
             "elements": [{k: v for k, v in e.items() if not k.startswith("_")} for e in design["elements"]]}
    ex, _ = convert_chameleon_to_crello(clean, r.font_manager, str(ROOT / "brand_kit" / "assets"))
    return r.render(ex, max_size=1400, format="png")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body, ctype="application/json", code=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if ctype == "application/json" else "max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._send(PAGE.read_bytes(), TYPES[".html"])
        if url.path == "/api/designs":
            return self._send({"designs": list(designs()), "catalogue": CATALOGUE})
        if url.path == "/api/design":
            name = parse_qs(url.query).get("name", [""])[0]
            f = designs().get(name)
            if not f:
                return self._send({"error": "unknown design"}, code=404)
            return self._send(json.loads(f.read_text()))
        for prefix, base in STATIC.items():
            if url.path.startswith(prefix):
                f = (base / url.path[len(prefix):]).resolve()
                if f.parent == base.resolve() and f.is_file():
                    return self._send(f.read_bytes(), TYPES.get(f.suffix.lower(), "application/octet-stream"))
                return self._send(b"", "text/plain", 404)
        return self._send(b"not found", "text/plain", 404)

    def do_POST(self):
        url = urlparse(self.path)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if url.path == "/api/resize":
            w, h = max(20, int(body["width"])), max(20, int(body["height"]))
            return self._send(resize_explained(body["source"], w, h))
        if url.path == "/api/render":
            png = render_png(body["design"])
            return self._send({"png": base64.b64encode(png).decode()})
        return self._send({"error": "not found"}, code=404)


def main(port=5051):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Chameleon Studio on http://127.0.0.1:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
