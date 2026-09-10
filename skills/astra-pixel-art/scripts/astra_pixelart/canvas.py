"""The pixel canvas: indexed pixels, a palette, and drawing primitives.

One :class:`Canvas` is one layer.  A drawing is usually several canvases
composited with :func:`flatten`, which is how layers/overlays are done
without any dedicated layer machinery.

Pure Python on purpose: no NumPy, no Pillow, nothing to install.  A flat
``list[int]`` of palette indices is fast enough for pixel art (a 512x512
canvas is 256k ints) and keeps the engine runnable in a bare container.
"""
from __future__ import annotations

import math
import string
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import font5x7
from . import fontfusion
from . import pathdata
from . import png

RGBA = Tuple[int, int, int, int]
ColorRef = "int | str | tuple"


class CanvasError(ValueError):
    """Raised for invalid canvas input, with a message the caller can act on."""


def parse_color(spec) -> RGBA:
    """Turn ``'#rrggbb'``, ``'#rgb'``, ``'#rrggbbaa'`` or an RGB(A) tuple into RGBA."""
    if isinstance(spec, str):
        s = spec.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        if len(s) == 6:
            s += "ff"
        if len(s) != 8 or any(c not in "0123456789abcdefABCDEF" for c in s):
            raise CanvasError(f"colour must be '#rgb', '#rrggbb' or '#rrggbbaa', got {spec!r}")
        v = [int(s[i:i + 2], 16) for i in range(0, 8, 2)]
        return (v[0], v[1], v[2], v[3])
    if isinstance(spec, (tuple, list)):
        v = [int(c) for c in spec]
        if len(v) == 3:
            v.append(255)
        if len(v) != 4 or any(not 0 <= c <= 255 for c in v):
            raise CanvasError(f"colour tuple must be 3 or 4 values in 0..255, got {spec!r}")
        return (v[0], v[1], v[2], v[3])
    raise CanvasError(f"cannot read colour {spec!r}; use '#rrggbb' or an (r, g, b[, a]) tuple")


def hex_of(rgba: RGBA) -> str:
    return "#%02x%02x%02x%02x" % rgba


def over(src: RGBA, dst: RGBA) -> RGBA:
    """Source-over composite of two non-premultiplied RGBA colours.

    Used when layers are merged.  For the two common cases this is just a
    choice between the operands — a fully opaque source hides what is
    beneath it and a fully transparent one is ignored — so ``flatten``
    keeps its old "topmost opaque pixel wins" behaviour, and only genuinely
    part-transparent pixels blend.
    """
    sa = src[3] / 255.0
    if sa >= 1.0:
        return src
    if sa <= 0.0:
        return dst
    da = dst[3] / 255.0
    oa = sa + da * (1.0 - sa)
    if oa <= 0.0:
        return (0, 0, 0, 0)
    rgb = tuple(
        int(round((src[i] * sa + dst[i] * da * (1.0 - sa)) / oa)) for i in range(3)
    )
    return (rgb[0], rgb[1], rgb[2], int(round(oa * 255)))


class Palette:
    """The colours of a drawing, shared by every canvas (layer) in it.

    Pixel art has one palette for the whole picture, and layers index into
    it — registering ``"ink"`` once must not make it unknown on the next
    layer.  So the palette lives beside the canvases, not inside one of
    them: a session creates one and hands it to every canvas it makes.
    Index 0 is always fully transparent.
    """

    def __init__(self) -> None:
        self.colors: List[RGBA] = [(0, 0, 0, 0)]
        self.names: Dict[str, int] = {"transparent": 0}
        self.by_rgba: Dict[RGBA, int] = {(0, 0, 0, 0): 0}

    def register(self, spec, name: Optional[str] = None) -> int:
        """Look up or add a colour and return its index."""
        if isinstance(spec, int):
            if not 0 <= spec < len(self.colors):
                raise CanvasError(f"palette index {spec} does not exist (0..{len(self.colors) - 1})")
            return spec
        if isinstance(spec, str) and spec in self.names:
            if name is not None and name != spec:
                raise CanvasError(
                    f"{spec!r} is already registered; use a different name or the existing one"
                )
            return self.names[spec]
        rgba = parse_color(spec)
        if name is not None and name in self.names:
            existing = self.names[name]
            if self.colors[existing] != rgba:
                raise CanvasError(
                    f"colour name {name!r} already means {hex_of(self.colors[existing])}; "
                    "pick another name or use the existing colour"
                )
            return existing
        idx = self.by_rgba.get(rgba)
        if idx is None:
            idx = len(self.colors)
            self.colors.append(rgba)
            self.by_rgba[rgba] = idx
        if name is not None:
            self.names[name] = idx
        return idx

    def index_of(self, ref) -> int:
        """Resolve name / index / hex / tuple / None (erase) to an index."""
        if ref is None:
            return 0
        if isinstance(ref, bool):
            raise CanvasError(f"{ref!r} is not a colour")
        if isinstance(ref, int):
            return self.register(ref)
        if isinstance(ref, str):
            if ref in self.names:
                return self.names[ref]
            if ref.strip().lower() in ("transparent", "none"):
                return 0
            if not ref.startswith("#"):
                known = ", ".join(sorted(n for n in self.names if n != "transparent")[:8]) or "none yet"
                raise CanvasError(
                    f"unknown colour {ref!r}: register it first with "
                    f"colour('#rrggbb', name={ref!r}), or pass a hex string like '#rrggbb'. "
                    f"Known names: {known}"
                )
        return self.register(ref)

    def name_of(self, index: int) -> Optional[str]:
        return next((n for n, i in self.names.items() if i == index), None)

    def label(self, index: int) -> str:
        return self.name_of(index) or hex_of(self.colors[index])

    def rgba(self, index: int) -> RGBA:
        return self.colors[index]

    def to_list(self) -> List[dict]:
        return [
            {"index": i, "name": self.label(i), "rgba": list(rgba)}
            for i, rgba in enumerate(self.colors)
        ]

    def to_dict(self) -> dict:
        return {"colors": [list(c) for c in self.colors], "names": dict(self.names)}

    @classmethod
    def from_dict(cls, d: dict) -> "Palette":
        pal = cls()
        pal.colors = [tuple(int(v) for v in rgba) for rgba in d["colors"]]
        pal.by_rgba = {rgba: i for i, rgba in enumerate(pal.colors)}
        pal.names = {str(k): int(v) for k, v in (d.get("names") or {}).items()}
        return pal

    def __len__(self) -> int:
        return len(self.colors)


class Canvas:
    """An indexed-colour pixel grid.

    Index ``0`` is always fully transparent.  Drawing methods accept a
    colour reference (a registered name, an index, ``'#rrggbb'`` or an
    RGB(A) tuple) and return ``self`` so calls can be chained.
    """

    def __init__(self, width: int, height: int, palette: Optional[Palette] = None,
                 background=None, name: Optional[str] = None) -> None:
        if width <= 0 or height <= 0:
            raise CanvasError(f"canvas size must be positive, got {width}x{height}")
        self.w = int(width)
        self.h = int(height)
        self.name = name
        if palette is not None and not isinstance(palette, Palette):
            raise CanvasError(
                f"palette must be a Palette, got {type(palette).__name__}; "
                "to fill a new canvas pass background=... instead"
            )
        self.pal = palette if palette is not None else Palette()
        self.px: List[int] = [0] * (self.w * self.h)
        self._snapshots: Dict[str, List[int]] = {}
        if background is not None:
            self.rect(0, 0, self.w, self.h, background)

    # ------------------------------------------------------------------
    # palette
    # ------------------------------------------------------------------
    def color(self, spec, name: Optional[str] = None) -> int:
        """Look up or register a colour and return its index.

        Accepts a hex string or an RGB(A) tuple (registering it if new), a
        palette index, or the name of a colour already registered, so naming
        an existing colour is a lookup while giving ``name=`` alongside a
        value is a registration.  An already-known colour reuses its index, so
        repeating a hex string never grows the palette.  The palette is
        shared by every canvas in the drawing, so a name registered while
        drawing one layer is usable on the next.
        """
        return self.pal.register(spec, name)

    def color_name(self, index: int) -> str:
        return self.pal.label(index)

    def palette(self) -> List[dict]:
        return self.pal.to_list()

    def _cid(self, ref) -> int:
        """Resolve a colour reference (name, index, hex, tuple or None) to an index.

        ``None`` and ``"transparent"`` mean index 0, so a shape can erase
        instead of paint — the natural way to punch a hole in a layer (an
        eye, a crescent, a window).
        """
        return self.pal.index_of(ref)

    # ------------------------------------------------------------------
    # raw pixel access
    # ------------------------------------------------------------------
    def get(self, x: int, y: int) -> int:
        if not (0 <= x < self.w and 0 <= y < self.h):
            raise CanvasError(f"({x}, {y}) is outside {self.w}x{self.h}")
        return self.px[y * self.w + x]

    def _put(self, x: int, y: int, cid: int) -> bool:
        """Write one pixel if in bounds; returns whether it changed."""
        if 0 <= x < self.w and 0 <= y < self.h:
            i = y * self.w + x
            if self.px[i] != cid:
                self.px[i] = cid
                return True
        return False

    # ------------------------------------------------------------------
    # primitives
    # ------------------------------------------------------------------
    def set(self, x: int, y: int, color) -> "Canvas":
        self._put(int(x), int(y), self._cid(color))
        return self

    def rect(self, x: int, y: int, w: int, h: int, color, fill: bool = True) -> "Canvas":
        cid = self._cid(color)
        x, y, w, h = int(x), int(y), int(w), int(h)
        if w <= 0 or h <= 0:
            raise CanvasError(f"rect needs positive w/h, got {w}x{h}")
        if fill:
            for py in range(y, y + h):
                for px in range(x, x + w):
                    self._put(px, py, cid)
        else:
            for px in range(x, x + w):
                self._put(px, y, cid)
                self._put(px, y + h - 1, cid)
            for py in range(y, y + h):
                self._put(x, py, cid)
                self._put(x + w - 1, py, cid)
        return self

    def line(self, x0: int, y0: int, x1: int, y1: int, color) -> "Canvas":
        """Bresenham line, endpoints included."""
        cid = self._cid(color)
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self._put(x0, y0, cid)
            if x0 == x1 and y0 == y1:
                return self
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def _ellipse_rows(self, cx: float, cy: float, rx: float, ry: float) -> Dict[int, Tuple[int, int]]:
        """Map each covered row to its (x_start, x_end) horizontal span."""
        rows: Dict[int, Tuple[int, int]] = {}
        if rx <= 0 or ry <= 0:
            return {int(round(cy)): (int(round(cx)), int(round(cx)))}
        y0, y1 = int(cy - ry), int(cy + ry)
        for py in range(y0, y1 + 1):
            dy = (py - cy) / ry
            if abs(dy) > 1:
                continue
            half = rx * (1 - dy * dy) ** 0.5
            rows[py] = (int(round(cx - half)), int(round(cx + half)))
        return rows

    def circle(self, cx: int, cy: int, r: int, color, fill: bool = True) -> "Canvas":
        return self.ellipse(cx, cy, r, r, color, fill=fill)

    def ellipse(self, cx: int, cy: int, rx: int, ry: int, color, fill: bool = True) -> "Canvas":
        cid = self._cid(color)
        cx, cy, rx, ry = float(cx), float(cy), float(rx), float(ry)
        if rx < 0 or ry < 0:
            raise CanvasError(f"ellipse radii must be >= 0, got rx={rx}, ry={ry}")
        rows = self._ellipse_rows(cx, cy, rx, ry)
        if fill:
            for py, (xa, xb) in rows.items():
                for px in range(xa, xb + 1):
                    self._put(px, py, cid)
            return self
        # stroke = the boundary of the filled shape: a pixel of the shape
        # whose 4-neighbourhood is not fully inside it
        inside = set()
        for py, (xa, xb) in rows.items():
            for px in range(xa, xb + 1):
                inside.add((px, py))
        for px, py in inside:
            if any((px + dx, py + dy) not in inside for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                self._put(px, py, cid)
        return self

    def poly(self, points: Sequence[Sequence[int]], color, fill: bool = True) -> "Canvas":
        pts = [(int(x), int(y)) for x, y in points]
        if len(pts) < 2:
            raise CanvasError("poly needs at least 2 points")
        if not fill:
            for i in range(len(pts)):
                x0, y0 = pts[i]
                x1, y1 = pts[(i + 1) % len(pts)]
                self.line(x0, y0, x1, y1, color)
            return self
        cid = self._cid(color)
        ys = [p[1] for p in pts]
        for py in range(min(ys), max(ys) + 1):
            crossings = []
            for i in range(len(pts)):
                x0, y0 = pts[i]
                x1, y1 = pts[(i + 1) % len(pts)]
                if y0 == y1:
                    continue
                if min(y0, y1) <= py < max(y0, y1):
                    t = (py - y0) / (y1 - y0)
                    crossings.append(x0 + t * (x1 - x0))
            crossings.sort()
            for i in range(0, len(crossings) - 1, 2):
                for px in range(int(round(crossings[i])), int(round(crossings[i + 1])) + 1):
                    self._put(px, py, cid)
        return self

    def _fill_contours(self, contours: Sequence[Sequence[Tuple[float, float]]], cid: int) -> None:
        """Even-odd scanline fill of one or more contours.

        The crossing rule is :meth:`poly`'s, so a single contour fills exactly
        the way ``poly`` fills it. Several contours fill as *one* region, which
        is what makes a second contour cut a hole instead of painting over the
        first. Unlike ``poly`` the points stay floats until a pixel is written:
        a flattened curve is not on the integer grid, and rounding it first
        would make the fill wobble row to row.
        """
        edges: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        for pts in contours:
            if len(pts) < 3:
                continue  # nothing to fill: fill treats every subpath as closed
            ring = list(pts) + [pts[0]]
            edges.extend(zip(ring, ring[1:]))
        if not edges:
            return
        rows = [p[1] for edge in edges for p in edge]
        for py in range(math.floor(min(rows)), math.ceil(max(rows)) + 1):
            crossings = []
            for (x0, y0), (x1, y1) in edges:
                if y0 == y1:
                    continue
                if min(y0, y1) <= py < max(y0, y1):
                    t = (py - y0) / (y1 - y0)
                    crossings.append(x0 + t * (x1 - x0))
            if len(crossings) < 2:
                continue
            crossings.sort()
            for i in range(0, len(crossings) - 1, 2):
                for px in range(int(round(crossings[i])), int(round(crossings[i + 1])) + 1):
                    self._put(px, py, cid)

    def path(self, d: str, color, fill: bool = True) -> "Canvas":
        """Draw an SVG path data string: curves, arcs and multi-part outlines.

        ``M L H V C S Q T A Z``, absolute in upper case and relative in lower
        case, with SVG's implicit repetition and its ``S``/``T`` shorthands,
        so one call covers a whole outline.  Coordinates are canvas pixels and
        are used as written: nothing is rescaled, flipped or fitted.  Only
        path *data* is accepted: there is no ``transform``, ``viewBox``,
        ``stroke-width`` or style, and anything else is an error naming the
        character position.

        With ``fill=True`` every subpath is closed implicitly and they are
        filled together, even-odd, so an inner subpath cuts a hole. With
        ``fill=False`` each subpath is stroked as an open line, and only ``Z``
        closes it — which is why a curve does not come back on itself unless
        you ask for it.
        """
        try:
            contours = pathdata.flatten(d)
        except pathdata.PathDataError as exc:
            # the parser's messages already name the fault, the position and the
            # text, so they are passed through rather than wrapped in a prefix
            raise CanvasError(str(exc)) from None
        cid = self._cid(color)
        if fill:
            self._fill_contours([c.points for c in contours], cid)
            return self
        for contour in contours:
            pts = list(contour.points)
            if contour.closed and len(pts) > 1:
                pts.append(pts[0])
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                self.line(int(round(x0)), int(round(y0)),
                          int(round(x1)), int(round(y1)), cid)
        return self

    def text(self, x: int, y: int, s: str, color, spacing: Optional[int] = None,
             font: Optional[str] = None) -> "Canvas":
        """Draw ``s`` as one line of text, in one of several built-in fonts.

        ``"5x7"`` is the tiny one: 5x7 glyphs, A-Z, 0-9 and common
        punctuation, lowercase folded to uppercase, 1 pixel between
        characters.  ``"8px"``, ``"10px"`` and ``"12px"`` are three sizes of
        the same CJK-capable font, with square cells of that size and no
        spacing of their own.  They are not interchangeable — the smaller the
        cell, the fewer characters it covers and the sooner a complex glyph
        loses its strokes — so pick by what the line has to say and how much
        room the canvas has; the reference has the coverage of each size.

        ``font=None`` picks by content: a string of ASCII uses the 5x7 font,
        and a string with any character outside ASCII uses ``"12px"``, the
        size with the widest coverage.  ``spacing=None`` leaves each font its
        own default; an integer forces one instead.

        ``y`` is the top of the line: the 5x7 font occupies rows ``y`` to
        ``y+6``, an 8px line rows ``y+1`` to ``y+7``, and a 12px line's
        baseline is 10 pixels below ``y``.  A character no font has draws a
        filled cell, so the line keeps its length and the gap is visible.
        """
        cid = self._cid(color)
        put = lambda px, py: self._put(px, py, cid)  # noqa: E731
        if font is None:
            font = fontfusion.DEFAULT if any(ord(ch) > 0x7F for ch in s) else "5x7"
        if font == "5x7":
            font5x7.draw_text(put, int(x), int(y), s,
                              spacing=1 if spacing is None else spacing)
        elif font in fontfusion.METRICS:
            try:
                chosen = fontfusion.get(font)
                chosen.draw_text(put, int(x), int(y), s,
                                 spacing=0 if spacing is None else spacing)
            except fontfusion.FontDataError as exc:
                raise CanvasError(str(exc)) from None
        else:
            sizes = ", ".join(f"'{name}'" for name in fontfusion.METRICS)
            raise CanvasError(
                f"unknown font {font!r}; this draws '5x7' (ASCII, 5x7 pixels) "
                f"or a CJK-capable size: {sizes}"
            )
        return self

    def fill_at(self, x: int, y: int, color) -> "Canvas":
        """Four-connected flood fill from ``(x, y)``."""
        x, y = int(x), int(y)
        if not (0 <= x < self.w and 0 <= y < self.h):
            raise CanvasError(f"({x}, {y}) is outside {self.w}x{self.h}")
        cid = self._cid(color)
        target = self.px[y * self.w + x]
        if target == cid:
            return self
        stack = [(x, y)]
        while stack:
            px, py = stack.pop()
            if not (0 <= px < self.w and 0 <= py < self.h):
                continue
            i = py * self.w + px
            if self.px[i] != target:
                continue
            self.px[i] = cid
            stack.extend(((px + 1, py), (px - 1, py), (px, py + 1), (px, py - 1)))
        return self

    def clear(self, x: int = 0, y: int = 0, w: Optional[int] = None, h: Optional[int] = None) -> "Canvas":
        """Erase a region to transparent (default: the whole canvas)."""
        w = self.w if w is None else int(w)
        h = self.h if h is None else int(h)
        for py in range(int(y), int(y) + h):
            for px in range(int(x), int(x) + w):
                self._put(px, py, 0)
        return self

    def blit(self, src: "Canvas", dx: int, dy: int) -> "Canvas":
        """Composite ``src`` onto this canvas with its top-left at ``(dx, dy)``.

        Fully transparent pixels leave the target alone; fully opaque ones
        replace it; part-transparent ones blend over it.
        """
        dx, dy = int(dx), int(dy)
        alphas = [rgba[3] for rgba in src.pal.colors]
        for sy in range(src.h):
            ty = dy + sy
            if not 0 <= ty < self.h:
                continue
            row = sy * src.w
            for sx in range(src.w):
                tx = dx + sx
                if not 0 <= tx < self.w:
                    continue
                cid = src.px[row + sx]
                a = alphas[cid]
                if a == 0:
                    continue
                if a == 255:
                    self._put(tx, ty, self.pal.index_of(src.pal.rgba(cid)))
                    continue
                merged = over(src.pal.rgba(cid), self.pal.rgba(self.px[ty * self.w + tx]))
                self.px[ty * self.w + tx] = self.pal.register(merged)
        return self

    def mirror(self, axis: str = "h") -> "Canvas":
        if axis not in ("h", "v"):
            raise CanvasError(f"axis must be 'h' or 'v', got {axis!r}")
        out = [0] * (self.w * self.h)
        for y in range(self.h):
            for x in range(self.w):
                sx, sy = (self.w - 1 - x, y) if axis == "h" else (x, self.h - 1 - y)
                out[y * self.w + x] = self.px[sy * self.w + sx]
        self.px = out
        return self

    def shift(self, dx: int, dy: int, fill=None) -> "Canvas":
        """Move the whole canvas by ``(dx, dy)``; vacated pixels take ``fill``."""
        cid = 0 if fill is None else self._cid(fill)
        out = [cid] * (self.w * self.h)
        for y in range(self.h):
            ty = y + int(dy)
            if not 0 <= ty < self.h:
                continue
            for x in range(self.w):
                tx = x + int(dx)
                if 0 <= tx < self.w:
                    out[ty * self.w + tx] = self.px[y * self.w + x]
        self.px = out
        return self

    def outline(self, color) -> "Canvas":
        """Repaint every visible pixel that touches a transparent one."""
        cid = self._cid(color)
        alphas = [rgba[3] for rgba in self.pal.colors]
        edges = []
        for y in range(self.h):
            for x in range(self.w):
                if alphas[self.px[y * self.w + x]] == 0:
                    continue
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if (not (0 <= nx < self.w and 0 <= ny < self.h)
                            or alphas[self.px[ny * self.w + nx]] == 0):
                        edges.append((x, y))
                        break
        for x, y in edges:
            self.px[y * self.w + x] = cid
        return self

    def dither(self, x: int, y: int, w: int, h: int, colors: Sequence, pattern: str = "checker") -> "Canvas":
        """Fill a region with a two-colour dither (``checker`` or ``bayer4``)."""
        if len(colors) != 2:
            raise CanvasError("dither needs exactly 2 colours")
        a, b = self._cid(colors[0]), self._cid(colors[1])
        bayer = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
        for j in range(int(h)):
            for i in range(int(w)):
                if pattern == "checker":
                    cid = a if (i + j) % 2 == 0 else b
                elif pattern == "bayer4":
                    cid = a if bayer[j % 4][i % 4] < 8 else b
                else:
                    raise CanvasError(f"pattern must be 'checker' or 'bayer4', got {pattern!r}")
                self._put(int(x) + i, int(y) + j, cid)
        return self

    # ------------------------------------------------------------------
    # snapshots
    # ------------------------------------------------------------------
    def snapshot(self, name: str = "default") -> "Canvas":
        """Keep the current pixels in memory under ``name`` (per canvas)."""
        self._snapshots[name] = list(self.px)
        return self

    def restore(self, name: str = "default") -> "Canvas":
        if name not in self._snapshots:
            known = ", ".join(sorted(self._snapshots)) or "none"
            raise CanvasError(f"no snapshot named {name!r} on {self.name or 'canvas'}; known: {known}")
        self.px = list(self._snapshots[name])
        return self

    def copy(self, name: Optional[str] = None) -> "Canvas":
        """A new canvas with the same pixels, sharing the drawing's palette.

        Sharing the palette object is the point: every canvas in a drawing
        indexes into one palette, so a clone carrying its own would make the
        same index mean different colours in the two canvases.
        """
        c = Canvas(self.w, self.h, palette=self.pal, name=name)
        c.px = list(self.px)
        return c

    # ------------------------------------------------------------------
    # observation / serialisation
    # ------------------------------------------------------------------
    def stats(self) -> dict:
        alphas = [rgba[3] for rgba in self.pal.colors]
        counts: Dict[int, int] = {}
        for cid in self.px:
            counts[cid] = counts.get(cid, 0) + 1
        shown = [i for i, cid in enumerate(self.px) if alphas[cid] > 0]
        visible = len(shown)
        xs = [i % self.w for i in shown]
        ys = [i // self.w for i in shown]
        return {
            "name": self.name,
            "size": [self.w, self.h],
            "colors_used": len([c for c in counts if alphas[c] > 0]),
            "visible_pixels": visible,
            "coverage": round(visible / (self.w * self.h), 4),
            "bbox": [min(xs), min(ys), max(xs), max(ys)] if xs else None,
            "top_colors": [
                {"name": self.color_name(cid), "pixels": n}
                for cid, n in sorted(counts.items(), key=lambda kv: -kv[1])[:8]
                if alphas[cid] > 0
            ],
        }

    def stats_text(self) -> str:
        s = self.stats()
        bbox = "none" if s["bbox"] is None else "(%d,%d)-(%d,%d)" % tuple(s["bbox"])
        top = ", ".join(f"{c['name']}={c['pixels']}" for c in s["top_colors"]) or "(empty)"
        return (
            f"{s['name'] or 'canvas'} {s['size'][0]}x{s['size'][1]} | "
            f"colors={s['colors_used']} visible={s['visible_pixels']} "
            f"coverage={s['coverage']:.1%} bbox={bbox}\n  top: {top}"
        )

    def matrix_text(self, region=None, indices: bool = False) -> str:
        """Text view of the pixels, with a legend, for agents that cannot see images.

        This is the observation channel when no image reading is available,
        so it aims to be *readable*: each colour gets one character and a
        legend explains what each character is, including its hex value and
        its palette index.  ``indices=True`` prints those indices in the grid
        itself, when exact numbers matter.
        """
        x0, y0, x1, y1 = region if region else (0, 0, self.w - 1, self.h - 1)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(self.w - 1, x1), min(self.h - 1, y1)
        if x0 > x1 or y0 > y1:
            return "(empty region)"
        used: List[int] = []
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                cid = self.px[y * self.w + x]
                if cid != 0 and cid not in used:
                    used.append(cid)
        if indices:
            labels = {0: "."}
            labels.update({cid: str(cid) for cid in used})
            width = max(len(v) for v in labels.values()) + 1
            body = [
                "".join(labels[self.px[y * self.w + x]].rjust(width) for x in range(x0, x1 + 1)).rstrip()
                for y in range(y0, y1 + 1)
            ]
            return f"region ({x0},{y0})-({x1},{y1}) as palette indices:\n" + "\n".join("  " + r for r in body)
        charset = string.ascii_lowercase + string.ascii_uppercase + string.digits
        # The letter is fixed by the *palette index*, not by the order the
        # colours happen to appear in this region: `a` has to mean the same
        # colour in a region dump as in a full-canvas dump, or two views of
        # one drawing cannot be read together.
        labels = {0: "."}
        for cid in used:
            labels[cid] = charset[cid - 1] if 1 <= cid <= len(charset) else "?"
        body = ["".join(labels[self.px[y * self.w + x]] for x in range(x0, x1 + 1)) for y in range(y0, y1 + 1)]
        legend = []
        for cid in used:
            label = next((n for n, i in self.pal.names.items() if i == cid), None)
            rgba = self.pal.colors[cid]
            parts = [hex_of(rgba) if label else hex_of(rgba), f"id {cid}"]
            if rgba[3] != 255:
                parts.insert(1, f"{round(rgba[3] / 255 * 100)}% alpha")
            shown = f"{label} " if label else ""
            legend.append(f"  {labels[cid]} = {shown}({', '.join(parts)})")
        head = f"region ({x0},{y0})-({x1},{y1}), {self.w}x{self.h} canvas, '.' = transparent:"
        return "\n".join([head] + ["  " + r for r in body] + (["legend:"] + legend if legend else []))

    def to_rgba(self, scale: int = 1) -> Tuple[int, int, bytearray]:
        """Render to RGBA bytes (nearest-neighbour ``scale``)."""
        if scale < 1:
            raise CanvasError(f"scale must be 1 or more, got {scale}")
        buf = bytearray(self.w * self.h * 4)
        for i, cid in enumerate(self.px):
            buf[i * 4:i * 4 + 4] = bytes(self.pal.colors[cid])
        if scale == 1:
            return self.w, self.h, buf
        return self.w * scale, self.h * scale, png.resize_nearest(
            self.w, self.h, buf, self.w * scale, self.h * scale)

    def to_indexed(self, scale: int = 1) -> Tuple[int, int, bytearray, List[RGBA]]:
        """Pixels as one byte per pixel plus the palette, for an indexed PNG.

        The palette is compacted to the colours actually used, in first-seen
        order, and the pixels are remapped onto it.  Indexed PNGs carry the
        palette in the file (PLTE/tRNS), so a pixel-art tool reading the
        result sees the real colours rather than a flattened RGBA copy — and
        for anything bigger than a small sprite they are smaller on disk.
        Raises when the drawing uses more than 256 colours.
        """
        if scale < 1:
            raise CanvasError(f"scale must be 1 or more, got {scale}")
        used: List[int] = []
        remap: Dict[int, int] = {}
        for cid in self.px:
            if cid not in remap:
                if len(used) == 256:
                    raise CanvasError(
                        "an indexed PNG holds at most 256 colours and this drawing has more; "
                        "save without indexed=True"
                    )
                remap[cid] = len(used)
                used.append(cid)
        indices = bytearray(remap[cid] for cid in self.px)
        w, h = self.w, self.h
        if scale > 1:
            w, h = w * scale, h * scale
            indices = png.resize_nearest(self.w, self.h, indices, w, h, bpp=1)
        return w, h, indices, [self.pal.rgba(cid) for cid in used]

    def to_dict(self) -> dict:
        """Pixels only; the palette is stored once per drawing by the session."""
        return {"name": self.name, "width": self.w, "height": self.h, "pixels": self.px}

    @classmethod
    def from_dict(cls, d: dict, palette: Optional[Palette] = None) -> "Canvas":
        """Rebuild a canvas from :meth:`to_dict` output.

        ``palette`` is the palette the stored indices refer to — the session
        keeps one per drawing and stores it beside the canvases, so the pixel
        data alone does not say what its numbers mean. Without one the
        indices are rejected rather than silently reinterpreted.
        """
        c = cls(int(d["width"]), int(d["height"]), palette=palette, name=d.get("name"))
        pixels = [int(v) for v in d["pixels"]]
        if len(pixels) != c.w * c.h:
            raise CanvasError(f"pixel count {len(pixels)} != {c.w}x{c.h}")
        if any(not 0 <= v < len(c.pal) for v in pixels):
            raise CanvasError(
                "stored pixels reference palette entries that do not exist; "
                "the canvas data and the palette it was saved with do not match"
            )
        c.px = pixels
        return c

    @classmethod
    def from_image(cls, path, palette: Optional[Palette] = None, name: Optional[str] = None) -> "Canvas":
        """Load a PNG file as a canvas (transparent pixels become index 0)."""
        w, h, rgba = png.read_rgba(path)
        c = cls(w, h, palette=palette, name=name)
        index: Dict[RGBA, int] = {(0, 0, 0, 0): 0}
        for i in range(w * h):
            px = tuple(rgba[i * 4:i * 4 + 4])
            if px not in index:
                index[px] = c.color(px)
            c.px[i] = index[px]
        return c

    def __repr__(self) -> str:  # noqa: D105
        return f"Canvas({self.name or 'canvas'} {self.w}x{self.h}, {len(self.pal)} colors)"


def flatten(layers: Iterable[Canvas], name: Optional[str] = None, background=None) -> Canvas:
    """Composite layers bottom-to-top into a new canvas.

    Each layer is composited over the result so far (source-over).  A fully
    opaque pixel hides what is beneath it and a fully transparent one is
    ignored, so the old "topmost opaque pixel wins" behaviour is exactly the
    common case; only genuinely part-transparent pixels blend.  A blended
    colour is registered in the palette like any other, so the result never
    carries colours outside it.

    Layers normally share one palette, in which case this is a straight
    copy of indices.  A layer carrying a different palette (an imported
    image, say) has its colours translated into the target palette first,
    so the result always has a single coherent palette.
    """
    items = [c for c in layers]
    if not items:
        raise CanvasError("flatten needs at least one canvas")
    w, h = items[0].w, items[0].h
    for c in items:
        if (c.w, c.h) != (w, h):
            raise CanvasError(
                f"all layers must be the same size; {c.name or 'canvas'} is {c.w}x{c.h}, expected {w}x{h}"
            )
    pal = items[0].pal
    out = Canvas(w, h, palette=pal, background=background, name=name)
    for c in items:
        if c.pal is pal:
            mapping = None
            alphas = [rgba[3] for rgba in pal.colors]
        else:
            alphas = [rgba[3] for rgba in c.pal.colors]
            mapping = [pal.index_of(c.pal.rgba(i)) for i in range(len(c.pal))]
        for i, cid in enumerate(c.px):
            a = alphas[cid]
            if a == 0:
                continue
            if a == 255:
                out.px[i] = cid if mapping is None else mapping[cid]
                continue
            src = c.pal.rgba(cid) if mapping is None else pal.rgba(mapping[cid])
            out.px[i] = pal.register(over(src, pal.rgba(out.px[i])))
    return out
