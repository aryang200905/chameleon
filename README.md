# CHAMELEON — a self-critiquing layout engine

> Any design. Every canvas. Moves the furniture, never stretches the picture.

Give it a finished design and a target canvas; it returns the same design
**re-laid-out** for that canvas: same element ids, same copy, same fonts, same
assets, but new positions, sizes and type scale chosen the way a designer would.

No model, no network, deterministic, < 0.1 s per design.

```
source design ─▶ 1. READ THE DNA ─▶ 2. PROPOSE ─▶ 3. CRITIQUE ─▶ 4. LOOK ─▶ 5. REPAIR ─▶ output
                 roles, rhythm,     ~100-300       rubric-shaped    render top     hard rules
                 alignment          candidate      scoring of       candidates,    (ids, bounds,
                                    layouts        every layout     check pixels   proportions)
```

## How it works

**1. Read the design's DNA** (`engine/chameleon/analyze.py`)
The input carries no semantic labels, so the engine infers them from geometry,
typography, stacking and grouping. It identifies background, logo, hero
image(s), headline, body, legal fine print, CTA button, badges, rotated stickers,
dividers, decorations and panels. A "label sitting on a shape" counts as a
button even when it is not grouped. It also reads the *style* of the design:
alignment axis (centered/left/right), margin ratio, vertical rhythm, which
elements are pinned to the top/bottom or to a corner, header rows (logo-left /
date-right), and badges that sit *on* a product (they travel with it).

**2. Propose many layouts** (`layout.py`)
Three templates, each parameterized and searched:

| template | used for | idea |
|---|---|---|
| **stack** | tall → square → mild landscape | one column in reading order; images flex into leftover space |
| **split** | square → landscape | media on one side, text column on the other (both sides tried) |
| **row** | wide banners (≥ 2.2:1) | logo · hero · text column · CTA in one line (5 orders × 3 logo sizes) |

For each template the engine bisects the largest type scale that still fits,
then also tries smaller scales that give imagery more room. Text is measured
with **the same fonts, the same skia calls and the same greedy wrap as the
official renderer**, with integer font sizes (the renderer truncates), so what we
predict is what renders.

**3. Critique every candidate** (`critic.py`)
A scorer shaped like the judging rubric:
readability (each text's size vs. what its role needs on *this* canvas) ·
hierarchy (headline stays biggest, type order preserved) · imagery (hero/logo
presence) · brand proportion (logo vs. headline) · collisions (only
*unintended* overlaps, using visible pixels) · composition (fill, balance on the
DNA axis) · typographic rag (no one-word-per-line columns) · faithfulness (cost
of anything that had to step aside).

**4. Look at the pixels** (render-in-the-loop)
The top candidates are rendered with the official renderer (text off), and the
real pixels under every text box are measured for WCAG contrast and
busyness. The engine sees what the judge will see before it answers.

**5. Repair** (`emit.py`)
Every id exactly once, every box (rotated ones included) inside the canvas,
images at their source aspect ratio, integer font sizes, scaled corner radii.
If anything unexpected ever throws, `resize()` falls back to a safe
proportional fit, so the engine never crashes.

### Details that matter

- **Pixel-aware imagery.** Brand PNGs carry heavy transparent padding (the bottle
  art is ~43% × 69% of its canvas). Layout, spacing and collision checks use each
  image's *visible* alpha box mapped through `fit`, not the raw element box.
- **Graceful degradation.** When the canvas is too small (e.g. 728×90 with a
  140-character headline), the lowest-priority elements step aside in order:
  legal → stickers → badges → body copy, plus hero imagery on extreme banners.
  This is a designer's call, as in the brief's reference leaderboard. Headline,
  logo and CTA are never dropped. Stepped-aside elements stay in the JSON (same
  ids, inside the canvas) with `opacity: 0`. Text also gets a transparent
  colour alpha, because the renderer does not apply element opacity to text.
  The critic only drops something when readability would otherwise suffer.
- **Missing assets** (e.g. the Level 3 watermark) are hidden instead of
  rendering a "missing asset" placeholder.
- **No image regeneration, no copy changes, no font-family changes.** Only
  position, size, font size and corner radius change.

## Run it

```bash
pip install -r requirements.txt
python -m engine.main            # every task in input/ -> output/<task>_output.{json,png}
```

Docker, exactly as stage one runs it:

```bash
docker build --platform linux/amd64 -t chameleon-engine .
docker run --rm --network none -v "$(pwd)/input:/app/input" -v "$(pwd)/output:/app/output" chameleon-engine
```

No extra dependencies beyond the starter `requirements.txt`.

**Verified in Docker:** the full pipeline (engine + official renderer) runs
offline (`--network none`) in a Linux container: 8 designs in < 2 s, no
warnings. The engine itself imports **only skia + PIL**. It mirrors the
renderer's font lookup and raster rules instead of importing `cr_renderer`,
whose import chain (`datasets` → `pyarrow`) segfaults under QEMU amd64
emulation on Apple Silicon. So `resize()` also runs under plain QEMU
(~1 s/task). If you build the amd64 image on an Apple Silicon Mac, enable
Docker Desktop → *Use Rosetta for x86_64/amd64 emulation* so the starter's
PNG rendering step works too.

## How we tested it

```bash
python -m tools.suite     # every design x 18 real formats -> output/suite/*__sheet.png contact sheets
python -m tools.fuzz 600  # 600 random, deliberately awkward designs
```

- **Suite:** 3 official samples + 5 extra designs (left-aligned text-only, two-product
  gallery with a price sticker, content card/panel, landscape source, busy event
  poster with a divider and header row) × 18 formats, from Instagram Story to the
  320×50 mobile banner. 144 runs, 0 hard-rule violations.
- **Fuzz:** 600 random designs (rotations, empty text, off-canvas elements, unknown
  fonts, missing assets, content-free designs). 0 crashes, 0 rule violations.

## Layout

```
engine/
  resize.py            entry point (calls the engine, safe fallback)
  main.py              starter orchestrator (unchanged)
  chameleon/
    analyze.py         design DNA: units, roles, rows, alignment, anchors
    fonts.py           renderer-exact text measurement and fitting
    assets.py          visible (alpha) footprint of brand assets
    layout.py          blocks, flexible column, templates, extras placement
    critic.py          rubric-shaped scoring + render-in-the-loop pixel check
    backdrop.py        PIL backdrop rasteriser mirroring the renderer's rules
    emit.py            back to the schema + hard-rule repair
    engine.py          generate -> critique -> render-check -> emit
tools/                 local test harness (suite, fuzz) - not shipped in the image
tests/designs/         extra test designs used by the suite
```
