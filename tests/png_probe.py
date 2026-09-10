#!/usr/bin/env python3
"""Check exported PNGs by decoding them independently of the engine.

Uses Pillow when it happens to be installed (a genuinely independent
decoder) and falls back to a small standard-library reader otherwise, so
the check works in a bare container too.

    python3 tests/png_probe.py <workspace> [--quiet]

Exits non-zero and prints every mismatch when the exports are wrong.
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


def read_png_stdlib(path: Path):
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"{path}: not a PNG")
    pos, idat, w, h, depth, ctype = 8, b"", 0, 0, 0, 0
    while pos + 8 <= len(data):
        (ln,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if (depth, ctype) != (8, 6):
        raise SystemExit(f"{path}: expected 8-bit RGBA, got depth={depth} type={ctype}")
    raw = zlib.decompress(idat)
    stride, rows, prev, p = w * 4, [], bytearray(w * 4), 0
    for _ in range(h):
        ftype = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ftype == 1:
            for i in range(4, stride):
                line[i] = (line[i] + line[i - 4]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                a = line[i - 4] if i >= 4 else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - 4] if i >= 4 else 0
                b = prev[i]
                c = prev[i - 4] if i >= 4 else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        elif ftype != 0:
            raise SystemExit(f"{path}: unknown filter {ftype}")
        rows.append(line)
        prev = line
    return w, h, lambda x, y: tuple(rows[y][x * 4:x * 4 + 4])


def reader(path: Path):
    try:
        from PIL import Image  # optional: only used to cross-check

        im = Image.open(path).convert("RGBA")
        return im.size[0], im.size[1], lambda x, y: im.getpixel((x, y))
    except ImportError:
        return read_png_stdlib(path)


def main() -> int:
    root = Path(sys.argv[1])
    quiet = "--quiet" in sys.argv
    problems: list[str] = []
    png1 = root / "exports" / "night.png"
    png4 = root / "exports" / "night@4x.png"

    for path in (png1, png4):
        if not path.is_file():
            problems.append(f"missing {path}")
    if problems:
        print("PIXELS " + "; ".join(problems))
        return 1

    w, h, px = reader(png1)
    if (w, h) != (32, 24):
        problems.append(f"1x is {w}x{h}, expected 32x24")
    for x, y, want, what in (
        (20, 9, (244, 241, 222, 255), "moon centre"),
        (2, 2, (27, 42, 74, 255), "night sky"),
        (16, 21, (36, 58, 99, 255), "ground dither B"),
        (17, 21, (22, 33, 61, 255), "ground dither A"),
    ):
        got = tuple(px(x, y))
        if got != want:
            problems.append(f"{what} at ({x},{y}) is {got}, expected {want}")

    w4, h4, px4 = reader(png4)
    if (w4, h4) != (128, 96):
        problems.append(f"4x is {w4}x{h4}, expected 128x96")
    elif tuple(px4(20 * 4, 9 * 4)) != (244, 241, 222, 255):
        problems.append(f"4x moon block is {tuple(px4(20 * 4, 9 * 4))}")
    elif tuple(px4(2 * 4 + 3, 2 * 4 + 3)) != (27, 42, 74, 255):
        problems.append("4x nearest-neighbour block is not uniform")

    # 4x must be a pure nearest-neighbour blow-up of 1x
    if not problems:
        for y in range(0, h, 3):
            for x in range(0, w, 5):
                if tuple(px(x, y)) != tuple(px4(x * 4 + 2, y * 4 + 2)):
                    problems.append(f"4x pixel block at ({x},{y}) differs from 1x")
                    break

    print("PIXELS " + ("ok" if not problems else "; ".join(problems[:6])))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
