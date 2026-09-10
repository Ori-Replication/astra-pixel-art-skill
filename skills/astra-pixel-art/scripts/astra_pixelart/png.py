"""PNG encode/decode in pure Python (standard library only).

The engine must run where nothing can be installed (the Claude API sandbox
has no network and no runtime package installation), so image I/O cannot
depend on Pillow.  PNG is a small, fully specified format and both
directions fit in a few hundred lines:

* :func:`encode_rgba` writes an 8-bit RGBA PNG from raw scanlines.
* :func:`decode_rgba` reads the 8-bit greyscale / truecolour / palette /
  truecolour-alpha forms back into RGBA.

Everything here is deliberately dependency-free; Pillow stays optional and
is only used for formats PNG cannot express (JPEG, GIF).
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# colour types of the PNG spec
GREY, TRUECOLOUR, PALETTE, GREY_ALPHA, TRUECOLOUR_ALPHA = 0, 2, 3, 4, 6
_CHANNELS = {GREY: 1, TRUECOLOUR: 3, PALETTE: 1, GREY_ALPHA: 2, TRUECOLOUR_ALPHA: 4}


class PngError(ValueError):
    """Raised for input this module cannot decode, with a usable message."""


def _chunk(tag: bytes, payload: bytes) -> bytes:
    body = tag + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def encode_rgba(width: int, height: int, pixels: bytes | bytearray, compress: int = 9) -> bytes:
    """Encode ``width * height * 4`` RGBA bytes as an 8-bit RGBA PNG."""
    expected = width * height * 4
    if len(pixels) != expected:
        raise PngError(f"expected {expected} RGBA bytes for {width}x{height}, got {len(pixels)}")
    if width <= 0 or height <= 0:
        raise PngError(f"image size must be positive, got {width}x{height}")
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0 (None) for every scanline
        raw += pixels[y * stride:(y + 1) * stride]
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, TRUECOLOUR_ALPHA, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(raw), compress))
        + _chunk(b"IEND", b"")
    )


def resize_nearest(width: int, height: int, pixels: bytes | bytearray,
                   out_width: int, out_height: int, bpp: int = 4) -> bytearray:
    """Nearest-neighbour resample to an arbitrary size.

    Nearest neighbour is the only resampling pixel art wants: it never
    invents a colour, so the palette survives and the edges stay hard.

    ``bpp`` is bytes per pixel — 4 for RGBA, 1 for palette indices.  An exact
    integer multiple is done by repeating every pixel run, which is the same
    result as the general path but skips the per-pixel work; that path is
    what every upscale by a whole number takes.
    """
    if out_width <= 0 or out_height <= 0:
        raise PngError(f"output size must be positive, got {out_width}x{out_height}")
    if out_width % width == 0 and out_height % height == 0:
        kx, ky = out_width // width, out_height // height
        src_stride = width * bpp
        out_stride = out_width * bpp
        out = bytearray(out_stride * out_height)
        for y in range(height):
            row = pixels[y * src_stride:(y + 1) * src_stride]
            wide = bytearray(out_stride)
            for x in range(width):
                wide[x * kx * bpp:(x + 1) * kx * bpp] = row[x * bpp:(x + 1) * bpp] * kx
            for j in range(ky):
                start = (y * ky + j) * out_stride
                out[start:start + out_stride] = wide
        return out
    out = bytearray(out_width * out_height * bpp)
    src_stride, out_stride = width * bpp, out_width * bpp
    for y in range(out_height):
        src_row = min(height - 1, y * height // out_height) * src_stride
        dst_row = y * out_stride
        for x in range(out_width):
            sx = min(width - 1, x * width // out_width) * bpp
            dst = dst_row + x * bpp
            out[dst:dst + bpp] = pixels[src_row + sx:src_row + sx + bpp]
    return out


def encode_indexed(width: int, height: int, indices: bytes | bytearray,
                   palette, compress: int = 9) -> bytes:
    """Encode an indexed-colour PNG (colour type 3, PLTE + tRNS).

    ``indices`` is one byte per pixel, already remapped to positions in
    ``palette``; ``palette`` is a sequence of ``(r, g, b, a)``.  The
    palette is written into the file, so a reader gets the drawing's actual
    colours back rather than a flattened RGBA copy — which is what pixel-art
    tools expect, and smaller for flat art.
    """
    if len(indices) != width * height:
        raise PngError(
            f"expected {width * height} index bytes for {width}x{height}, got {len(indices)}"
        )
    if not palette:
        raise PngError("an indexed PNG needs at least one palette entry")
    if len(palette) > 256:
        raise PngError(f"indexed PNG holds at most 256 colours, got {len(palette)}")
    stride = width
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += indices[y * stride:(y + 1) * stride]
    plte = b"".join(bytes(entry[:3]) for entry in palette)
    alphas = bytes(entry[3] for entry in palette)
    while alphas and alphas[-1] == 255:
        alphas = alphas[:-1]
    out = (
        PNG_SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, PALETTE, 0, 0, 0))
        + _chunk(b"PLTE", plte)
    )
    if alphas:
        out += _chunk(b"tRNS", alphas)
    return out + _chunk(b"IDAT", zlib.compress(bytes(raw), compress)) + _chunk(b"IEND", b"")


def write_rgba(path, width: int, height: int, pixels: bytes | bytearray) -> int:
    """Encode and write; returns the number of bytes written."""
    data = encode_rgba(width, height, pixels)
    Path(path).write_bytes(data)
    return len(data)


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> list[bytearray]:
    stride = width * bpp
    rows: list[bytearray] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        if pos >= len(raw):
            raise PngError("truncated PNG image data")
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        if len(line) != stride:
            raise PngError("truncated PNG image data")
        pos += stride
        if ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up = prev[i]
                upleft = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, up, upleft)) & 0xFF
        elif ftype != 0:
            raise PngError(f"unknown PNG filter type {ftype} on row {len(rows)}")
        rows.append(line)
        prev = line
    return rows


def decode_rgba(data: bytes) -> tuple[int, int, bytearray]:
    """Decode a PNG byte string into ``(width, height, RGBA bytes)``."""
    if data[:8] != PNG_SIGNATURE:
        raise PngError("not a PNG file (bad signature)")
    pos = 8
    idat = bytearray()
    palette_rows: list[tuple[int, int, int]] = []
    alpha_rows: list[int] = []
    width = height = depth = ctype = interlace = 0
    have_ihdr = False
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, depth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", payload)
            have_ihdr = True
        elif tag == b"PLTE":
            palette_rows = [tuple(payload[i:i + 3]) for i in range(0, len(payload) - 2, 3)]
        elif tag == b"tRNS":
            alpha_rows = list(payload)
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
    if not have_ihdr:
        raise PngError("PNG has no IHDR chunk")
    if interlace:
        raise PngError("interlaced PNG is not supported; re-save it as non-interlaced")
    if depth != 8:
        raise PngError(f"only 8-bit PNG is supported, got bit depth {depth}")
    if ctype not in _CHANNELS:
        raise PngError(f"unsupported PNG colour type {ctype}")

    bpp = _CHANNELS[ctype]
    rows = _unfilter(zlib.decompress(bytes(idat)), width, height, bpp)
    out = bytearray(width * height * 4)

    def put(index: int, rgba: tuple[int, int, int, int]) -> None:
        out[index:index + 4] = bytes(rgba)

    for y, line in enumerate(rows):
        base = y * width * 4
        if ctype == TRUECOLOUR_ALPHA:
            out[base:base + width * 4] = line
        elif ctype == TRUECOLOUR:
            for x in range(width):
                r, g, b = line[x * 3:x * 3 + 3]
                put(base + x * 4, (r, g, b, 255))
        elif ctype == GREY:
            for x in range(width):
                v = line[x]
                put(base + x * 4, (v, v, v, 255))
        elif ctype == GREY_ALPHA:
            for x in range(width):
                v, a = line[x * 2], line[x * 2 + 1]
                put(base + x * 4, (v, v, v, a))
        else:  # PALETTE
            for x in range(width):
                i = line[x]
                if i >= len(palette_rows):
                    raise PngError(f"palette index {i} out of range on row {y}")
                r, g, b = palette_rows[i]
                a = alpha_rows[i] if i < len(alpha_rows) else 255
                put(base + x * 4, (r, g, b, a))
    return width, height, out


def read_rgba(path) -> tuple[int, int, bytearray]:
    """Read and decode a PNG file into ``(width, height, RGBA bytes)``."""
    return decode_rgba(Path(path).read_bytes())
