---
name: astra-pixel-art
description: Draw, edit and export pixel art.
license: MIT
compatibility: Requires Python 3.9+. No packages to install and no environment to create. Optional Pillow is only for importing JPEG/GIF sources.
metadata:
  version: "0.4.0"
---

# Astra Pixel Art

Draw pixel art in steps. Each step is a short Python snippet; variables and
canvases survive between steps. Run everything from the user's working
directory so the artwork lands in their project. `PA` below is this skill's
`scripts/pixelart.py`.

## Workflow

1. **Read the API first.** `python3 "$PA" help` prints everything you can call
   and everything you can do with the session. `references/api.md`, next to
   this file, has the details worth reading before a bigger drawing.

2. **Draw, one snippet per step.** `python3 "$PA" exec` reads a snippet from
   stdin (or from `--file`) and replays the whole program first, so a name or
   a canvas you bound in an earlier step is still in scope.

3. **Look at it.** `save()` writes the deliverable: a 1× and a 4× PNG.
   `view()` renders a preview for a vision module and returns its path. If you
   cannot read images, read `c.stats_text()` and `c.matrix_text()` instead, and
   say the drawing is described, not seen. `view()` by default pads the
   drawing and puts the long edge on 512, which is what a vision model wants;
   its `size`, `scale`, `margin` and `edges` arguments change that, and `help`
   explains why the padding is there.

   A step that only looks changes nothing, so send it with `exec --scratch`
   and it stays out of the program instead of being replayed forever.

The deliverable is the PNG pair from `save()`, written to
`<cwd>/pixelart/exports/<name>.png` and `…@4x.png` (`--workspace` or
`$PIXELART_WORKSPACE` moves the `pixelart/` directory). Give the user those
paths; exports do not land in the current directory itself. A name may carry a
path when the user asked for a particular location; otherwise keep exports in
the workspace.

When the user wants to keep editing the drawing in another program, `export()`
writes the same drawing as a layered document — one layer per canvas — for
Aseprite (`"ase"`) or for Krita, GIMP and MyPaint (`"ora"`). `help` has the
rules; the layers have to share one size.

## Gotchas

- `rect` takes `w`/`h` as the size, not the far corner.
- Origin is top-left, `y` grows down.
- Shapes fill by default; `fill=False` outlines. `c.outline(colour)` outlines
  what is already drawn.
- Anything curved that `circle`/`ellipse` cannot express — Béziers, arcs,
  rounded corners, several outlines at once — is one call, `c.path(d, colour,
  fill=True)`, where `d` is SVG path data (`M L H V C S Q T A Z`, upper case
  absolute and lower case relative). It fills by default; `fill=False` strokes
  an open line. `help` and the reference have the rules.
- `c.text()` draws one line in one of four built-in fonts: `"5x7"` (ASCII,
  tiny) or `"8px"` / `"10px"` / `"12px"`, three sizes that cover Latin and
  CJK. The sizes are not interchangeable, so pick deliberately; `font=None`
  picks by content, which is the right default when you have no reason.
- A colour is a hex string, an `(r, g, b[, a])` tuple, a registered name, or
  an index. Drawing replaces a pixel; the alpha channel blends when layers are
  composited.
- `save(name)` overwrites — re-save after every change.
- A step that raises is not stored and changes nothing: fix it and run again.
- To repair a step, edit it rather than undoing it: `pixelart log` prints the
  program, and the log is plain JSONL whose lines carry each step's code.
  Editing keeps every later step; `undo` cannot bring back what it drops, and
  a later step that used a name from the dropped one fails with `NameError`.
- Every step is replayed on the next command, so keep steps deterministic
  (no `random`, no wall-clock) and free of side effects.

Read [references/api.md](references/api.md) for exact signatures, palette
helpers, and importing an existing image to pixelate.
