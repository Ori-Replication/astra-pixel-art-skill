"""The append-only session: replay instead of a live interpreter.

Design
------
Every step the agent takes is a snippet appended to ``session.jsonl``.  To
get the state of the drawing, the session **replays the whole log** in a
fresh namespace.  That gives REPL semantics (variables, imports and
canvases survive across steps) without running a daemon:

* no process to start, connect to, keep alive, or clean up after;
* nothing to lose on a crash or a timeout — the log is on disk;
* a failed step is simply **not appended**, so it cannot corrupt the
  drawing: every step is all-or-nothing;
* the log *is* the program, so it is readable, diffable, editable and can
  be exported as a notebook.

Two rules keep replay honest, and both are stated in the API help:

1. Segments should be deterministic (use ``rng(seed)`` rather than
   ``random``, no wall-clock timing) or replay will drift.
2. Segments should not have side effects other than drawing; a segment
   that downloads, installs or writes files will do it again on every
   replay.
"""
from __future__ import annotations

import io
import json
import os
import time
import traceback
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import documents
from . import vision
from .canvas import Canvas, CanvasError, Palette, flatten

LOG_NAME = "session.jsonl"
DEFAULT_WORKSPACE = "pixelart"


def safe_name(name, what: str = "name") -> str:
    """Reject a name that would escape the workspace when used as a file name.

    The names in this API come from a model, and they end up in paths under
    ``exports/`` and ``previews/``.  A name with a separator or a ``..`` would
    write outside the workspace, which the skill promises never happens.
    """
    text = str(name).strip()
    if not text:
        raise CanvasError(f"{what} must not be empty")
    if text in (".", "..") or "/" in text or "\\" in text or "\0" in text:
        raise CanvasError(
            f"{what} {name!r} cannot be used as a file name: no path separators, "
            "no '.' or '..'. Exports and previews are always flat inside the workspace."
        )
    return text


def _preview_spec(scale, size, margin, edges: str, resolved: int) -> str:
    """Name a preview from what was actually done to it.

    Every knob goes into the name, because the name is the only way to tell
    two previews of the same canvas apart — and to compare them. An earlier
    version dropped `scale` from the name, so the default preview and a 4x
    render wrote the same file and silently overwrote each other.
    """
    if scale is not None:
        head = f"{scale}x"
    elif isinstance(size, (tuple, list)):
        head = f"{int(size[0])}x{int(size[1])}"
    else:
        head = f"long{vision.TARGET if size is None else int(size)}"
    is_default = (
        scale is None
        and (size is None or (not isinstance(size, (tuple, list)) and int(size) == vision.TARGET))
        and margin is None
        and edges == vision.DEFAULT_EDGES
    )
    return "vision" if is_default else f"{head}-m{resolved}-{edges}"


class SessionError(RuntimeError):
    """Raised for session-level problems (bad workspace, replay failure)."""


class StepResult:
    """Outcome of one executed snippet."""

    def __init__(self, index: int, ok: bool, stdout: str = "", error: Optional[str] = None,
                 files=None, notes=None) -> None:
        self.index = index
        self.ok = ok
        self.stdout = stdout
        self.error = error
        self.files: List[str] = list(files or [])
        self.notes: List[str] = list(notes or [])


class Session:
    """A workspace directory plus its append-only program."""

    def __init__(self, root=None) -> None:
        env = os.environ.get("PIXELART_WORKSPACE")
        self.root = Path(root or env or DEFAULT_WORKSPACE).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.exports = self.root / "exports"
        self.previews = self.root / "previews"
        self.log_path = self.root / LOG_NAME
        self.palette = Palette()
        self.canvases: List[Canvas] = []
        self._current: Optional[Canvas] = None
        self._files: List[str] = []
        self._notes: List[str] = []

    # ------------------------------------------------------------------
    # the program
    # ------------------------------------------------------------------
    def segments(self) -> List[dict]:
        if not self.log_path.is_file():
            return []
        out: List[dict] = []
        for lineno, line in enumerate(self.log_path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise SessionError(f"{self.log_path}:{lineno} is not valid JSON ({e}); fix or delete the log") from None
            if not isinstance(row.get("code"), str):
                raise SessionError(f"{self.log_path}:{lineno} has no 'code' string")
            out.append(row)
        return out

    def append(self, code: str) -> int:
        row = {"step": len(self.segments()) + 1, "code": code, "t": time.time()}
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row["step"]

    def undo(self, n: int = 1) -> int:
        """Drop the last ``n`` steps; returns how many steps remain."""
        rows = self.segments()
        if n < 1:
            raise SessionError("undo count must be >= 1")
        keep = rows[: max(0, len(rows) - n)]
        self._write_log(keep)
        return len(keep)

    def reset(self) -> None:
        self._write_log([])
        for stale in (self.exports, self.previews):
            if stale.is_dir():
                for f in stale.glob("*"):
                    if f.is_file():
                        f.unlink()

    def _write_log(self, rows: List[dict]) -> None:
        tmp = self.log_path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for i, row in enumerate(rows, 1):
                row = dict(row, step=i)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, self.log_path)

    # ------------------------------------------------------------------
    # the API available inside a step
    # ------------------------------------------------------------------
    def _api(self) -> Dict[str, Any]:
        def new_canvas(width: int, height: int, background=None, name: Optional[str] = None) -> Canvas:
            label = safe_name(name, "canvas name") if name is not None else f"c{len(self.canvases) + 1}"
            c = Canvas(width, height, palette=self.palette, background=background, name=label)
            self.canvases.append(c)
            self._current = c
            return c

        def _canvas(ref=0) -> Canvas:
            """Fetch a canvas and make it current: by name, by index, -1 for
            the newest, or a Canvas you already hold.

            Private only so that `save`/`view` can name their parameter
            `canvas` — which is what the docs promise and what a caller
            writes; the name is exported unchanged below.
            """
            if isinstance(ref, Canvas):
                return ref
            if isinstance(ref, int):
                if not self.canvases:
                    raise CanvasError("no canvas yet; call new_canvas(w, h) first")
                try:
                    c = self.canvases[ref]
                except IndexError:
                    raise CanvasError(
                        f"canvas index {ref} does not exist; there are {len(self.canvases)} canvas(es)"
                    ) from None
                self._current = c
                return c
            for c in self.canvases:
                if c.name == ref:
                    self._current = c
                    return c
            known = ", ".join(c.name or "?" for c in self.canvases) or "none"
            raise CanvasError(f"no canvas named {ref!r}; known canvases: {known}")

        def canvases() -> List[dict]:
            return [
                {"name": c.name, "size": [c.w, c.h], "colors": len(c.palette()),
                 "current": c is self._current}
                for c in self.canvases
            ]

        def current() -> Canvas:
            if self._current is None:
                raise CanvasError("no canvas yet; call new_canvas(w, h) first")
            return self._current

        def save(name: str, canvas=None, scales: Tuple[int, ...] = (1, 4), indexed: bool = False):
            """Write the canvas as PNG (default 1x **and** 4x) plus a reloadable state file.

            ``indexed=True`` writes indexed-colour PNGs (PLTE + tRNS) that
            carry the palette in the file and are smaller for anything past a
            small sprite; the default is 8-bit RGBA, which every reader
            handles and which cannot fail on colour count.
            """
            c = current() if canvas is None else _canvas(canvas)
            target = self._export_base(name)
            written: Dict[str, str] = {}
            for scale in scales:
                suffix = "" if scale == 1 else f"@{scale}x"
                path = target.with_name(target.name + f"{suffix}.png")
                from . import png as _png

                if indexed:
                    w, h, indices, palette = c.to_indexed(scale=scale)
                    data = _png.encode_indexed(w, h, indices, palette)
                else:
                    w, h, buf = c.to_rgba(scale=scale)
                    data = _png.encode_rgba(w, h, buf)
                # PNG bytes are deterministic, so an unchanged rerun keeps
                # the file identical instead of rewriting it.
                if not path.is_file() or path.read_bytes() != data:
                    path.write_bytes(data)
                written[f"png{'' if scale == 1 else f'_{scale}x'}"] = str(path)
                self._files.append(str(path))
            state = target.with_name(target.name + ".pixelart.json")
            payload = {"format": "astra-pixel-art-v1", "palette": self.palette.to_dict(),
                       "canvas": c.to_dict(),
                       "canvases": [x.to_dict() for x in self.canvases]}
            state.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            written["state"] = str(state)
            self._files.append(str(state))
            return written

        def view(canvas=None, scale: Optional[int] = None, size=None, margin=None, edges="tl"):
            """Render the canvas to a PNG you can look at; returns its path.

            Four independent choices, because rendering and the margin are
            separate decisions:

            size    None (default) puts the long edge on 512, the size a
                    vision module expects; an int N puts it on N, larger or
                    smaller; (w, h) delivers into that exact frame, fitted
                    without distortion and centred.
            scale   N renders exactly N times bigger instead. Pass either
                    scale or size, not both.
            margin  None (default) uses the margin that tested best, one
                    sixteenth of the long edge (16->1px, 32->2px, 64->4px,
                    nothing at 512+); 0 renders with no margin; an int is
                    taken literally, in canvas pixels.
            edges   which sides carry it: "tl" (default), "all", "none", or
                    any of t/l/b/r.

            The margin exists because large uniform colour blocks on a
            perfectly regular grid make vision models judge a picture worse:
            it breaks the grid alignment and stops one flat region from
            dominating. It is a viewing aid - save() never pads, nothing about
            your coordinates changes, and there is nothing to compensate for.

            To look at your own work: view(). Padding, then 512.
            """
            path, notes = self.render_preview(canvas, scale, size, margin, edges)
            self._files.append(path)
            self._notes.extend(notes)
            return path

        def load(path, name: Optional[str] = None, add: bool = True) -> Canvas:
            """Load a canvas from a ``.pixelart.json`` state file or a PNG.

            ``add=True`` (default) makes it a layer of this drawing, which is
            what you want for a source image or a saved state.  Pass
            ``add=False`` to just read the file — inspecting a preview, or
            checking a size — without the file becoming part of the picture.
            """
            p = Path(path)
            if not p.is_absolute():
                p = self.root / p
            if p.suffix == ".json":
                payload = json.loads(p.read_text(encoding="utf-8"))
                stored = Palette.from_dict(payload["palette"]) if payload.get("palette") else self.palette
                c = Canvas.from_dict(payload["canvas"], palette=stored)
                if stored is not self.palette:
                    # the file's palette is what its indices mean; translate
                    # them into this drawing's palette and rebind the canvas
                    mapping = [self.palette.index_of(stored.rgba(i)) for i in range(len(stored))]
                    c.px = [mapping[v] for v in c.px]
                    c.pal = self.palette
            else:
                c = Canvas.from_image(p, palette=self.palette)
            c.name = safe_name(name if name is not None else p.stem, "canvas name")
            if add:
                self.canvases.append(c)
                self._current = c
            return c

        def flatten_layers(layers, name: Optional[str] = None, background=None) -> Canvas:
            """Composite layers into a new canvas, which becomes the current one."""
            resolved = [_canvas(x) for x in layers]
            c = flatten(resolved, name=name or f"flat{len(self.canvases) + 1}", background=background)
            self.canvases.append(c)
            self._current = c
            return c

        def export(name: str, format: str = "ase"):
            """Write the whole drawing as a layered document for another editor.

            Every canvas becomes a layer, bottom first, so the file opens with
            the drawing still in pieces instead of flattened.  ``format`` is
            ``"ase"`` (Aseprite, also spelled ``.aseprite``) or ``"ora"``
            (OpenRaster, read by Krita, GIMP and MyPaint); anything else is an
            error naming what is available.  The palette travels with the
            layers, names included, and every layer has to be the size the
            drawing started at.
            """
            c = current()
            merged = flatten(self.canvases, name="merged")
            try:
                doc = documents.Document(
                    width=c.w,
                    height=c.h,
                    layers=documents.layers_of(self.canvases),
                    palette=documents.palette_entries(self.palette),
                    merged=bytes(merged.to_rgba()[2]),
                )
                path = documents.write(format, self._export_base(name), doc)
            except documents.DocumentError as exc:
                raise CanvasError(str(exc)) from None
            self._files.append(str(path))
            return str(path)

        return {
            "new_canvas": new_canvas,
            "canvas": _canvas,
            "canvases": canvases,
            "current": current,
            "flatten": flatten_layers,
            "save": save,
            "export": export,
            "view": view,
            "load": load,
            "Canvas": Canvas,
            "CanvasError": CanvasError,
        }

    def _export_base(self, name) -> Path:
        """Where an export lands, from the name the caller gave.

        A plain name goes into ``<workspace>/exports/`` — the directory the
        skill creates and the one to use unless the user asked for somewhere
        else.  A name with directories in it is honoured: relative ones stay
        under ``exports/`` (``save("scenes/cat")``), absolute ones go exactly
        where they say (``save("~/pictures/cat")`` needs expanding first).
        Parent directories are created either way.
        """
        label = str(name).strip()
        if not label:
            raise CanvasError("export name must not be empty")
        base = Path(label)
        if label.endswith(("/", os.sep)) or base.name in ("", ".", ".."):
            raise CanvasError(
                f"export name {name!r} has to end in a file name, not a directory"
            )
        if base.suffix.lower() == ".png":
            base = base.with_suffix("")          # save("cat.png") is not cat.png.png
        target = base if base.is_absolute() else self.exports / base
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    @property
    def current_canvas(self) -> Optional[Canvas]:
        """The canvas that `save`/`view` default to, or None before any exist."""
        return self._current

    # ------------------------------------------------------------------
    # previews — one implementation behind `view()` and `pixelart view`
    # ------------------------------------------------------------------
    def render_preview(self, canvas=None, scale: Optional[int] = None, size=None,
                       margin=None, edges: str = "tl"):
        """Write a preview PNG; returns ``(path, notes)``.

        The single implementation behind ``view()`` in a step and the
        ``pixelart view`` command — they were written twice once, and
        immediately disagreed about whether the margin existed.

        Order, always: pad the 1x pixels by ``margin`` source pixels on
        ``edges``, then scale by ``scale`` or to ``size``.
        """
        c = self._current if canvas is None else canvas
        if c is None:
            raise CanvasError("no canvas yet; draw something first")
        if scale is not None and size is not None:
            raise CanvasError(
                "pass scale=N or size=..., not both: scale is a multiple of the "
                "canvas, size is a target to fit it into"
            )
        if scale is not None and scale < 1:
            raise CanvasError(f"scale must be 1 or more, got {scale}")
        self.previews.mkdir(parents=True, exist_ok=True)
        from . import png as _png

        w, h, buf = c.to_rgba()
        resolved = vision.resolve_margin(max(w, h), margin)
        label = safe_name(c.name or "canvas", "canvas name")
        path = self.previews / f"{label}@{_preview_spec(scale, size, margin, edges, resolved)}.png"

        if scale is None:
            ready = vision.prepare_for_vision(w, h, buf, size=size, margin=margin, edges=edges)
            out_w, out_h, out = ready["width"], ready["height"], ready["pixels"]
            if isinstance(size, (tuple, list)):
                how = f"fitted into a {out_w}x{out_h} frame"
            elif size is not None:
                how = f"the long edge scaled to {int(size)}"
            else:
                how = f"the long edge scaled to {vision.TARGET}"
        else:
            padded_w, padded_h, padded, _ox, _oy = vision.pad(w, h, buf, resolved, edges)
            out_w, out_h = padded_w * scale, padded_h * scale
            out = _png.resize_nearest(padded_w, padded_h, padded, out_w, out_h)
            how = f"rendered at {scale}x"
        _png.write_rgba(path, out_w, out_h, out)

        notes: List[str] = []
        if resolved:
            notes.append(
                f"that preview is {out_w}x{out_h}: the drawing plus a {resolved}px transparent "
                f"margin on {vision.describe_edges(edges)}, {how}. The margin stops a vision model "
                "from reading the picture's colour blocks instead of the picture. It is a viewing "
                "aid, not part of the canvas: your coordinates are unchanged and save() output has "
                "no margin - do not shift anything to compensate."
            )
        return str(path), notes

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------
    def _fresh_namespace(self) -> Dict[str, Any]:
        ns: Dict[str, Any] = {"__name__": "pixelart_step", "StepError": CanvasError}
        ns.update(self._api())
        return ns

    def replay(self) -> Dict[str, Any]:
        """Rebuild the drawing by running every stored step in order.

        Output printed by a *stored* step is swallowed: the report the caller
        prints should show what this step did, not everything the program has
        ever printed.  Returns the namespace the steps ran in.
        """
        self.palette = Palette()
        self.canvases = []
        self._current = None
        self._files = []
        self._notes = []
        ns = self._fresh_namespace()
        out = io.StringIO()
        with redirect_stdout(out):
            for row in self.segments():
                try:
                    exec(compile(row["code"], f"<step {row['step']}>", "exec"), ns)
                except Exception:
                    raise SessionError(
                        f"stored step {row['step']} failed during replay; "
                        "run 'pixelart undo' to drop it, or 'pixelart reset' to start over"
                        f"\n{traceback.format_exc()}"
                    ) from None
        return ns

    def step(self, code: str, persist: bool = True) -> StepResult:
        """Run one snippet against the replayed state.

        With ``persist`` the snippet is appended to the log when it
        succeeds; a failing snippet is never stored, so the durable state
        cannot end up half-changed.
        """
        ns = self.replay()
        # files written while replaying earlier steps are not this step's
        # output, so start the list fresh now that the state is rebuilt
        self._files = []
        self._notes = []
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                exec(compile(code, "<step>", "exec"), ns)
        except Exception:
            error = traceback.format_exc()
            self.replay()  # report the durable state, not the half-mutated one
            return StepResult(0, False, buf.getvalue(), error, [])
        index = self.append(code) if persist else len(self.segments())
        return StepResult(index, True, buf.getvalue(), None, list(self._files), list(self._notes))

    def history_text(self, with_code: bool = True) -> str:
        rows = self.segments()
        if not rows:
            return "no steps yet"
        lines = [f"{len(rows)} step(s) in {self.log_path}:"]
        for row in rows:
            code = row["code"].strip().splitlines()
            head = code[0][:70] + ("..." if len(code[0]) > 70 else "")
            lines.append(f"  {row['step']:>3}. {head}")
            if with_code:
                for extra in code[1:]:
                    lines.append(f"       {extra[:70]}")
        return "\n".join(lines)

    def notebook(self) -> dict:
        """Export the program as an .ipynb (one cell per step) for review."""
        cells = []
        for row in self.segments():
            cells.append({
                "cell_type": "code",
                "execution_count": row["step"],
                "metadata": {},
                "outputs": [],
                "source": row["code"].rstrip("\n").splitlines(keepends=True),
            })
        return {
            "cells": cells,
            "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
