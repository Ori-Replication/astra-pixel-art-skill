#!/usr/bin/env python3
"""Build the engine's bitmap font data from Fusion Pixel Font's BDF release.

The skill has to stay standard-library-only and has no font rasteriser, so
the CJK font ships as a flat bitmap table rather than as a font file.  This
is the offline half of that: it reads a BDF (BDF is *plain text* — one hex
codepoint and a few rows of hex bits per glyph, so this needs no rasteriser
either) and writes ``fontfusion12.bin`` beside the engine.

    python3 tools/build_fusion_font.py <path to .bdf> <8px|10px|12px> [output .bin]

Get the BDF from the upstream release, pinning the version:

    curl -LO https://github.com/TakWolf/fusion-pixel-font/releases/download/\
2026.09.01/fusion-pixel-font-12px-monospaced-bdf-v2026.09.01.zip
    unzip -j ...zip '*/fusion-pixel-12px-monospaced-zh_hans.bdf'

The output format is deliberately the simplest thing that can be read with
``int.from_bytes`` and bit tests: records sorted by codepoint, each

    <I  codepoint
    <B  advance in pixels (the cell width for a full-width glyph, half for a
       half-width one)
    Ns  the glyph as a cell_w x cell_h bitmap, 1 bit per pixel, MSB first,
        row major, where N is ceil(cell_w * cell_h / 8)

so 13 bytes per glyph at 8px, 18 at 10px and 23 at 12px, with no index, no
compression and no header.  The reader binary-searches the codepoint field
and slices the record.

Licence: Fusion Pixel Font is OFL-1.1 (Copyright (c) 2022, TakWolf).  The
licence text travels with the skill; see THIRD_PARTY_NOTICES.md.
"""
from __future__ import annotations

import hashlib
import re
import struct
import sys
from pathlib import Path

#: The upstream sizes this repository ships, and the cell each one declares.
#: The converter checks the font against the row it is asked for rather than
#: trusting the file name, so a mislabelled download cannot pass silently.
SIZES = {
    "8px": (8, 8, 7),
    "10px": (10, 10, 9),
    "12px": (12, 12, 10),
}


def parse_bdf(path: Path):
    """Yield (codepoint, width, height, x-offset, y-offset, advance, rows)."""
    glyph, rows, in_bitmap = None, None, False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("STARTCHAR"):
            glyph, rows, in_bitmap = {}, [], False
        elif line.startswith("ENCODING"):
            glyph["cp"] = int(line.split()[1])
        elif line.startswith("BBX"):
            _, w, h, ox, oy = line.split()
            glyph.update(w=int(w), h=int(h), ox=int(ox), oy=int(oy))
        elif line.startswith("DWIDTH"):
            glyph["adv"] = int(line.split()[1])
        elif line.startswith("BITMAP"):
            in_bitmap = True
        elif line.startswith("ENDCHAR"):
            if glyph.get("cp", -1) >= 0:
                yield glyph["cp"], glyph, rows
            glyph, in_bitmap = None, False
        elif in_bitmap and re.fullmatch(r"[0-9A-Fa-f]+", line or ""):
            rows.append(int(line, 16))

    if glyph is not None and glyph.get("cp", -1) >= 0:
        yield glyph["cp"], glyph, rows


def font_metrics(path: Path):
    ascent = descent = None
    box = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("FONT_ASCENT"):
            ascent = int(line.split()[1])
        elif line.startswith("FONT_DESCENT"):
            descent = int(line.split()[1])
        elif line.startswith("FONTBOUNDINGBOX"):
            box = tuple(int(v) for v in line.split()[1:])
        elif line.startswith("ENDPROPERTIES"):
            break
    return ascent, descent, box


def _record(cell_w: int, cell_h: int):
    return struct.Struct(f"<IB{(cell_w * cell_h + 7) // 8}s")


def cell_bits(glyph, rows, ascent: int, cell_w: int, cell_h: int) -> tuple[int, bool]:
    """Pack one glyph into the 12x12 cell; False if it does not fit.

    A BDF bitmap row is written as ``ceil(w/8)`` bytes with the glyph's own
    ``w`` bits in the **high** positions and the rest as padding, so pixel
    ``k`` is bit ``field - 1 - k`` and not bit ``w - 1 - k``.  Reading from
    ``w - 1`` shifts every glyph sideways and mangles the half-width ASCII
    glyphs, which is what the letterform check in the suite watches for.
    """
    w, h, ox, oy = glyph["w"], glyph["h"], glyph["ox"], glyph["oy"]
    if ox < 0 or ox + w > cell_w or h > cell_h:
        return 0, False
    field = 8 * ((w + 7) // 8)
    bits = 0
    for j in range(h):
        # BDF rows run top to bottom; the cell's row 0 is the top of the line,
        # and the font states its ascent, so a glyph's rows land at
        # ascent - yoff - h + j.
        r = ascent - oy - h + j
        if not 0 <= r < cell_h:
            return 0, False
        row = rows[j] if j < len(rows) else 0
        for k in range(w):
            if row >> (field - 1 - k) & 1:
                b = r * cell_w + ox + k
                bits |= 1 << (cell_w * cell_h - 1 - b)
    return bits, True


def build(bdf: Path, out: Path, size: str) -> int:
    if size not in SIZES:
        raise SystemExit(f"unknown size {size!r}; this writes {', '.join(SIZES)}")
    cell_w, cell_h, ascent_expected = SIZES[size]
    ascent, descent, box = font_metrics(bdf)
    if box is None or ascent is None or descent is None:
        raise SystemExit(f"{bdf}: not a BDF with font metrics")
    if (box[0], ascent + descent) != (cell_w, cell_h) or ascent != ascent_expected:
        raise SystemExit(
            f"{bdf}: this font is {box[0]}x{ascent + descent} with ascent {ascent}, "
            f"but {size} means {cell_w}x{cell_h} with ascent {ascent_expected}; "
            "check which release you downloaded"
        )

    record = _record(cell_w, cell_h)
    payload_bytes = (cell_w * cell_h + 7) // 8
    records = {}
    skipped = []
    for cp, glyph, rows in parse_bdf(bdf):
        bits, fits = cell_bits(glyph, rows, ascent, cell_w, cell_h)
        if not fits:
            skipped.append(cp)
            continue
        advance = glyph["adv"]
        if not 0 < advance <= 255:
            skipped.append(cp)
            continue
        records[cp] = record.pack(cp, advance, bits.to_bytes(payload_bytes, "big"))

    blob = b"".join(records[cp] for cp in sorted(records))
    out.write_bytes(blob)
    digest = hashlib.sha256(blob).hexdigest()
    cjk = sum(1 for cp in records if 0x4E00 <= cp <= 0x9FFF)
    print(f"{bdf.name}: {size} = {cell_w}x{cell_h}, ascent {ascent}, descent {descent}")
    print(f"glyphs stored : {len(records)}  (CJK Unified {cjk})")
    print(f"glyphs skipped: {len(skipped)} "
          f"({', '.join(f'U+{cp:04X}' for cp in sorted(skipped)[:8])}"
          f"{' ...' if len(skipped) > 8 else ''})")
    print(f"wrote {out} : {len(blob)} bytes ({len(blob) / len(records):.0f} per glyph)")
    print(f"sha256 {digest}")
    return 0


def main(argv) -> int:
    if len(argv) < 3:
        print(__doc__.strip().splitlines()[0])
        print("usage: build_fusion_font.py <input.bdf> <size> [output.bin]")
        print(f"       size is one of {', '.join(SIZES)}")
        return 2
    bdf, size = Path(argv[1]), argv[2]
    default = (Path(__file__).resolve().parent.parent /
               "skills/astra-pixel-art/scripts/astra_pixelart" / f"fontfusion{size.rstrip('px')}.bin")
    out = Path(argv[3]) if len(argv) > 3 else default
    return build(bdf, out, size)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
