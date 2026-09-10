# Maintainer notes

## What this is

A single skill that turns "draw me a pixel-art X" into PNG files. The
whole product is `skills/astra-pixel-art/`: an entry point, one reference
document, and a standard-library engine behind a small CLI.

It is deliberately **not** a benchmark harness, not a model loop, not an
MCP server, and not a scoring system.

## The constraints that shape every decision

1. **No install step.** The engine imports only the standard library.
   Some agent sandboxes (the Claude API container, for one) have no
   network and no runtime package installation, so anything that needs
   `pip` or `uv` at run time is not a dependency — it is a failure mode.
   Optional extras are detected by import and degrade with a clear
   message; they are never required.
2. **No tool registration.** A skill cannot declare a tool: the
   `SKILL.md` frontmatter has six fields and none of them add one.
   Registering an MCP server means per-host configuration, a second
   process, and a whole class of "the server did not start" failures. The
   agent already has code execution; use it.
3. **No daemon.** No kernel, no connection file, no pid, no orphan
   processes, no timeout-versus-still-running ambiguity. State is a file.
4. **Nothing is written into the skill directory at run time.** Skill
   directories get replaced on update and may be read-only; a workspace
   (`./pixelart` by default) holds the log, previews and exports.
   This includes the interpreter's own litter: `pixelart.py` sets
   `sys.dont_write_bytecode = True` before importing the package, or the
   import machinery drops a `__pycache__` next to the source. Do not remove
   it as a "free speedup" — `tests/verify.sh` tars the shipped directory
   before and after a run and fails if a single mtime moves. Measured cost:
   about 11 ms per invocation (46 ms against 35 ms), paid once per step of a
   drawing session, and worth it to leave someone else's tree alone.

If a change would break one of these, it needs a much better argument than
convenience.

## What ships, and what stays in the repo

Two layers, and only the first one is the product:

| | Path | Audience |
| --- | --- | --- |
| **The skill** (the published unit) | `skills/astra-pixel-art/` | the agent that *uses* it |
| Everything else | repo root: `README.md`, `AGENTS.md`, `CLAUDE.md`, `tests/`, `tools/` | humans and maintenance agents |

Installation copies `skills/astra-pixel-art/` and nothing else, so that
directory has to stand on its own — hence its own `LICENSE` and
`THIRD_PARTY_NOTICES.md`: it bundles a font adapted from an MIT project,
and MIT requires the notice to travel with every copy, not just with the
git repository.

The rule for that directory is **only what the using agent needs, plus what
the licence requires**. No README (the human reads the repo one), no tests
(a maintainer clones the repo), no changelog. The body of `SKILL.md` is
loaded in full on every activation, so anything that is not an instruction
or a licence obligation is charged to the reader's context for nothing.

## No examples, and no drawing lessons

The shipped unit states the API and stops. No fenced code blocks, no worked
examples, no copyable snippets, and nothing that tells a model how to make a
picture — no advice about notches, small-size legibility, quantising a photo,
or how to shrink a canvas.

The owner's call, twice over: the model is expected to work the drawing out
from the surface (and it is better at that than at reusing someone else's
coordinates), and a worked example is a liability as well as an expense. The
reference's own heart example was a rounded cone for a while, complete with a
`# heart` comment, and a fresh model copied it and drew a pink blob. An
example can be wrong in a way prose cannot, and every reader pays for it.

What stays: the surface (signatures, argument names and defaults), the rules
that decide what a call *does* (fill semantics, the even-odd rule, the fill
boundary, the alpha behaviour, the subdivision tolerance), measurements
(byte sizes, the margin table), and the constraints of the session (replay,
determinism, no side effects, how to repair a step). Those are the API. A
statement like "a notch is the first thing to lose at small sizes" is a
drawing lesson and belongs to whoever is drawing.

`tests/verify.sh` enforces the mechanical half of this in section [27]: no
fenced block and no literal colour anywhere in the shipped docs, the cheat
sheet or the engine's own text, and — so that "no examples" does not quietly
become "no documentation" — every name in the surface must still appear in
`references/api.md` in call form, `name(`. Matching the bare word once let
`c.copy()` go undocumented while every check passed, because prose elsewhere
said "rather than a copy".

The checks that used to verify an example (does it parse, does the heart look
like a heart) were deleted with the examples, along with
`tests/doc_examples.py`, which ran the shell blocks in `SKILL.md`. If examples
are ever wanted back, those checks come back with them — `git log` has them,
in the commits before this policy.

## Frontmatter is the portable subset, on purpose

`SKILL.md` uses only the six fields the Agent Skills specification allows
(`name`, `description`, `license`, `compatibility`, `metadata`,
`allowed-tools`). Host-specific fields are rejected outright by some
targets (`Unexpected key(s) in SKILL.md frontmatter: …`), and `name` must
equal the directory name. Keep the body under 500 lines and push detail
into `references/`; the body is loaded in full whenever the skill
activates.

## Engine rules

- **`canvas.py` never imports anything outside the standard library** and
  never imports Pillow. Optional Pillow work belongs to the caller, and the
  engine never grows a second path for it. (This rule used to say "belongs in
  the reference document as a shown snippet"; there are no snippets in the
  reference any more, so the reference describes the optional route in prose
  and stops.)
- **Every shape fills by default**; `fill=False` means outline. One rule,
  stated once, so nobody has to remember per-method defaults. (An earlier
  draft defaulted circles to an outline; it produced a ring where the
  author meant a disc. Don't reintroduce per-method defaults.)
- **`None` and `"transparent"` erase.** That is how a layer gets a hole
  punched in it.
- **Drawing replaces; compositing blends.** A drawing call stores the
  colour it is given, alpha included, so it stays idempotent and the palette
  stays closed. `flatten`/`blit` composite source-over, and a blended colour
  is registered in the palette like any other. Do not "helpfully" blend at
  draw time: that would make every call depend on what was under it.
- **Transparency is an alpha, not index 0.** `c.color((255, 0, 0, 0))` is a
  red that paints nothing; `outline`, `stats` and the compositors must ask
  the palette for alpha rather than compare against 0. (An early version
  compared indices, which quietly treated that red as opaque content.)
- **One palette per drawing, shared by every canvas.** A canvas is a
  layer, not a separate picture: registering `"ink"` while drawing one
  layer must make it usable on the next. Colours are only per-canvas as
  an implementation detail of `Palette`; the session owns one and hands it
  to every canvas it creates. (Per-canvas palettes were the first draft,
  and the author's own demo hit the trap within two steps.)
- **A colour is registered once.** The same RGBA always maps to the same
  palette entry, so the palette cannot grow by accident.
- **Text views must stay readable.** `matrix_text` prints a legend;
  without it the output is a wall of `#` and useless to a text-only model.
- **Every artifact name is a function of the drawing, never of how many
  times this process ran.** `view()` used to name previews with a counter,
  so a replayed step (or a failed step, which re-replays to report the
  durable state) littered `previews/` with `view-01`, `view-02`, … and the
  name a caller got back depended on history.
- **RGBA is the default output, indexing is opt-in.** Truecolour RGBA
  cannot fail and every reader handles it; an indexed PNG carries the
  palette in the file and is smaller past ~32x32, but it breaks above 256
  colours. Making the safe format the default keeps `save()` from failing
  on a drawing that blended itself a big palette.
- **Replay must stay honest.** `session.step` appends only on success and
  re-replays after a failure so the reported state is the durable state,
  never the half-mutated one.
- **`documents.py` writes layered files and reads none.** A drawing here is
  already layered, so handing it to another editor is a writer's job, not a
  converter's: `export()` gathers the canvases, the palette and the flattened
  image into a plain :class:`Document` and one writer per format turns that
  into bytes. Importing someone else's layered file is a different feature
  and is refused on purpose — it would mean reproducing that program's
  compositing exactly, and a layer stack that survives a round trip has to
  mean the same thing on both sides. `.pixelart.json` remains the round-trip
  format.
- **Two formats, chosen for what they are read by, not for what they look
  like.** `.ase` is the pixel-art default and has a published file
  specification, so a writer can be built from the document rather than from
  reverse engineering; every cel is zlib, and `zlib` is in the standard
  library. `.ora` is the open interchange format, and it is a ZIP of PNGs
  plus `stack.xml`, which `zipfile` and the engine's own PNG encoder cover.
  Both are therefore free of the "no install step" constraint that rules out
  anything needing a renderer. Considered and not written: **PSD** (the most
  universal layered format, and the biggest — layer records, per-channel
  data, an image data section, and a documented spec long enough that a
  partial writer is a liability), **Krita `.kra`** (ORA-shaped, but Krita
  specific, and `.ora` already reaches Krita), **GIMP `.xcf`** (reverse
  engineered offsets, gzipped, no real specification), **Piskel `.piskel`**
  and **Pixelorama `.pxo`** (JSON-ish and application specific), and
  **spritesheet + JSON**, which is not layered at all.
- **Layered output is RGBA, and the palette rides along.** Aseprite's indexed
  mode exists and would shrink the cels, but it would import that format's
  256-colour limit into a drawing the engine deliberately lets grow past it,
  and the colour mode is not what carries the layers. Both writers store the
  palette with its names so the colours and their labels survive the trip.
  The Aseprite palette chunk is clamped to its practical 256 entries; the
  pixels are RGBA, so nothing is lost when a drawing has more.
- **Aseprite cels are cropped to what the layer paints, and an empty layer
  gets no cel at all.** That is what Aseprite itself writes, it keeps a
  sparse layer from costing a full-canvas blob, and it is checked in both
  directions: the suite decompresses each cel and compares it with the canvas
  it came from, including the crop origin.
- **OpenRaster lists the topmost layer first; the engine is bottom-first.**
  The specification is explicit ("The first element in a stack is the
  uppermost"), and the reversal happens in exactly one place, `write_ora`, so
  there is one line to check when a layer comes out on the wrong side.
- **Both writers are deterministic.** PNG bytes always were; the Aseprite
  header's file size is patched into place, and the ZIP entries get a fixed
  timestamp instead of the clock, so exporting twice gives identical bytes
  and a re-export in a replayed step is a no-op rather than a churn. The
  suite checks it.
- **Four fonts: one ASCII table and three CJK sizes, and the sizes are not
  interchangeable.** `"5x7"` is the borrowed ASCII table: tiny, crisp, no CJK.
  `"8px"`, `"10px"` and `"12px"` are the upstream Fusion Pixel Font sizes,
  with square cells of that size, 27,977 / 24,832 / 36,533 glyphs and 14,717 /
  10,560 / 19,214 hanzi out of the 20,992 in CJK Unified. **10px carries
  fewer hanzi than 8px** because each upstream size was assembled from
  different sources; that is a property of the release, not a bug to fix, and
  the reference states all three numbers so a caller can choose with them.
  `font=None` picks `"12px"` as soon as a string has a character outside
  ASCII, and picks per *line* rather than per character on purpose — the sizes
  have different cell heights, so mixing them within one line would put the
  baselines in different places. An explicit `font=` always wins, because the
  owner's call is that the agent chooses when it has a reason to.
- **The CJK fonts ship as bitmap tables, not as font files, and they are
  built from the BDF releases.** The engine has no rasteriser and must not
  gain one, so a TTF/OTF/WOFF2 is unusable at run time. BDF is *plain text*
  bitmaps, so `tools/build_fusion_font.py` converts it with the standard
  library alone — no Pillow, no fontTools — into one table per size
  (`fontfusion8.bin`, `fontfusion10.bin`, `fontfusion12.bin`): records sorted
  by codepoint, `<I` codepoint, `<B` advance, then the cell as a 1-bit bitmap,
  so 13, 18 and 23 bytes per glyph. `fontfusion.py` holds a `Font` per size
  and reads each table lazily on the first character drawn from it, so a
  drawing that uses one size never pays for the other two. Regenerate only
  from a pinned upstream release: the versions and the data files' sha256 are
  in the reader's docstring, and the converter checks the font it was given
  against the size it was asked for (cell *and* ascent), so a mislabelled
  download cannot pass silently.
- **Uncompressed and index-free, on purpose.** The three tables are 1.6 MB
  together and zlib would have taken them to ~350 KB, but the owner's call is
  that this size is not worth the indirection: flat tables with no header, no
  index and no decompression step are the simplest thing that can be read with
  `int.from_bytes`, and they keep the reader small enough to audit in one
  sitting. Do not "optimise" them into a compressed or delta-encoded form
  without being asked. A drawing only ever reads the one size it uses.
- **OFL-1.1 obligations travel with the data.** Fusion Pixel Font is OFL-1.1,
  and a bitmap table derived from it is a Modified Version in OFL terms: the
  licence text must accompany it, which is why
  `scripts/astra_pixelart/FUSION-PIXEL-OFL-1.1.txt` sits beside the data and
  both notices name the copyright holder. Upstream declares **no Reserved
  Font Name**, so the name stays and is credited. Nothing here is sold and the
  font is not sold alone, which is the other OFL condition. If the font is
  ever updated, update the version, the sha256, the notices and the converter
  output together.
- **A missing character draws a filled cell.** Same rule as the 5×7 font, so
  a line keeps its length and the gap is visible; the advance is the cell width
  for anything Unicode calls East Asian wide and half of it otherwise, which
  keeps a fallback from wrecking the line's shape. Glyphs that do not fit their
  cell are skipped rather than clipped, because a clipped glyph is a lie where
  a placeholder is honest: that is six glyphs at 10px and 12px (four
  private-use codepoints, U+E100, U+E101, U+E110, U+E111, and two vertical
  typography marks, U+3031 and U+3032) and none at all at 8px. The converter
  reports the count and the codepoints every time it runs.
- **Text is one line, one size, unrotated.** No wrapping, no scaling, no
  metrics beyond those tables: `text()` places glyphs at the font's own size
  and that is all, so "make the text bigger" means choosing a bigger font or
  drawing on a bigger canvas and exporting with `save(scales=…)`. That is a
  deliberate ceiling — the alternative is a text layout engine, which this is
  not.
- **`pathdata.py` is a pure module.** It parses path data and returns
  contours of float points; it never touches a `Canvas`, never imports one,
  and raises `PathDataError` (a `ValueError`) rather than `CanvasError`, so
  `canvas.py` can import it without a cycle. `Canvas.path` is the only place
  that turns a failure into the user-facing `CanvasError`, and it passes the
  parser's message through unchanged because that message already carries the
  position and the offending text.
- **`path()` borrows SVG's path *data*, and nothing else.** The grammar is
  `M L H V C S Q T A Z` in both cases, with implicit repetition and the
  `S`/`T` shorthands; `A` is the endpoint parameterisation of the spec's
  appendix F.6.5. There is no `transform`, `viewBox`, `stroke-width`, style
  or `<svg>` document, and there never should be: that is an SVG renderer,
  and it is the same "no install step" wall (a real one needs cairo or a
  browser). What is worth borrowing is the *spelling*, because a model has
  read millions of these strings and will get a curve right in one line
  instead of writing a sampling loop with its own rounding bug. An earlier
  proposal was a Python-level `cubic()` / `arc()` API; it was dropped because
  it needs a parameter per shorthand, cannot express several subpaths in one
  call, and teaches the model a signature instead of a syntax it already
  knows.
- **The subdivision tolerance is a constant, not a parameter.** Control
  points are compared against their chord and a curve is split until every
  one sits within `TOLERANCE` (0.25 px) of it, capped at `MAX_DEPTH`
  (64 segments). This bounds the error by construction, and it makes the
  shape depend on the curve rather than on the canvas size — the same path
  gives the same shape at 16 px and at 512 px. Do not add a `tolerance=`
  argument: nobody has measured a case that wants one, and the rule about
  unmeasured knobs applies.
- **`path(fill=False)` does not close the outline; `poly(fill=False)` does.**
  That asymmetry is intentional and must not be "fixed": a curve is usually
  an open stroke, and the closing edge is what you get from writing `Z`.
  `poly` keeps its behaviour because it cannot tell a polygon from a
  polyline, and closing is the useful reading for a list of points.
- **Filling several subpaths happens together, even-odd.** That is what makes
  a second subpath cut a hole, and it matches `poly`'s rule so a one-subpath
  `path` and the equivalent `poly` are pixel-identical (there is a check for
  exactly that). The cost is a documented difference from browsers, which
  default to nonzero: a self-crossing outline like a five-pointed star fills
  with a hollow middle here.
- **Curve points stay floats until a pixel is written.** Rounding the contour
  before the scanline fill makes the fill wobble from row to row.
- **A failed step's hint has to match the failure.** The generic "fix the
  snippet" line is right for a snippet that is wrong, and wrong for a
  `NameError`, which means the name is not bound by the program that ran —
  what happens after `undo` drops the step that created it. A model hit that
  on the first undo it ran and spent two commands looking in the wrong place,
  so `_error_hint` answers it with the log and the undo instead. Keep the
  generic branch for everything else.
- **The log is editable, and that is the documented way to repair a step.**
  Replay makes it true: rewriting one step's `code` and running any command
  re-runs the whole program and keeps every later step, where `undo` cannot
  bring back what it dropped. `session.segments()` validates JSON and the
  `code` key per line and names the line number, which is what makes the
  workflow safe to advertise. Do not add a command that edits the log in
  place — the file is the interface, and a model can already write it.

## The vision margin, and why `view()` is not a plain upscale

Measured, on real evaluation runs: handing a vision model a sprite upscaled
straight to 512×512 made it judge the picture worse, not better. Large perfectly
uniform colour blocks on a perfectly regular grid appear to eat ViT capacity
that should go to the drawing.

The fix that tested best is a thin transparent margin along the top and left
before scaling — one sixteenth of the long edge (16→1 px, 32→2 px, 64→4 px),
which survives the resize as ~6% of the final image. It breaks the grid
alignment and stops any one flat region from dominating. At 512 and above
nothing is added or scaled.

Rules that follow from it:

- **The margin is a viewing artefact.** `save()` must never pad: the
  deliverable is the canvas, at its own size, with nothing shifting it. The
  test suite asserts this, because it is the kind of thing that silently
  leaks when someone "unifies" the two render paths.
- **The model has to be told, where it will actually look.** `view()` returns
  the path, but a model that calls it without `print()` still needs to know,
  so the session records a note and the step report prints it. The note says
  the margin is deliberate and that coordinates are unchanged, or a model
  may "fix" a drawing that looks offset.
- **Scaling is nearest-neighbour, never interpolated.** Interpolation would
  invent colours and soften exactly the edges the artwork is made of. Going
  *smaller* is allowed and drops pixels rather than blurring them; an earlier
  draft refused to scale down at all, which was an invention of the author's
  rather than a requirement.
- **`view()` renders faithfully and takes the margin as a parameter.** Four
  knobs — `size` (long edge, or `(w, h)` for an exact frame), `scale`
  (a multiple), `margin` (pixels, `0` for none) and `edges` (which sides).
  The recommended flow is the default call, `view()`: pad, then 512. Do not
  fold the margin back into a hidden default: it was hidden once, and the
  two call sites disagreed about whether it existed.
- **Every preview file name encodes every knob.** The name is the only way to
  tell two renders of the same canvas apart, and the default and a 4x render
  collided once because `scale` was left out of it.
- **The margin stays transparent, and there is no option to make it
  otherwise.** An opaque or checkerboard margin was considered — a vision
  pipeline that flattens alpha onto black or white turns the margin into a
  large flat block, which is the thing being avoided — and the owner's call
  is that the image is passed through as it is. Do not add a `pad_color` or
  background parameter to `view()` without a measurement that asks for one.

## Verification

`bash tests/verify.sh` runs the real CLI with the system `python3`, no
virtualenv and nothing installed — that is the contract being tested, so
running it inside a prepared environment would prove less. It exits
non-zero on the first failed check. `tests/png_probe.py` decodes the
exports with Pillow when available and with an independent
standard-library reader otherwise, so the encoder is never trusted to
check itself.

There is no test framework and no test package: add checks to
`tests/verify.sh` in the same style (`check "what" actual expected`).

The suite's exit status is part of that contract, and it was **wrong** until
section [29] was written: the EXIT trap printed the tally but always exited 0,
so a failed check was invisible to anything reading the status, and a syntax
error halfway down the file printed "all checks passed" because the trap ran
with the count still at zero. `cleanup` now exits non-zero on any failure, and
a `FINISHED=1` sentinel on the last line makes a run that stopped early report
itself as a failure. Both cases are worth keeping in mind when editing this
file: a broken quote in a `grep` pattern is enough to skip every check below
it, and a marker string containing an apostrophe will do exactly that.

`tools/` holds maintainer build steps that must not ship: today that is
`build_fusion_font.py`, which turns an upstream BDF release into one of the
engine's bitmap tables — `build_fusion_font.py <release.bdf> <8px|10px|12px>`.
It runs with the standard library alone, writes into
`skills/astra-pixel-art/scripts/astra_pixelart/`, prints the sha256 the
reader's docstring is supposed to carry, and refuses a font whose cell or
ascent is not the one the size means, so a mislabelled download fails loudly
instead of producing a plausible table.

The layered writers were also checked against **other people's parsers**,
outside the suite, because a format is only correct if a program that did not
write it can read it: `aseprite` 0.1.0 (a pure-Python Aseprite reader) opened
the `.ase` as `Sprite(48, 32, RGBA, frames=1, layers=4)` with the layer names
in order, every cel cropped where the pixels are, and every cel's decompressed
data byte-identical to the engine's canvas; `pyora` did the same for the
`.ora`. Those packages are verification tools only — they are installed in a
throwaway venv for the check and are **not** dependencies of the skill, which
stays standard-library-only. The suite's own checks parse both formats by hand
for the same reason it decodes PNGs itself: the writer is never trusted to
check itself.

## Known limitations

- **Replay cost grows with the log.** Every step re-runs the whole
  program. Pixel operations are cheap (a 64×64 canvas is 4k pixels), so
  this is milliseconds in practice, but a step that decodes a large image
  is paid again on every later step. Keep heavy work in its own step and
  save the result with `save()`.
- **Determinism is a contract, not a guarantee.** A step using `random`
  or wall-clock time replays differently. The skill tells the agent this;
  the engine cannot enforce it.
- **Side effects repeat.** A step that downloads or writes files does so
  on every replay.
- **A layered export is one frame with plain layers.** The drawing has no
  timeline, so an Aseprite file carries exactly one frame and the frame
  duration is Aseprite's own default; an OpenRaster file has no frame concept
  at all. Every layer is written Normal, fully opaque and visible, because
  the engine composites source-over and nothing else — layer groups, blend
  modes and per-layer opacity are expressible in both formats and are simply
  not part of this model. Reading is not implemented either: `.pixelart.json`
  is the round-trip format, and someone else's layer stack is not.
- **No interpolation or anti-aliasing anywhere.** Drawing is integer
  rasterisation with hard edges, by design — this is pixel art.
- **`matrix_text` output is O(region)**. Asking for a whole 512×512
  canvas as text will flood the context window; the skill tells the agent
  to keep regions small, and `stats_text()` is the cheap summary.
- **Text is one line, one size and unrotated, and the CJK fonts are bitmaps of
  one upstream release.** No wrapping, no scaling, no kerning beyond the
  font's own advances. The largest size carries 36,533 characters and the most
  hanzi (91.5% of CJK Unified); the smaller sizes carry fewer or, at 10px,
  fewer hanzi than the size below them. Anything outside a table (emoji, CJK
  extensions beyond U+9FFC, and six wide glyphs the 10px and 12px cells cannot
  hold) draws a filled cell rather than a glyph. The 5×7 font is
  uppercase-only ASCII. No font can be substituted or extended at run time —
  there is no font file to load, by design.
- **PNG only, in both directions.** JPEG/GIF import needs Pillow, which
  may not be installable; the reference explains the fallback. Interlaced
  PNGs and non-8-bit depths are rejected with a clear message.
- **`path()` fills even-odd, where a browser fills nonzero.** They agree on
  every outline that does not cross itself. A self-crossing single stroke —
  the usual one-path five-pointed star — leaves a hollow middle here. This is
  a documented difference, not a bug to "fix" with a `fill_rule=` argument.
- **A path has one-pixel strokes and no stroke properties.** No
  `stroke-width`, no joins or caps, no dash arrays: `fill=False` chains the
  ordinary line primitive. A thicker outline is `c.outline(colour)`, or a
  second path offset by a pixel.
- **The polygon fill is inclusive on the right and exclusive at the bottom.**
  Rows use `min_y <= row < max_y`; within a row the pixels run from the left
  crossing to the right crossing *inclusive*. A polygon with edges at `x=6`
  and `x=58` therefore fills columns 6–58, while the same span vertically
  fills rows 6–37. `rect()` is exact on both axes. This is `poly`'s original
  rule and `path` inherits it deliberately (they are pixel-identical, and
  there is a check); the principled alternative — fill pixels whose *centre*
  falls inside, i.e. half-open on both axes — would change `poly`'s output
  and was left to the owner rather than changed in passing. If it is ever
  normalised, change both together and re-derive the pixel expectations in
  section [25] of the suite.
