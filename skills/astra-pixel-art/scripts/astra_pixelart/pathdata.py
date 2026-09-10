"""SVG path data, parsed and flattened into polylines.

Only the *path data* grammar is accepted — ``M L H V C S Q T A Z``, upper
case for absolute and lower case for relative, with the SVG rules for implicit
repetition and for the smooth shorthands ``S`` and ``T``.  A whole ``<svg>``
document is neither accepted nor wanted: no ``transform``, no ``viewBox``, no
``stroke-width``, no styles, no gradients.  The one thing worth borrowing from
SVG is how it *spells* a curve, because every model has read a million of
these strings::

    M32 14 C32 6 20 6 20 16 C20 26 32 32 32 42 C32 32 44 26 44 16 C44 6 32 6 32 14 Z

Coordinates are canvas pixels — origin top-left, y growing down — so a path
can be written or pasted without a mental transform.

Nothing here draws: :func:`flatten` turns a string into contours of float
points, and the canvas rasterises them with the primitives it already has.
Curves are subdivided until every control point sits within ``TOLERANCE`` of
its chord, which makes the result depend on the curve rather than on the
canvas size — a 16-point sprite and a 512-point one get the same shape.
"""
from __future__ import annotations

import math
from typing import List, NamedTuple, Optional, Tuple

Point = Tuple[float, float]

#: How far a control point may sit off its chord before a curve is split.
#: A quarter pixel is below what a hard-edged pixel grid can show, so this is
#: "smooth enough", and it is a constant rather than a parameter because a
#: knob nobody has measured is a knob nobody sets correctly.
TOLERANCE = 0.25

#: Recursion cap per curve segment: 2**6 = at most 64 line segments.
MAX_DEPTH = 6

#: Degenerate-arc guard, in pixels.
EPSILON = 1e-12

_COMMANDS = set("MmLlHhVvCcSsQqTtAaZz")


class PathDataError(ValueError):
    """Bad path data.  The message names the position and the offending text."""


class Contour(NamedTuple):
    """One subpath: its points, and whether ``Z`` closed it."""

    points: List[Point]
    closed: bool


class _Scanner:
    """Character-level reader for the path grammar.

    Character-level, not a regex, because of two corners of the grammar that a
    token list gets wrong: ``1-2`` is two numbers and ``.5.5`` is two numbers,
    and the arc flags are single characters, so ``0116 16`` is flag 0, flag 1,
    then the number 116.
    """

    def __init__(self, text: str):
        self.text = text
        self.i = 0

    def fail(self, message: str):
        raise PathDataError(f"{message} at character {self.i} of {self.text!r}")

    def skip_separators(self) -> None:
        while self.i < len(self.text) and self.text[self.i] in " \t\r\n,":
            self.i += 1

    def peek(self) -> Optional[str]:
        return self.text[self.i] if self.i < len(self.text) else None

    def take_command(self) -> Optional[str]:
        ch = self.peek()
        if ch is not None and ch in _COMMANDS:
            self.i += 1
            return ch
        return None

    def number(self) -> float:
        self.skip_separators()
        start = self.i
        text = self.text
        if self.i < len(text) and text[self.i] in "+-":
            self.i += 1
        digits = False
        while self.i < len(text) and text[self.i].isdigit():
            self.i += 1
            digits = True
        if self.i < len(text) and text[self.i] == ".":
            self.i += 1
            while self.i < len(text) and text[self.i].isdigit():
                self.i += 1
                digits = True
        if not digits:
            self.fail("expected a number")
        if self.i < len(text) and text[self.i] in "eE":
            mark = self.i
            self.i += 1
            if self.i < len(text) and text[self.i] in "+-":
                self.i += 1
            if self.i < len(text) and text[self.i].isdigit():
                while self.i < len(text) and text[self.i].isdigit():
                    self.i += 1
            else:
                self.i = mark  # "1e" is the number 1, then junk
        return float(text[start:self.i])

    def flag(self) -> int:
        self.skip_separators()
        ch = self.peek()
        if ch not in ("0", "1"):
            self.fail("expected an arc flag of 0 or 1")
        self.i += 1
        return int(ch)


def _mid(a: Point, b: Point) -> Point:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def _flat_enough(p0: Point, c1: Point, c2: Point, p3: Point, tolerance: float) -> bool:
    """Whether both control points sit within ``tolerance`` of the chord."""
    dx, dy = p3[0] - p0[0], p3[1] - p0[1]
    length = math.hypot(dx, dy)
    if length < EPSILON:
        # start and end coincide: the "chord" is a point, so measure from it
        return max(math.hypot(c1[0] - p0[0], c1[1] - p0[1]),
                   math.hypot(c2[0] - p0[0], c2[1] - p0[1])) <= tolerance

    def distance(p: Point) -> float:
        return abs((p[0] - p0[0]) * dy - (p[1] - p0[1]) * dx) / length

    return max(distance(c1), distance(c2)) <= tolerance


def _cubic(out: List[Point], p0: Point, c1: Point, c2: Point, p3: Point,
           tolerance: float, depth: int) -> None:
    """Append ``p3`` (or the subdivision of it) — ``p0`` is already there."""
    if depth <= 0 or _flat_enough(p0, c1, c2, p3, tolerance):
        out.append(p3)
        return
    p01, p12, p23 = _mid(p0, c1), _mid(c1, c2), _mid(c2, p3)
    p012, p123 = _mid(p01, p12), _mid(p12, p23)
    split = _mid(p012, p123)
    _cubic(out, p0, p01, p012, split, tolerance, depth - 1)
    _cubic(out, split, p123, p23, p3, tolerance, depth - 1)


def _elevate(p: Point, c: Point) -> Point:
    """The cubic control point equivalent to a quadratic one at ``c``."""
    return (p[0] + 2.0 / 3.0 * (c[0] - p[0]), p[1] + 2.0 / 3.0 * (c[1] - p[1]))


def _reflect(p: Point, ctrl: Optional[Point]) -> Point:
    """The smooth-shorthand control point: ``ctrl`` mirrored through ``p``."""
    if ctrl is None:
        return p
    return (2.0 * p[0] - ctrl[0], 2.0 * p[1] - ctrl[1])


def _angle(ux: float, uy: float, vx: float, vy: float) -> float:
    dot = ux * vx + uy * vy
    norms = math.hypot(ux, uy) * math.hypot(vx, vy)
    if norms < EPSILON:
        return 0.0
    a = math.acos(max(-1.0, min(1.0, dot / norms)))
    return -a if ux * vy - uy * vx < 0 else a


def _arc(out: List[Point], p0: Point, rx: float, ry: float, rotation: float,
         large: int, sweep: int, p1: Point, tolerance: float) -> None:
    """Append the elliptical arc from ``p0`` to ``p1`` (spec appendix F.6.5)."""
    rx, ry = abs(rx), abs(ry)
    if rx < EPSILON or ry < EPSILON or (abs(p0[0] - p1[0]) < EPSILON and abs(p0[1] - p1[1]) < EPSILON):
        out.append(p1)  # an arc with no radius, or no travel, is a straight line
        return

    phi = math.radians(rotation)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    dx2, dy2 = (p0[0] - p1[0]) / 2.0, (p0[1] - p1[1]) / 2.0
    x1 = cos_phi * dx2 + sin_phi * dy2
    y1 = -sin_phi * dx2 + cos_phi * dy2

    scale = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if scale > 1.0:
        scale = math.sqrt(scale)
        rx *= scale
        ry *= scale

    numerator = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    denominator = rx * rx * y1 * y1 + ry * ry * x1 * x1
    factor = math.sqrt(max(numerator, 0.0) / denominator)
    if large == sweep:
        factor = -factor
    cxp, cyp = factor * rx * y1 / ry, -factor * ry * x1 / rx
    cx = cos_phi * cxp - sin_phi * cyp + (p0[0] + p1[0]) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (p0[1] + p1[1]) / 2.0

    theta = _angle(1.0, 0.0, (x1 - cxp) / rx, (y1 - cyp) / ry)
    delta = _angle((x1 - cxp) / rx, (y1 - cyp) / ry,
                   (-x1 - cxp) / rx, (-y1 - cyp) / ry)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    if sweep and delta < 0:
        delta += 2 * math.pi

    radius = max(rx, ry)
    if tolerance >= radius:
        step = abs(delta)
    else:
        step = 2 * math.acos(max(-1.0, min(1.0, 1.0 - tolerance / radius)))
    count = max(2, int(math.ceil(abs(delta) / max(step, 1e-6))))
    count = min(count, 1024)

    for k in range(1, count + 1):
        th = theta + delta * k / count
        ex, ey = rx * math.cos(th), ry * math.sin(th)
        out.append((cos_phi * ex - sin_phi * ey + cx,
                    sin_phi * ex + cos_phi * ey + cy))
    out[-1] = p1  # land exactly on the endpoint: no hairline gap to stroke over


class _Parser:
    """The path grammar, as a state machine over :class:`_Scanner`."""

    def __init__(self, text: str, tolerance: float, max_depth: int):
        self.sc = _Scanner(text)
        self.tolerance = tolerance
        self.max_depth = max_depth
        self.contours: List[Contour] = []
        self.points: List[Point] = []
        self.closed = False
        self.cur: Point = (0.0, 0.0)
        self.start: Point = (0.0, 0.0)
        self.prev = ""
        self.cubic_ctrl: Optional[Point] = None
        self.quad_ctrl: Optional[Point] = None

    # -- contour bookkeeping -------------------------------------------
    def flush(self) -> None:
        if self.points:
            self.contours.append(Contour(self.points, self.closed))
        self.points = []
        self.closed = False

    def active(self) -> List[Point]:
        """The contour being drawn; after ``Z`` it restarts at the subpath start."""
        if not self.points:
            self.points = [self.cur]
        return self.points

    def add(self, p: Point) -> None:
        self.active().append(p)

    def begin(self, p: Point) -> None:
        """A moveto: close off whatever was open and begin a new subpath."""
        self.flush()
        self.points = [p]

    # -- reads ----------------------------------------------------------
    def point(self, relative: bool) -> Point:
        x, y = self.sc.number(), self.sc.number()
        if relative:
            return (self.cur[0] + x, self.cur[1] + y)
        return (x, y)

    # -- commands -------------------------------------------------------
    def run(self) -> List[Contour]:
        sc = self.sc
        sc.skip_separators()
        if sc.peek() is None:
            raise PathDataError("path data is empty; start with 'M', e.g. \"M0 0 L10 10\"")
        if sc.peek() not in "Mm":
            sc.fail("path data must start with 'M' or 'm'")

        while True:
            sc.skip_separators()
            if sc.peek() is None:
                break
            cmd = sc.take_command()
            if cmd is None:
                # not a command letter: either an implicit repetition of the
                # previous command, or a letter that is not in the grammar
                ch = sc.peek()
                if ch is not None and ch.isalpha():
                    sc.fail(f"unsupported command {ch!r}; this understands M L H V C S Q T A Z")
                if self.prev in "Zz":
                    sc.fail("expected a command after 'Z'")
                # implicit repetition: extra coordinates repeat the command,
                # except after a moveto, where they mean lineto
                cmd = {"M": "L", "m": "l"}.get(self.prev, self.prev)
            if not cmd:
                sc.fail("expected a command")
            self.command(cmd)
            self.prev = cmd

        self.flush()
        return self.contours

    def command(self, cmd: str) -> None:
        upper = cmd.upper()
        relative = cmd.islower()

        if upper == "M":
            self.cur = self.point(relative)
            self.start = self.cur
            self.begin(self.cur)
            self.cubic_ctrl = self.quad_ctrl = None
            return

        if upper == "Z":
            if self.points:
                self.closed = True
            self.flush()
            self.cur = self.start
            self.cubic_ctrl = self.quad_ctrl = None
            return

        if upper in "LHV":
            if upper == "L":
                self.cur = self.point(relative)
            elif upper == "H":
                x = self.sc.number()
                self.cur = (self.cur[0] + x, self.cur[1]) if relative else (x, self.cur[1])
            else:
                y = self.sc.number()
                self.cur = (self.cur[0], self.cur[1] + y) if relative else (self.cur[0], y)
            self.add(self.cur)
            self.cubic_ctrl = self.quad_ctrl = None
            return

        if upper in "CS":
            out = self.active()
            if upper == "C":
                c1 = self.point(relative)
            else:
                # the smooth shorthand: mirror the last control point, but only
                # if the previous command was a cubic (spec 8.3.6)
                c1 = _reflect(self.cur, self.cubic_ctrl) if self.prev in "CcSs" else self.cur
            c2 = self.point(relative)
            end = self.point(relative)
            _cubic(out, self.cur, c1, c2, end, self.tolerance, self.max_depth)
            self.cubic_ctrl, self.quad_ctrl = c2, None
            self.cur = end
            return

        if upper in "QT":
            out = self.active()
            if upper == "Q":
                ctrl = self.point(relative)
            else:
                ctrl = _reflect(self.cur, self.quad_ctrl) if self.prev in "QqTt" else self.cur
            end = self.point(relative)
            _cubic(out, self.cur, _elevate(self.cur, ctrl), _elevate(end, ctrl), end,
                   self.tolerance, self.max_depth)
            self.quad_ctrl, self.cubic_ctrl = ctrl, None
            self.cur = end
            return

        if upper == "A":
            out = self.active()
            rx, ry = self.sc.number(), self.sc.number()
            rotation = self.sc.number()
            large, sweep = self.sc.flag(), self.sc.flag()
            end = self.point(relative)
            _arc(out, self.cur, rx, ry, rotation, large, sweep, end, self.tolerance)
            self.cubic_ctrl = self.quad_ctrl = None
            self.cur = end
            return

        self.sc.fail(f"unsupported command {cmd!r}")


def flatten(d, tolerance: float = TOLERANCE, max_depth: int = MAX_DEPTH) -> List[Contour]:
    """Parse SVG path data into contours of float points.

    Raises :class:`PathDataError` — with the character position — for anything
    that is not path data.
    """
    if not isinstance(d, str):
        raise PathDataError(f"path data must be a string, got {type(d).__name__}")
    return _Parser(d, tolerance, max_depth).run()
