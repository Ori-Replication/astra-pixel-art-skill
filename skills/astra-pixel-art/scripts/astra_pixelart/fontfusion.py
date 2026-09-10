"""Fusion Pixel Font, as bitmap tables the engine can read.

The engine has no font rasteriser and never will — it is standard-library
only — so this font does not travel as a font file.  It travels as flat
tables of bitmaps built offline from the upstream BDF releases by
``tools/build_fusion_font.py``, and this module reads them:

    fontfusion8.bin   8x8 cells    27,977 glyphs    363,701 bytes
    fontfusion10.bin  10x10 cells  24,832 glyphs    446,976 bytes
    fontfusion12.bin  12x12 cells  36,533 glyphs    840,259 bytes

Records are sorted by codepoint, so a lookup is a binary search and a glyph
is a slice; there is no header, no index and no compression, and nothing is
parsed at import time.  A table is read once, on the first character actually
drawn from it, so a drawing that never uses a size never pays for it.

    <I  codepoint
    <B  advance in pixels (the cell width for a full-width glyph, half of it
       for a half-width one)
    Ns  the glyph as a cell_w x cell_h bitmap, 1 bit per pixel, MSB first,
        row major, where N is ceil(cell_w * cell_h / 8)

The three sizes are the upstream ones and they are not interchangeable: the
smaller the cell, the fewer characters it covers and the more complex glyphs
lose their strokes.  What each table holds is a property of the upstream
release rather than of this reader, so the numbers live in
``references/api.md``.

Upstream: Fusion Pixel Font, monospaced, Simplified Chinese, release
2026.09.01 — https://github.com/TakWolf/fusion-pixel-font — OFL-1.1,
Copyright (c) 2022, TakWolf.  The licence travels with the data; see
THIRD_PARTY_NOTICES.md next to it, and the OFL text beside this file.

Data file sha256:
    8px  baee97f539931fbcf63406fc699df41a269e6d09dc5690a42cd8e4e025529509
    10px 896a520d9e51a6a1bcd9ed91f39abd82702290ebd543b323e3e886ee84776368
    12px ec642dd3699bc67558a27212f3a94ef4fc43bb90f00167e2e3637a7c878b1227
"""
from __future__ import annotations

import struct
import unicodedata
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

#: What each size declares, as (cell width, cell height, ascent).  Checked
#: against the data file's own length rather than trusted from its name.
METRICS = {
    "8px": (8, 8, 7),
    "10px": (10, 10, 9),
    "12px": (12, 12, 10),
}

#: What a line with characters outside ASCII gets when no font is asked for:
#: the size with the widest coverage, not the smallest.
DEFAULT = "12px"


class FontDataError(ValueError):
    """A bitmap table is missing or does not look like one."""


class Font:
    """One size of the font: a lazy, read-only view of its bitmap table."""

    __slots__ = ("name", "cell_w", "cell_h", "ascent", "_path", "_blob", "_count", "_stride")

    def __init__(self, name: str):
        if name not in METRICS:
            raise FontDataError(f"unknown font size {name!r}; there is {', '.join(METRICS)}")
        self.name = name
        self.cell_w, self.cell_h, self.ascent = METRICS[name]
        self._path = Path(__file__).with_name(f"fontfusion{name.rstrip('px')}.bin")
        self._blob: Optional[bytes] = None
        self._count: Optional[int] = None
        self._stride = 4 + 1 + (self.cell_w * self.cell_h + 7) // 8

    # -- the table ------------------------------------------------------
    @property
    def payload(self) -> int:
        """Bytes of bitmap per glyph."""
        return (self.cell_w * self.cell_h + 7) // 8

    def _load(self) -> bytes:
        if self._blob is None:
            try:
                blob = self._path.read_bytes()
            except OSError as exc:
                raise FontDataError(
                    f"the {self.name} font data is missing: {self._path} ({exc.strerror}); "
                    "reinstall the skill or rebuild it with tools/build_fusion_font.py"
                ) from None
            if not blob or len(blob) % self._stride:
                raise FontDataError(
                    f"{self._path} is {len(blob)} bytes, not a multiple of {self._stride}; "
                    "rebuild it with tools/build_fusion_font.py"
                )
            self._blob, self._count = blob, len(blob) // self._stride
        return self._blob

    def count(self) -> int:
        """How many glyphs the table holds."""
        self._load()
        return self._count or 0

    def loaded(self) -> bool:
        """Whether the table has been read yet — for tests, not for callers."""
        return self._blob is not None

    def _index(self, cp: int) -> Optional[int]:
        """The record holding ``cp``, or None.  Binary search over the table."""
        blob = self._load()
        low, high = 0, (self._count or 0) - 1
        while low <= high:
            mid = (low + high) // 2
            found = struct.unpack_from("<I", blob, mid * self._stride)[0]
            if found == cp:
                return mid
            if found < cp:
                low = mid + 1
            else:
                high = mid - 1
        return None

    # -- glyphs ---------------------------------------------------------
    def has(self, cp: int) -> bool:
        """Whether the table has a glyph for this codepoint."""
        return self._index(cp) is not None

    def advance(self, cp: int) -> int:
        """How far the pen moves after this character, in pixels.

        A missing glyph still takes room: the full cell width for anything
        Unicode calls East Asian wide, half of it otherwise, so a fallback
        does not wreck the line.
        """
        index = self._index(cp)
        if index is None:
            wide = unicodedata.east_asian_width(chr(cp)) in ("W", "F") if cp < 0x110000 else True
            return self.cell_w if wide else self.cell_w // 2
        return self._load()[index * self._stride + 4]

    def rows(self, cp: int) -> Optional[Tuple[int, ...]]:
        """The glyph as one integer per row, row 0 at the top, leftmost pixel
        in bit ``cell_w - 1``.

        None when the character has no glyph, so the caller decides what a
        missing character looks like.
        """
        index = self._index(cp)
        if index is None:
            return None
        start = index * self._stride + 5
        bits = int.from_bytes(self._load()[start:start + self.payload], "big")
        return tuple((bits >> (self.cell_w * (self.cell_h - 1 - row))) & ((1 << self.cell_w) - 1)
                     for row in range(self.cell_h))

    def iter_codepoints(self) -> Iterator[int]:
        """Every codepoint in the table, in order — for tests and tooling."""
        blob = self._load()
        for i in range(self._count or 0):
            yield struct.unpack_from("<I", blob, i * self._stride)[0]

    # -- drawing --------------------------------------------------------
    def draw_text(self, put, x: int, y: int, text: str, spacing: int = 0) -> int:
        """Draw ``text`` at integer offset ``(x, y)``.

        ``y`` is the top of the cell, so the baseline sits at ``y + ascent``.
        ``put(px, py)`` is called for every lit pixel, exactly as the 5x7 font
        does; a character this table does not have draws a filled cell, so the
        line keeps its length and the gap is visible.
        """
        n = 0
        cx = x
        for ch in text:
            cp = ord(ch)
            glyph = self.rows(cp)
            if glyph is None:
                for row in range(1, 1 + self.ascent):
                    for col in range(self.cell_w):
                        put(cx + col, y + row)
                        n += 1
            else:
                for row, bits in enumerate(glyph):
                    if not bits:
                        continue
                    for col in range(self.cell_w):
                        if bits & (1 << (self.cell_w - 1 - col)):
                            put(cx + col, y + row)
                            n += 1
            cx += self.advance(cp) + spacing
        return n


_loaded: Dict[str, Font] = {}


def get(size: str = DEFAULT) -> Font:
    """The reader for one size: ``"8px"``, ``"10px"`` or ``"12px"``."""
    font = _loaded.get(size)
    if font is None:
        font = _loaded[size] = Font(size)
    return font


def lines(*sizes: str) -> Dict[str, int]:
    """Glyph counts for the given sizes — a convenience for tooling."""
    return {size: get(size).count() for size in (sizes or tuple(METRICS))}
