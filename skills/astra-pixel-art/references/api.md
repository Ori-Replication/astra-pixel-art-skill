# API reference

Everything below is preloaded inside a step — no imports needed. A snippet is
sent on stdin with `python3 "$PA" exec`, where `PA` is the path to this skill's
`scripts/pixelart.py`; `python3 "$PA" help` prints the same surface as a cheat
sheet. This file adds the details worth reading before a bigger drawing.

There are deliberately no examples here. The signatures, the rules and the
measurements are below; what to draw with them is yours to work out.

## Canvases

| Call | Returns | Notes |
| --- | --- | --- |
| `new_canvas(w, h, background=None, name=None)` | `Canvas` | A canvas **is** a layer. `background=None` starts fully transparent. Auto-named `c1`, `c2`, … |
| `canvas(ref)` | `Canvas` | By name, by index, or `-1` for the newest. Selecting a canvas also makes it current. |
| `canvases()` | `list[dict]` | `{name, size, colors, current}` per canvas. |
| `current()` | `Canvas` | The canvas `save`/`view` default to. |
| `c.copy(name=None)` | `Canvas` | Duplicate a canvas; the copy shares the drawing's palette. |
| `flatten([layers…], name=None, background=None)` | `Canvas` | Bottom-to-top composite, source-over: opaque hides, transparent passes through, part-transparent blends. |
| `load(path, name=None, add=True)` | `Canvas` | From a `.pixelart.json` state file or a PNG. `add=True` makes it a layer; `add=False` only reads the file, which is what you want when inspecting a preview. |

`save`, `view`, `stats` and friends act on the **current** canvas: the one you
last created, selected with `canvas(...)`, or made with `flatten(...)`. Pass
`canvas=` to point them at another one.

`flatten` is the layering model: one canvas is one layer, and transparent
pixels let the layers below show through. That is also how a hole is cut —
drawing `None` or `"transparent"` over part of a layer removes those pixels
rather than covering them.

## Drawing

Every drawing method returns the canvas it drew on, so calls chain, and the
value returned is always the same canvas rather than a copy.

| Method | Notes |
| --- | --- |
| `c.set(x, y, colour)` | One pixel. |
| `c.rect(x, y, w, h, colour, fill=True)` | `w`/`h` are the **size**, not the far corner. |
| `c.line(x0, y0, x1, y1, colour)` | Bresenham, both endpoints included. |
| `c.circle(cx, cy, r, colour, fill=True)` | Radius r is 2r+1 pixels across, so r=3 is a 7px disc. |
| `c.ellipse(cx, cy, rx, ry, colour, fill=True)` | |
| `c.poly([(x, y), …], colour, fill=True)` | Even-odd fill; `fill=False` closes the outline. |
| `c.path(d, colour, fill=True)` | Curves, arcs and multi-part outlines, written as SVG path data. See [Curves and paths](#curves-and-paths). |
| `c.text(x, y, s, colour, spacing=None, font=None)` | One line of text, in either of two built-in fonts. See [Text and fonts](#text-and-fonts). |
| `c.fill_at(x, y, colour)` | Four-connected flood fill. |
| `c.clear(x=0, y=0, w=None, h=None)` | Erase a region to transparent (default: everything). |
| `c.blit(other_canvas, dx, dy)` | Stamp one canvas onto another; transparent pixels are skipped. |
| `c.mirror(axis="h")` | `"h"` or `"v"`, in place. |
| `c.shift(dx, dy, fill=None)` | Moves the whole canvas; vacated pixels take `fill`. |
| `c.outline(colour)` | Repaints opaque pixels that touch transparency — the quick way to make a sprite read against any background. |
| `c.dither(x, y, w, h, [colour_a, colour_b], pattern="checker")` | `"checker"` or `"bayer4"`; the two-colour way to fake a third tone. |

Every shape **fills by default**; pass `fill=False` for an outline.

## Curves and paths

`c.path(d, colour, fill=True)` draws curves, arcs and multi-part outlines from
an **SVG path data string**. Only the path *data* grammar is accepted — there
is no `<svg>` document, no `transform`, no `viewBox`, no `stroke-width`, no
styles. Coordinates are canvas pixels (origin top-left, y down), so a path is
written in the same numbers you would use for `set()`: nothing is rescaled,
flipped or fitted, and a path taken from an icon keeps its shape and its size.

Coordinates outside the canvas are clipped in silence: no error, just a shape
that is partly or entirely missing.

| Command | Arguments | Meaning |
| --- | --- | --- |
| `M` `m` | `x y` | Move: starts a new subpath. |
| `L` `l` | `x y` | Line to. |
| `H` `h` / `V` `v` | `x` / `y` | Horizontal / vertical line. |
| `C` `c` | `x1 y1 x2 y2 x y` | Cubic Bézier: two control points, then the end. |
| `S` `s` | `x2 y2 x y` | Same, with the first control point mirrored from the previous cubic. |
| `Q` `q` | `x1 y1 x y` | Quadratic Bézier: one control point, then the end. |
| `T` `t` | `x y` | Same, with the control point mirrored from the previous quadratic. |
| `A` `a` | `rx ry rotation large-arc sweep x y` | Elliptical arc. |
| `Z` `z` | — | Close the subpath. |

Upper case is absolute, lower case is relative to the current point. A command
letter may be followed by more coordinate sets than it needs, each of which
repeats that command rather than starting a new one; after a moveto those
repeats mean lineto, as they do in SVG. Numbers do not need spaces where a
sign or a second decimal point already ends the previous one. `S` and `T`
mirror only after a matching curve command; after anything else their implied
control point sits on the current point, exactly as the SVG specification says.

**Filling.** With `fill=True` every subpath is treated as closed, and all
subpaths are filled **together**, even-odd — so a second subpath inside the
first cuts a **hole** rather than painting over it.

With `fill=False` each subpath is stroked as an **open** line, and only an
explicit `Z` closes it. That is deliberate: a curve drawn with `fill=False`
does not come back on itself unless asked. (Note the difference from
`c.poly(..., fill=False)`, which always closes the outline.)

One difference from a browser is worth knowing: SVG fills with the **nonzero**
rule by default, this fills **even-odd**. They agree on any outline that does
not cross itself, which is most of them, but a shape written as one
self-crossing stroke — a five-pointed star in the usual single-path form —
comes out with a hollow middle here and a solid one in a browser. There is no
switch: draw the crossing shape as separate non-crossing subpaths, or fill the
middle in a second call.

**Shape of the result.** Curves are subdivided until every control point sits
within 0.25 px of its chord, which is below what a hard-edged pixel grid can
show, and then rasterised with the ordinary line and fill primitives — no
anti-aliasing, like everything else here. The subdivision depends on the curve
and not on the canvas size, so the same path has the same shape at 32 px and at
512 px.

`c.circle()` and `c.ellipse()` draw one plain circle or ellipse; `path()`
covers everything else.

**Where the filled pixels stop.** A fill covers the rows between its highest
and lowest edge, and in each row it runs from the left crossing to the right
one inclusive. So a shape whose edges sit at `y=6` and `y=38` fills rows 6–37,
while one whose edges sit at `x=6` and `x=58` fills columns 6–**58**. That
asymmetry is `poly()`'s rule as well (`path(fill=True)` and the equivalent
`poly()` are pixel-identical, by test), and it means a filled outline can be
one pixel wider than the coordinates suggest, where a filled rectangle does
not.

## Text and fonts

`c.text(x, y, s, colour, spacing=None, font=None)` draws one line of text in
one of four bitmap fonts. There is no fifth: nothing is loaded from the
system, nothing is rasterised, and no font is scaled when drawn.

| `font` | Cell | Glyphs | CJK Unified Ideographs | Spacing |
| --- | --- | --- | --- | --- |
| `"5x7"` | 5×7 | A–Z, 0–9 and common punctuation; lowercase is folded to uppercase | none | 1 pixel between characters |
| `"8px"` | 8×8 | 27,977 | 14,717 of 20,992 (70.1%) | none |
| `"10px"` | 10×10 | 24,832 | 10,560 of 20,992 (50.3%) | none |
| `"12px"` | 12×12 | 36,533 | 19,214 of 20,992 (91.5%) | none |

All three CJK sizes cover Latin and Latin-1, punctuation and symbols, box
drawing, some Greek and Cyrillic, kana and hangul; they differ in how many
hanzi they carry and in how much room a glyph has. **The sizes are not
interchangeable, and smaller is not simply worse**: 8px carries eleven
thousand more hanzi than 10px, because each size was assembled from different
upstream sources. What a smaller cell costs is stroke fidelity — a character
of ten or twenty strokes has fewer pixels to be drawn in, so strokes merge and
the glyph reads as a smudge — and what it buys is room: an 8-pixel line fits
one and a half times as many characters across the same canvas as a 12-pixel
one, which is the difference between a label and a paragraph on a 64-pixel
sprite.

`font=None` chooses by content: a string of ASCII uses `"5x7"`, and a string
with any character outside ASCII uses `"12px"`, the size with the widest
coverage. Choosing per line rather than per character matters: the sizes have
different cell heights, so mixing them inside one line would put the baselines
in different places. `spacing=None` leaves each font its own default; an
integer forces one.

**Geometry.** `y` is the top of the line. The 5×7 font occupies rows `y` to
`y+6`; the cell sizes fill the whole cell down from row `y+1`, and their
baselines sit 7, 9 and 10 pixels below `y` for 8, 10 and 12px respectively. A
full-width glyph advances by the cell width and a half-width one by half of
it, so Latin text in a CJK font is proportionally narrower than hanzi.

**A character none of the sizes has** draws a filled cell, so the line keeps
its length and the gap is visible; it advances by the cell width if Unicode
calls the character East Asian wide, and half of it otherwise. What the
smaller sizes leave out is worth knowing: the 10px and 12px releases contain
six glyphs that do not fit their cell and are therefore not carried over —
four private-use codepoints (U+E100, U+E101, U+E110, U+E111) and two vertical
typography marks (U+3031, U+3032) — and the 8px release does not contain
those marks at all.

**Where they come from.** The 5×7 font is a small MIT-licensed table in
`font5x7.py`. The others are three sizes of [Fusion Pixel Font](https://github.com/TakWolf/fusion-pixel-font),
monospaced Simplified Chinese, release `2026.09.01`, under OFL-1.1 —
converted ahead of time into flat tables of bitmaps (`fontfusion8.bin`,
`fontfusion10.bin`, `fontfusion12.bin`, 13, 18 and 23 bytes per glyph, read
with a binary search on the first use of that size), because this engine has
no font rasteriser and does not want one. The upstream licence and both
notices travel with the skill; the converter is `tools/build_fusion_font.py`
in the repository.

## Colours

A colour reference is any of: a name you registered, a palette index, a hex
string (`'#rgb'`, `'#rrggbb'`, `'#rrggbbaa'`), an `(r, g, b[, a])` tuple, or
`None`/`"transparent"` for erasing.

Alpha is part of a colour, and it behaves differently depending on what you
are doing:

- **Drawing replaces.** A half-transparent colour is stored in the pixel
  as given; it does not blend with what was there. That keeps every drawing
  call idempotent and keeps the palette closed.
- **Compositing blends.** `flatten()` and `blit()` composite source-over:
  an opaque pixel hides what is below, a fully transparent one is ignored,
  and a part-transparent one blends. A blended colour is registered in the
  palette like any other, so a composite can grow the palette — by distinct
  blended colours, not by pixels.
- **Export is literal.** The alpha in the palette is what lands in the PNG.

Transparency is an alpha value, not "index 0": a colour whose alpha is zero is
still a colour that paints nothing, and `outline()`, `stats()` and the
compositors treat it as invisible rather than as content.

| Call | Notes |
| --- | --- |
| `c.color(spec, name=None)` | Returns the index. Accepts a hex/tuple (registers), an index, or a **name already registered**, which makes it a lookup. A repeated colour reuses its entry. Naming an existing colour with a different value raises. |
| `c.palette()` | `[{index, name, rgba}, …]`. |
| `c.color_name(index)` | The registered name, else the hex string. |
| `c.get(x, y)` | Palette index at a pixel (bounds-checked). |

**The palette belongs to the drawing, not to a canvas.** Every canvas in a
session shares one palette, so a colour registered while drawing one layer
is immediately usable on the next — which is what makes `flatten` a plain
index copy and keeps the exported PNG to a single coherent palette. A
layer loaded from elsewhere (an imported PNG) has its colours merged in
when it is composited.

## Looking at the result

| Call | Returns |
| --- | --- |
| `c.stats()` | `{size, colors_used, visible_pixels, coverage, bbox, top_colors}` — "visible" means alpha > 0 |
| `c.stats_text()` | One-line summary plus the top colours. |
| `c.matrix_text(region=None, indices=False)` | The pixels as characters **with a legend**, so a text-only model can actually read the drawing. `region` is `(x0, y0, x1, y1)` inclusive. The legend gives each colour's hex value and palette index; `indices=True` puts the indices in the grid itself. |
| `view(canvas=None, scale=None, size=None, margin=None, edges="tl")` | Renders a preview and returns its path. `size`: `None` puts the long edge on 512, `N` puts it on N, `(w, h)` delivers into that exact frame. `scale`: `N` renders exactly N× bigger instead. `margin`: `None` uses the tested rule, `0` none, `N` exactly N canvas pixels. `edges`: `"tl"` (default), `"all"`, `"none"`, or any of `t`/`l`/`b`/`r`. |
| `save(name, canvas=None, scales=(1, 4), indexed=False)` | `{png, png_4x, state}` | The deliverable. Writes `<name>.png`, `<name>@4x.png` and `<name>.pixelart.json` into `<workspace>/exports/`. `name` may include a directory — relative stays under `exports/`, absolute goes where it says — but keep exports in the workspace unless the user asked for another location. Overwrites on re-save. |

`matrix_text` is the fallback observation channel. Use it whenever you
cannot open images, and keep regions small (a 64×64 dump is 64 lines).

## Snapshots

`c.snapshot(name)` stores the pixels of one canvas in memory and
`c.restore(name)` brings them back; that is the undo *inside* a step. Across
steps, drop the step with `pixelart undo` instead — or, better, repair the step
in place, as described under [How a step is executed](#how-a-step-is-executed).

## Looking at the drawing

`view()` defaults to the image you should hand a vision model, not a plain
upscale: a thin transparent margin along the **top and left**, one sixteenth
of the long edge, which survives the resize as roughly 6% of the final image.

| canvas | margin | result |
| --- | --- | --- |
| 16×16 | 1 px | 512×512 |
| 32×32 | 2 px | 512×512 |
| 64×64 | 4 px | 512×512 |
| 256×256 | 16 px | 512×512 |
| 512×512 or larger | none | unchanged — there is no upscaling to break up |

A non-square canvas keeps its aspect: the long edge goes to 512 (96×32
becomes 512×191). The drawing sits in the bottom-right of the margin.

The margin is a parameter rather than a fixed policy:

| Argument | What you get |
| --- | --- |
| nothing | the margin by the rule above, long edge at 512 |
| `size=N` | the long edge at N — smaller is allowed, and nearest neighbour drops pixels instead of blurring them |
| `size=(w, h)` | an exact frame, the drawing fitted inside it without distortion and centred |
| `scale=N` | exactly N×, whatever that comes to |
| `margin=0` | no margin: the comparison case at the same size |
| `margin=N` | exactly N canvas pixels of margin |
| `edges=…` | which sides carry it: `"tl"` (the default), `"all"`, `"none"`, or any of `t`, `l`, `b`, `r` |

`size` and `scale` are alternatives; passing both raises. Every variant gets
its own file name, built from the canvas name and the size, scale, margin and
edges it used, so you can render several and compare them instead of
overwriting one.

**This is a viewing transform only.** `save()` never pads, so the delivered
PNG is exactly the canvas at 1× and 4×. Nothing about your coordinates
changes, and the step report tells you so, so that you do not shift the
drawing to compensate.

## Output format

PNG only, both directions.

| | Default | `save(..., indexed=True)` |
| --- | --- | --- |
| PNG colour type | 8-bit truecolour + alpha (RGBA) | 8-bit indexed (PLTE + tRNS) |
| Palette in the file | no — colours are baked in | yes, so pixel-art tools read it back |
| Size, 64×64 / 6 colours | 284 bytes | 212 bytes (25% smaller) |
| Size, 256×256 / 6 colours | 1276 bytes | 665 bytes (48% smaller) |
| Fails when | never | the drawing uses more than 256 colours |

Both are non-interlaced, and both are written at 1× (one file pixel per
canvas pixel) plus a 4× nearest-neighbour copy — no interpolation, so a
scaled copy stays crisp. Indexed wins from roughly 32×32 upward; on a small
sprite the PLTE/tRNS chunks cost more than the pixels they save, which is
why it is opt-in rather than the default.

`save()` also writes `<name>.pixelart.json`, which holds the palette and
every canvas in the drawing. That file is the only place the palette and
your colour names survive — a PNG has no room for them (unless you asked for
indexed, where the colours travel but the names do not). `load()` reads both
back.

## Resizing

Only integer nearest-neighbour **upscaling** is built in (`save(scales=…)`,
`view(scale=…)`). A smaller canvas is not built in: `new_canvas` plus
`get`/`set` is the whole of the API you need for it, and `view(size=…)` will
render a preview at any size you ask for without touching the drawing.

## Layered export

`save()` writes the current canvas as pixels. `export(name, format="ase")`
writes the **whole drawing** as a document another editor can open with its
layers intact, and returns the path it wrote. Every canvas becomes one layer,
in the order the drawing composites them — bottom layer first — with the
canvas names as layer names, so what arrives is the drawing in pieces rather
than a flat image. Nothing is flattened and nothing is padded.

| `format` | Extension | Written as | Read by |
| --- | --- | --- | --- |
| `"ase"` | `.ase` | Aseprite's chunked binary format, one frame, one compressed cel per layer, cropped to what that layer actually paints | Aseprite, LibreSprite, and the many importers written against its published file specification |
| `"aseprite"` | `.aseprite` | the same file under Aseprite's other extension | as above |
| `"ora"` | `.ora` | OpenRaster: a ZIP holding `stack.xml`, one PNG per layer, a thumbnail and the merged image | Krita, GIMP, MyPaint |

Anything else raises an error naming what is available.

Both formats are written **RGBA**, so neither inherits the 256-colour limit
that indexed images have, and the palette travels alongside the layers with
their names — a document export never fails because a drawing grew too many
colours. A layer with nothing visible on it becomes an empty layer rather
than being dropped.

Two rules follow from the engine rather than from the formats:

- **Every layer has to be the size the drawing started at.** A document has
  one canvas size, so exporting a drawing whose canvases differ is refused,
  naming the canvas that does not match. This is the same rule `flatten`
  applies, for the same reason.
- **The file goes where an exported name goes**, by the same rules as
  `save()`: under `<workspace>/exports/`, a name may carry a relative
  directory, and an absolute name is taken literally. A name without a
  suffix gets the format's.

Export does not read anything back. Importing someone else's layered file
would mean reproducing that program's compositing exactly, and a layer stack
that survives a round trip has to mean the same thing on both sides; that is
a different feature from handing work out. `load()` reads this skill's own
`.pixelart.json` state, which is the round-trip format.

## Importing an existing image

PNG sources need nothing extra: `load()` reads them directly, and they are
subject to the same palette rules as anything else.

JPEG and GIF need Pillow, which is **not** installed by this skill and may not
be installable at all. With Pillow available, a canvas can be built from an
image by reading its pixels and setting them with `set`; every distinct pixel
colour read that way is registered in the palette, so the palette a photo
produces has one entry per distinct colour in the photo. `load()` also takes
an `add=False` to read a file without making it a layer.

## Interop

`c.to_rgba(scale=1)` returns `(width, height, bytes)` of raw RGBA, for handing
the drawing to another library without going through a file.
`c.to_indexed(scale=1)` returns `(width, height, indices, palette)` — one byte
per pixel plus the palette — which is what `save(indexed=True)` encodes.

## How a step is executed

`exec` replays the whole log in a fresh namespace, then runs your snippet:

- on success the snippet is appended to `<workspace>/session.jsonl`;
- on failure it is discarded and the drawing is untouched — a step is
  all-or-nothing, so a crash cannot leave half a shape behind.

Two consequences worth designing around:

- **Determinism.** Replay must reproduce the same drawing. Use fixed
  values; avoid `random`, wall-clock time, and anything that depends on a
  previous run's leftovers.
- **No side effects in steps.** A step runs again on every replay, so
  downloading, installing or writing files outside the workspace will
  happen repeatedly. Do those in a separate command, not in a drawing step.

**A step can be edited, and that is usually better than undoing it.** The log
is plain JSONL: each line is a JSON object carrying the step number and the
`code` that ran, and only `code` matters. A mis-sized shape is therefore fixed
by rewriting that one line and running any command afterwards — the whole
program is replayed, and the later steps land on top of the corrected one.
Undo is for dropping work, not for repairing it, and it cannot bring back the
step it dropped. A line that stops being valid JSON is reported with its line
number and the instruction to fix or delete it.

Session commands:

| Command | Effect |
| --- | --- |
| `pixelart exec [--file F] [--scratch]` | Run a snippet (`--scratch` = do not store it). |
| `pixelart undo [N]` | Drop the last N steps and rebuild. |
| `pixelart view [--scale S] [--size S] [--margin N] [--edges E]` | Preview PNG of the current canvas. |
| `pixelart log [--summary]` | Print the program; it is the drawing's source of truth. |
| `pixelart notebook [--out F]` | Export the program as an `.ipynb`, one cell per step. |
| `pixelart reset --yes` | Delete the log and all exports. |
| `pixelart help` | The cheat sheet: the same surface, briefly. |

The workspace defaults to `./pixelart` and can be moved with
`--workspace DIR` or `$PIXELART_WORKSPACE`.
