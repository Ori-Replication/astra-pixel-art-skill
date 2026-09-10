#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""pixelart — draw pixel art from the command line, in steps.

Everything this program needs is in the standard library, so there is no
environment to create and no package to install.  Draw by sending Python
snippets to ``exec``; each snippet is appended to a log that is replayed on
every call, so variables and canvases survive between steps without any
server or kernel process.

A step is one Python snippet, sent on stdin (or with ``--file``).  The whole
log is replayed before the new snippet runs, so anything a step bound — a
canvas, a variable, a registered colour — is still in scope in the next one.

Two rules keep replay honest: steps must be deterministic (use ``rng()``
instead of ``random``) and must not have side effects other than drawing
(a step that downloads or writes files outside the workspace will do it
again on every replay).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# An installed skill directory belongs to the host, not to us: it may be
# replaced on update or mounted read-only.  Importing the package below would
# otherwise drop a __pycache__ beside the source, so keep the interpreter from
# writing bytecode at all.
sys.dont_write_bytecode = True

from astra_pixelart import Canvas, CanvasError, Session, SessionError, __version__  # noqa: E402

CHEATSHEET = """\
ASTRA PIXEL ART — everything you can call inside a step. Nothing to import.

HOW A STEP RUNS
  `exec` replays every step so far, then runs yours; anything you bind
  (variables, imports, canvases) is still there in the next step. print()
  output comes back to you.
  A step that raises is NOT stored and leaves the drawing untouched — fix it
  and run again. There is nothing to clean up after a failure.
  Steps are replayed, so keep them deterministic (no random, no wall-clock)
  and free of side effects (no downloads, installs, or writes outside the
  workspace). Put work like that in its own command, not in a drawing step.
  Errors raise CanvasError with a message naming the fix.

CANVASES — a canvas is a layer; make one per element, composite at the end
  new_canvas(w, h, background=None, name=None) -> Canvas
      Nothing is pre-created: you choose the size. Transparent unless
      `background` is given, in which case it is filled with that colour.
      Auto-named c1, c2, ...
  canvas(ref=0) -> Canvas
      By name, by index, -1 for the newest, or a Canvas object you already
      hold. Selecting one also makes it the current canvas, which save() and
      view() default to.
  current() -> Canvas
      The canvas those two are defaulting to, without selecting anything.
  canvases() -> [{name, size, colors, current}, ...]
  flatten([layers...], name=None, background=None) -> Canvas
      Composite bottom-to-top into a NEW canvas, source-over: an opaque
      pixel hides what is below, a fully transparent one is ignored, and a
      part-transparent one blends with it. Any blended colour is registered
      in the palette like every other colour. Layers must all be the same
      size. Transparent areas let lower layers show through, which is how you
      cut holes.
  load(path, name=None, add=True) -> Canvas
      From a .pixelart.json state file or a PNG; relative paths resolve
      against the workspace. add=True (default) makes it a layer of the
      drawing; add=False only reads the file — inspecting a preview or
      checking a size — without it joining the picture.
  c.copy(name=None) -> Canvas
      Duplicate a canvas; it shares the palette.

DRAWING — every call returns the canvas it drew on, so calls chain:
    c.set(x, y, colour).line(x0, y0, x1, y1, colour)
  Every shape FILLS by default; fill=False outlines instead. Coordinates are
  0-indexed, origin top-left, y grows DOWN. Drawing outside the canvas is
  clipped silently, not an error. Sizes are inclusive: rect(x, y, w, h) covers
  w x h pixels, and a circle of radius r is 2r+1 pixels across (r=3 -> 7px).
  c.set(x, y, colour)                          one pixel
  c.rect(x, y, w, h, colour, fill=True)        w/h are the SIZE, not the far
                                               corner; both must be positive
  c.line(x0, y0, x1, y1, colour)               Bresenham, both ends included
  c.circle(cx, cy, r, colour, fill=True)
  c.ellipse(cx, cy, rx, ry, colour, fill=True)
  c.poly([(x, y), ...], colour, fill=True)     even-odd fill; >= 2 points
  c.path(d, colour, fill=True)                 curves, arcs and multi-part
                                               outlines as SVG path data:
                                               M L H V C S Q T A Z; upper case
                                               absolute, lower case relative,
                                               repeated numbers repeat the
                                               command. fill=True fills every
                                               subpath as ONE even-odd region,
                                               so a second subpath cuts a hole;
                                               fill=False strokes them open and
                                               only Z closes. Path data only -
                                               no transform, viewBox or style.
  c.text(x, y, s, colour, spacing=None, font=None)
                                               one line of text. "5x7" is
                                               ASCII at 5x7; "8px", "10px" and
                                               "12px" are three sizes of the CJK
                                               font, and they are not
                                               interchangeable (the reference
                                               lists what each one covers).
                                               font=None picks by content: ASCII
                                               uses 5x7, anything outside ASCII
                                               uses 12px. spacing=None keeps the
                                               font's own default.
  c.fill_at(x, y, colour)                      four-connected flood fill
  c.clear(x=0, y=0, w=None, h=None)            erase to transparent;
                                               defaults to the whole canvas
  c.blit(other_canvas, dx, dy)                 stamp a canvas; transparent
                                               pixels are skipped
  c.mirror(axis="h")                           "h" or "v", in place
  c.shift(dx, dy, fill=None)                   move everything; vacated pixels
                                               take `fill` (default transparent)
  c.outline(colour)                            repaint visible pixels touching
                                               transparency — the quick way to
                                               make a sprite read on any
                                               background
  c.dither(x, y, w, h, [a, b], pattern="checker")
                                               "checker" or "bayer4"; two
                                               colours faking a third tone

COLOURS — one palette per drawing, shared by every canvas
  c.color(spec, name=None) -> index
      `spec` is '#rrggbb', '#rgb', '#rrggbbaa' or an (r, g, b[, a]) tuple and
      is registered if new; it may also be the name of a colour already
      registered, or an index, to look one up. The same RGBA always reuses its
      palette entry. Naming an existing colour with a different value raises.
  c.palette() -> [{index, name, rgba}, ...]
  c.color_name(index) -> str          the registered name, else the hex
  c.get(x, y) -> index                palette index (0 = transparent);
                                      bounds-checked, unlike the drawing calls
  A colour reference is a registered name, an index, '#rrggbb', an
  (r, g, b[, a]) tuple, or None / "transparent" to ERASE. Drawing REPLACES
  the pixel, alpha included; alpha blends when flatten() or blit() composite,
  and lands in the PNG as it stands.

LOOKING AT THE RESULT — you cannot see a PNG unless you open it
  view(canvas=None, scale=None, size=None, margin=None, edges="tl") -> path
      Renders the canvas to previews/<canvas>@<spec>.png and returns the path.
      Four independent choices:
        size    None = long edge to 512, the size a vision module expects
                (this is the one to use). N = long edge to N, bigger or
                smaller. (w, h) = deliver into that exact frame, fitted
                without distortion and centred.
        scale   N = render exactly N times bigger instead. Pass scale or
                size, not both.
        margin  None = the margin that tested best (see below); 0 = none at
                all; N = exactly N canvas pixels.
        edges   where the margin goes: "tl" (default), "all", "none", or any
                of t/l/b/r.
      To look at your own work: view() — padding, then 512. Nothing to pass.
      Scaling uses nearest neighbour, so going smaller drops pixels rather
      than blurring them. Each variant gets its own file, so you can render
      several and compare them.
      Open the returned path with your image tool, if you have one — writing
      the file is not looking at it, and with no image reading the thing to
      read is matrix_text() below.

  THE MARGIN IS DELIBERATE
      One sixteenth of the long edge - 16->1px, 32->2px, 64->4px - and
      nothing at 512 or more. margin=0 is the comparison case.
      It is a viewing aid: save() never pads and coordinates do not change.

  c.stats() -> dict          size, colours used, visible pixels (alpha > 0),
                             coverage, bbox, top colours
  c.stats_text() -> str      one line: the cheap check after every change
  c.matrix_text(region=None, indices=False) -> str
      The pixels as characters plus a legend naming every colour shown, with
      its hex value and palette index — the fallback when you cannot read
      images. A letter means the same colour in every view of this drawing,
      so two dumps can be compared. `region` is (x0, y0, x1, y1) inclusive;
      keep it small, a 64x64 dump is 64 lines. indices=True puts the indices
      in the grid instead of characters, when you need exact numbers.

OUTPUT — what lands on disk
  save(name, canvas=None, scales=(1,4), indexed=False) -> {png, png_4x, state}
      Writes <name>.png (1x = one file pixel per canvas pixel), <name>@4x.png
      (4x nearest neighbour, no interpolation) and <name>.pixelart.json into
      <workspace>/exports/. Both PNGs by default. Overwrites silently, so
      re-save after every change.
      A name may carry a directory: relative stays under exports/
      ("scenes/cat" -> exports/scenes/cat.png), absolute goes where it says.
      Keep exports in the workspace unless the user asked for another
      location, and tell them the paths you wrote.
  export(name, format="ase") -> path
      The whole drawing as a layered document for another editor: one layer
      per canvas, bottom first, names and palette included, nothing
      flattened or padded. format "ase" is Aseprite (.ase/.aseprite); "ora"
      is OpenRaster (.ora), read by Krita, GIMP and MyPaint. Layered output
      is always RGBA, so no 256-colour limit; every layer has to be the size
      the drawing started at. Same directory rules as save().
  PNG only, both directions; other input formats need Pillow, not installed.
  Files are non-interlaced and transparency is kept. The default is 8-bit
  RGBA, so the colours are baked in and any reader works;
  save(name, indexed=True) writes an indexed PNG instead (the palette travels
  in the file and it is smaller past ~32x32, but at most 256 colours — it
  raises above that).
  <name>.pixelart.json holds the palette and every canvas; load() reads both
  formats back. view() is for looking, not shipping.

STEPPING BACK
  c.snapshot("before") / c.restore("before")    one canvas, within one step;
                                                restore raises if the name is
                                                unknown
  pixelart undo [N]                             drop the last N steps for good

COMMANDS (run outside a step; `pixelart` is this skill's pixelart.py)
  exec [--file F] [--scratch]   run a snippet (stdin, or a file);
                                --scratch = run it without storing it
  undo [N]                      drop the last N steps and rebuild
  log [--summary]               print the program so far — it is the drawing's
                                source of truth
  view [--size S] [--scale N] [--margin N] [--edges TL]
                                preview the current canvas: --size 512 (the
                                default), --size 128, --size 512x512 for an
                                exact frame, --scale 4 for a plain 4x
  notebook [--out F]            export the program as an .ipynb, one cell per
                                step
  reset --yes                   delete the log and all exports
  help                          this cheat sheet
  --help                        just the commands
"""

def _fmt_step(session: Session, result) -> str:
    lines = []
    state = ", ".join(
        f"{c['name']} {c['size'][0]}x{c['size'][1]}" + ("*" if c["current"] else "")
        for c in _api_state(session)
    ) or "(no canvas yet)"
    head = f"step {result.index} ok" if result.ok else "step FAILED (not saved; the drawing is unchanged)"
    lines.append(f"{head} | canvases: {state}")
    if result.stdout.strip():
        lines.append("stdout:")
        lines.extend("  " + l for l in result.stdout.rstrip().splitlines())
    if result.files:
        lines.append("files:")
        lines.extend(f"  {f}" for f in dict.fromkeys(result.files))
    for note in result.notes:
        lines.append(f"note: {note}")
    if result.error:
        lines.append(result.error.rstrip())
        lines.append(_error_hint(result.error))
    return "\n".join(lines)


def _error_hint(error: str) -> str:
    """The advice printed after a failed step.

    A NameError is usually not the snippet's fault. It means the name is not
    bound by the program that ran, which is exactly what happens after an
    ``undo`` drops the step that created it — and "fix the snippet" then sends
    the reader to the wrong place, two commands too late. A model hit this on
    the first undo it ever ran, so it gets its own sentence.
    """
    match = re.search(r"NameError: name '([^']+)' is not defined", error)
    if match:
        return (f"hint: nothing in the log defines {match.group(1)!r} — check the spelling, "
                "and check that the step which created it is still there: 'undo' drops steps "
                "for good, and 'pixelart log' prints the program that will run.")
    return "hint: fix the snippet and run exec again; the failed step was not stored."


def _api_state(session: Session):
    return [
        {"name": c.name, "size": [c.w, c.h], "current": c is session.current_canvas}
        for c in session.canvases
    ]


def _read_code(args) -> str:
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        code = sys.stdin.read()
        if code.strip():
            return code
    raise SystemExit(
        "no code given: a snippet is read from stdin, so pipe one in "
        "(or pass --file PATH)"
    )


def cmd_exec(args) -> int:
    session = Session(args.workspace)
    code = _read_code(args)
    try:
        result = session.step(code, persist=not args.scratch)
    except SessionError as e:
        print(str(e))
        return 1
    print(_fmt_step(session, result))
    return 0 if result.ok else 1


def cmd_undo(args) -> int:
    session = Session(args.workspace)
    try:
        left = session.undo(args.count)
        session.replay()
    except SessionError as e:
        print(str(e))
        return 1
    print(f"undone: {args.count} step(s); {left} step(s) left")
    return 0


def cmd_log(args) -> int:
    session = Session(args.workspace)
    try:
        print(session.history_text(with_code=not args.summary))
    except SessionError as e:
        print(str(e))
        return 1
    return 0


def _parse_size(text):
    """`512` -> 512 (long edge); `512x512` -> (512, 512) (exact frame)."""
    if text is None:
        return None
    value = str(text).strip().lower().replace(" ", "")
    if "x" in value:
        left, _, right = value.partition("x")
        try:
            return (int(left), int(right))
        except ValueError:
            raise SystemExit(f"--size wants an int or WxH, got {text!r}") from None
    try:
        return int(value)
    except ValueError:
        raise SystemExit(f"--size wants an int or WxH, got {text!r}") from None


def cmd_view(args) -> int:
    session = Session(args.workspace)
    try:
        session.replay()
        path, notes = session.render_preview(scale=args.scale, size=_parse_size(args.size),
                                             margin=args.margin, edges=args.edges)
    except SessionError as e:
        print(str(e))
        return 1
    except CanvasError as e:
        print(str(e))
        return 1
    print(path)
    for note in notes:
        print(f"note: {note}")
    print(session.current_canvas.stats_text())
    return 0


def cmd_reset(args) -> int:
    session = Session(args.workspace)
    if not args.yes:
        print("this deletes the session log and every export; re-run with --yes to confirm")
        return 1
    session.reset()
    print(f"reset: {session.log_path} is empty")
    return 0


def cmd_notebook(args) -> int:
    session = Session(args.workspace)
    out = Path(args.out) if args.out else session.root / "session.ipynb"
    out.write_text(json.dumps(session.notebook(), ensure_ascii=False, indent=1), encoding="utf-8")
    print(str(out))
    return 0


def cmd_help(args) -> int:
    print(CHEATSHEET)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pixelart",
        description="Step-by-step pixel art drawing. State lives in a replayable log, not a process.",
        epilog="Run 'pixelart help' for the full API cheat sheet.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--workspace", default=None,
                   help="workspace directory (default: ./pixelart, or $PIXELART_WORKSPACE)")
    p.add_argument("--version", action="version", version=f"astra-pixel-art {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("exec", help="run one snippet: appended to the log on success, discarded on failure")
    e.add_argument("--file", help="read the snippet from a file instead of stdin")
    e.add_argument("--scratch", action="store_true", help="run without adding the snippet to the log")
    e.set_defaults(func=cmd_exec)

    u = sub.add_parser("undo", help="drop the last N steps and rebuild the drawing")
    u.add_argument("count", nargs="?", type=int, default=1)
    u.set_defaults(func=cmd_undo)

    g = sub.add_parser("log", help="print the program so far (one entry per step)")
    g.add_argument("--summary", action="store_true", help="first line of each step only")
    g.set_defaults(func=cmd_log)

    v = sub.add_parser("view", help="preview the current canvas (vision-ready by default)")
    v.add_argument("--scale", type=int, default=None,
                   help="render exactly N times bigger (default: fit the long edge to --size)")
    v.add_argument("--size", default=None,
                   help='long edge to fit to, e.g. 512, or an exact frame like 512x512 (default: 512)')
    v.add_argument("--margin", type=int, default=None,
                   help="transparent margin in canvas pixels before scaling "
                        "(default: a sixteenth of the long edge; 0 for none)")
    v.add_argument("--edges", default="tl",
                   help='which sides carry the margin: "tl", "all", "none", or t/l/b/r')
    v.set_defaults(func=cmd_view)

    r = sub.add_parser("reset", help="delete the log and all exports")
    r.add_argument("--yes", action="store_true")
    r.set_defaults(func=cmd_reset)

    n = sub.add_parser("notebook", help="export the program as an .ipynb (one cell per step)")
    n.add_argument("--out", default=None)
    n.set_defaults(func=cmd_notebook)

    h = sub.add_parser("help", help="the API cheat sheet for code sent to exec")
    h.set_defaults(func=cmd_help)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (SessionError, CanvasError) as e:
        print(f"error: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
