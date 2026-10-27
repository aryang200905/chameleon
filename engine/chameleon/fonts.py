"""Text measurement that matches the official renderer glyph-for-glyph.

The renderer wraps text greedily using skia advance widths of the brand
fonts, draws line i at y = i * font_size * line_height (top-aligned in the
box) and truncates font_size to an int. We use the same font files, the
same skia calls and the same wrap rule, so a size we pick here wraps and
renders exactly as we predicted.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
FONTS_DIR = ROOT / "brand_kit" / "fonts"

_BOLD = {"bold", "semibold", "extrabold", "black"}
_WEIGHTS = {"thin", "light", "extralight", "regular", "medium", "semibold", "bold", "extrabold", "black"}
_STYLES = {"regular", "bold", "italic", "bolditalic"}


class _BrandFonts:
    """Mirror of renderer/converter/brand_fonts.py + cr_renderer FontManager
    lookup rules, without importing cr_renderer (whose import chain pulls in
    datasets/pyarrow, which can crash under amd64 emulation). Same files,
    same family normalisation, same weight/style fallbacks."""

    _CAMEL = re.compile(r"(?<!^)(?=[A-Z])")

    def __init__(self, fonts_dir):
        self._fonts = {}
        for path in sorted(Path(fonts_dir).glob("*")):
            if path.suffix.lower() not in (".ttf", ".otf"):
                continue
            prefix, _, suffix = path.stem.partition("-")
            family = self._CAMEL.sub(" ", prefix).strip()
            suffix = suffix or "Regular"
            italic = "italic" in suffix.lower()
            wt = re.sub("italic", "", suffix, flags=re.IGNORECASE).strip().lower() or "regular"
            wt = wt if wt in _WEIGHTS else "regular"
            st = ("bolditalic" if wt == "bold" else "italic") if italic else ("bold" if wt == "bold" else "regular")
            self._fonts.setdefault(self._norm(family), []).append(
                {"fontFamily": family, "fontWeight": wt, "fontStyle": st, "bytes": path.read_bytes()})
        if not self._fonts:
            raise FileNotFoundError(fonts_dir)

    @staticmethod
    def _norm(name):
        name = name.replace("_", " ").title()
        for token in (" Bold", " Regular", " Light", " Italic", " Medium"):
            name = name.replace(token, "")
        return name

    def lookup(self, family_name, weight="regular", style="regular"):
        fam = None
        for name in (family_name, "Montserrat"):
            fam = self._fonts.get(self._norm(name))
            if fam:
                break
        if not fam:
            fam = next(iter(self._fonts.values()))
        last = family_name.replace("_", " ").lower().split(" ")[-1]
        weight = last if last in _WEIGHTS else weight
        style = last if last in _STYLES else style
        for f in fam:
            if f["fontWeight"] == weight and f["fontStyle"] == style:
                return f["bytes"]
        return fam[0]["bytes"]


try:  # pragma: no cover - exercised implicitly
    import skia  # type: ignore

    _FM = _BrandFonts(FONTS_DIR)
except Exception:  # skia / fonts unavailable -> heuristic metrics
    skia = None
    _FM = None


class TextLayout(NamedTuple):
    size: int
    lines: int
    line_widths: tuple
    width: float      # widest line
    height: float     # ink box height (top of first line to descent of last)
    ascent: float     # negative, skia convention
    descent: float
    cap: float
    overflow: bool    # a single word is wider than the wrap width


def _style(el):
    weight = str(el.get("font_weight") or "regular").lower()
    is_bold = weight in _BOLD
    is_italic = el.get("font_style") == "italic" or el.get("italic") is True
    weight = "bold" if is_bold else "regular"
    if is_bold and is_italic:
        style = "bolditalic"
    elif is_bold:
        style = "bold"
    elif is_italic:
        style = "italic"
    else:
        style = "regular"
    return weight, style


@lru_cache(maxsize=None)
def _typeface(family, weight, style):
    return skia.Typeface.MakeFromData(_FM.lookup(family, weight, style))


@lru_cache(maxsize=50000)
def _font(family, weight, style, size):
    return skia.Font(_typeface(family, weight, style), size)


@lru_cache(maxsize=200000)
def _widths(family, weight, style, size, text):
    if not text:
        return ()
    if _FM is not None:
        try:
            font = _font(family, weight, style, size)
            return tuple(font.getWidths(font.textToGlyphs(text)))
        except Exception:
            pass
    factor = 0.58 if weight == "bold" else 0.53
    return tuple((0.28 if c == " " else factor) * size for c in text)


@lru_cache(maxsize=4096)
def _metrics(family, weight, style, size):
    if _FM is not None:
        try:
            m = _font(family, weight, style, size).getMetrics()
            cap = m.fCapHeight or 0.7 * size
            return m.fAscent, m.fDescent, cap
        except Exception:
            pass
    return -0.95 * size, 0.3 * size, 0.7 * size


def wrap(text, widths, max_width):
    """Same greedy rule as renderer/converter/wrap.py: only spaces become
    newlines, an over-long word overflows its line."""
    if not text or max_width <= 0:
        return text
    chars = list(text)
    line_width = 0.0
    last_space = None
    for i, c in enumerate(chars):
        if c == "\n":
            line_width = 0.0
            last_space = None
            continue
        w = widths[i] if i < len(widths) else 0.0
        if line_width + w > max_width and last_space is not None:
            chars[last_space] = "\n"
            line_width = sum(widths[j] for j in range(last_space + 1, i + 1) if chars[j] != "\n")
            last_space = None
        else:
            line_width += w
        if c == " ":
            last_space = i
    return "".join(chars)


@lru_cache(maxsize=200000)
def _layout(content, family, weight, style, size, line_height, letter_spacing, wrap_w):
    widths = _widths(family, weight, style, size, content)
    if letter_spacing:
        widths = tuple(w + letter_spacing for w in widths)
    wrapped = wrap(content, widths, wrap_w)
    line_widths = []
    start = 0
    for i, c in enumerate(wrapped + "\n"):
        if c == "\n":
            line_widths.append(sum(widths[start:i]))
            start = i + 1
    n = max(1, len(line_widths))
    asc, desc, cap = _metrics(family, weight, style, size)
    height = (n - 1) * size * line_height + (desc - asc)
    widest = max(line_widths) if line_widths else 0.0
    return TextLayout(size, n, tuple(line_widths), widest, height, asc, desc, cap,
                      widest > wrap_w + 0.5)


def layout(el, size, wrap_w):
    size = max(1, int(size))
    weight, style = _style(el)
    return _layout(
        str(el.get("content", "")),
        el.get("font_family", "Solstice Sans"),
        weight,
        style,
        size,
        float(el.get("line_height") or 1.2),
        float(el.get("letter_spacing") or 0.0),
        round(float(wrap_w), 1),
    )


def fit(el, wrap_w, max_h, hi, lo=1):
    """Largest integer size in [lo, hi] whose wrapped text fits wrap_w x max_h."""
    hi, lo = max(1, int(hi)), max(1, int(lo))
    if lo > hi:
        lo = hi

    def ok(s):
        t = layout(el, s, wrap_w)
        return (not t.overflow) and t.height <= max_h + 0.5

    if ok(hi):
        return layout(el, hi, wrap_w)
    if not ok(lo):
        return layout(el, lo, wrap_w)
    a, b = lo, hi  # ok(a) and not ok(b)
    while b - a > 1:
        mid = (a + b) // 2
        if ok(mid):
            a = mid
        else:
            b = mid
    return layout(el, a, wrap_w)
