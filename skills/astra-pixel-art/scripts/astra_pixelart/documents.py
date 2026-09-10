"""Layered documents: hand a drawing to another editor.

A drawing here is already layered — one canvas is one layer — so it can be
written as a real layered document instead of only a flattened PNG.  Two
formats, both produced with the standard library alone:

* **Aseprite** (``.ase``, also spelled ``.aseprite``) is the pixel-art
  default.  It is a chunked binary format whose specification lives in
  Aseprite's own repository; every cel is zlib-compressed, and ``zlib`` is in
  the standard library, so nothing has to be installed to write one.
* **OpenRaster** (``.ora``) is the open interchange format, read by Krita,
  GIMP and MyPaint among others: a ZIP holding ``stack.xml``, one PNG per
  layer, a thumbnail and the merged image.  ``zipfile`` and this package's
  own PNG encoder cover it.

Both are written as RGBA.  Aseprite's indexed mode exists, but it would
impose that format's 256-colour limit on a drawing the engine happily lets
grow past it, and the pixel format is not what carries the layers.  The
palette travels alongside in both formats, names included.

Nothing here reads a document back: importing someone else's layered file
would mean reproducing another program's compositing, and a layer stack that
survives a round trip has to mean the same thing on both sides.  That is a
different feature from handing work out.
"""
from __future__ import annotations

import struct
import zipfile
import zlib
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Tuple

from .png import encode_rgba, resize_nearest

RGBA = Tuple[int, int, int, int]

#: Aseprite's chunk and magic numbers, from its file format specification.
ASE_MAGIC = 0xA5E0
ASE_FRAME_MAGIC = 0xF1FA
ASE_LAYER_CHUNK = 0x2004
ASE_CEL_CHUNK = 0x2005
ASE_PALETTE_CHUNK = 0x2019

#: Aseprite stores one BYTE of layer opacity, and its palettes are 256
#: entries in practice; the colour information is carried by the pixels when
#: a drawing outgrows that, so the palette is clamped rather than fatal.
ASE_MAX_PALETTE = 256

#: Frame duration in milliseconds.  A still drawing has one frame, and
#: Aseprite's own default for a new sprite is 100.
ASE_FRAME_MS = 100

#: A fixed timestamp for the ZIP entries, so the same drawing writes the same
#: bytes.  The DOS epoch is the conventional "no date" value.
ZIP_DATE = (1980, 1, 1, 0, 0, 0)

ORA_MIMETYPE = "image/openraster"


class DocumentError(ValueError):
    """Raised when a drawing cannot be written as a layered document."""


class Document(NamedTuple):
    """Everything a layered writer needs, in plain data.

    ``layers`` is bottom-first, the order the engine composites in, so a
    writer that stores the other way round reverses it itself.
    """

    width: int
    height: int
    layers: List[Tuple[str, bytes]]
    palette: List[Tuple[str, RGBA]]
    merged: bytes


def _alpha_bounds(rgba: bytes, width: int, height: int) -> Optional[Tuple[int, int, int, int]]:
    """The inclusive bounding box of pixels with alpha > 0, or None if empty."""
    x0, y0, x1, y1 = width, height, -1, -1
    stride = width * 4
    for y in range(height):
        row = rgba[y * stride:(y + 1) * stride]
        if not any(row[x * 4 + 3] for x in range(width)):
            continue
        if y < y0:
            y0 = y
        y1 = y
        for x in range(width):
            if row[x * 4 + 3]:
                if x < x0:
                    x0 = x
                if x > x1:
                    x1 = x
    if x1 < 0:
        return None
    return x0, y0, x1, y1


def _crop(rgba: bytes, width: int, box: Tuple[int, int, int, int]) -> bytes:
    """The RGBA rows inside ``box``, packed without the surrounding canvas."""
    x0, y0, x1, y1 = box
    span = (x1 - x0 + 1) * 4
    out = bytearray()
    for y in range(y0, y1 + 1):
        start = (y * width + x0) * 4
        out += rgba[start:start + span]
    return bytes(out)


def _ase_string(text: str) -> bytes:
    data = text.encode("utf-8")[:0xFFFF]
    return struct.pack("<H", len(data)) + data


def write_ase(path, doc: Document) -> None:
    """Write an Aseprite document: one frame, one cel per non-empty layer."""
    chunks: List[Tuple[int, bytes]] = []

    # Layers first: in the first frame they define the whole layer layout, and
    # a cel refers to its layer by this order.  Flags are visible + editable.
    for name, _ in doc.layers:
        chunks.append((ASE_LAYER_CHUNK,
                       struct.pack("<HHHHHHB3x", 3, 0, 0, 0, 0, 0, 255) + _ase_string(name)))

    # One cel per layer, cropped to what is actually painted.  A layer with
    # nothing visible gets no cel at all, which is what Aseprite itself writes
    # for an empty layer.
    for index, (_, rgba) in enumerate(doc.layers):
        box = _alpha_bounds(rgba, doc.width, doc.height)
        if box is None:
            continue
        x0, y0, x1, y1 = box
        w, h = x1 - x0 + 1, y1 - y0 + 1
        payload = zlib.compress(_crop(rgba, doc.width, box), 9)
        chunks.append((ASE_CEL_CHUNK,
                       struct.pack("<HhhBHh5x", index, x0, y0, 255, 2, 0)
                       + struct.pack("<HH", w, h) + payload))

    entries = doc.palette[:ASE_MAX_PALETTE]
    if entries:
        body = bytearray(struct.pack("<III8x", len(entries), 0, len(entries) - 1))
        for name, (r, g, b, a) in entries:
            if name:
                body += struct.pack("<HBBBB", 1, r, g, b, a) + _ase_string(name)
            else:
                body += struct.pack("<HBBBB", 0, r, g, b, a)
        chunks.append((ASE_PALETTE_CHUNK, bytes(body)))

    frame_body = b"".join(
        struct.pack("<IH", 6 + len(data), chunk_type) + data
        for chunk_type, data in chunks
    )
    frame = struct.pack("<IHHH2xI", 16 + len(frame_body), ASE_FRAME_MAGIC, 0xFFFF,
                        ASE_FRAME_MS, len(chunks)) + frame_body

    header = struct.pack(
        "<IHHHHHIHII",
        128 + len(frame),          # file size, patched into place below
        ASE_MAGIC,
        1,                          # frames
        doc.width,
        doc.height,
        32,                         # colour depth: RGBA
        1,                          # flags: layer opacity carries a real value
        0,                          # deprecated speed field
        0,
        0,
    )
    header += bytes(1 + 3)                      # transparent index + reserved
    header += struct.pack("<HBBhhHH", 0, 1, 1, 0, 0, 0, 0)
    header += bytes(84)                         # reserved for the future
    if len(header) != 128:
        raise DocumentError(f"internal: Aseprite header is {len(header)} bytes, expected 128")

    Path(path).write_bytes(header + frame)


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))


def _thumbnail(doc: Document) -> Tuple[int, int, bytes]:
    """A thumbnail no larger than 256 px on its long edge, never upscaled."""
    w, h = doc.width, doc.height
    if max(w, h) <= 256:
        return w, h, doc.merged
    scale = 256 / max(w, h)
    tw, th = max(1, int(w * scale)), max(1, int(h * scale))
    return tw, th, bytes(resize_nearest(w, h, doc.merged, tw, th))


def write_ora(path, doc: Document) -> None:
    """Write an OpenRaster document: stack.xml plus a PNG per layer."""
    # The layer stack lists the topmost layer first; the engine orders layers
    # bottom-first, so the list is reversed here and only here.
    top_first = list(reversed(doc.layers))

    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<image version="0.0.3" w="{doc.width}" h="{doc.height}">',
           "  <stack>"]
    for index, (name, _) in enumerate(top_first):
        xml.append(f'    <layer name="{_xml_escape(name)}" src="data/layer{index}.png" '
                   'x="0" y="0" visibility="visible" opacity="1" composite-op="svg:src-over"/>')
    xml += ["  </stack>", "</image>", ""]
    stack = "\n".join(xml).encode("utf-8")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        # mimetype has to be the first entry and stored uncompressed.
        info = zipfile.ZipInfo("mimetype", ZIP_DATE)
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, ORA_MIMETYPE)
        archive.writestr(zipfile.ZipInfo("stack.xml", ZIP_DATE), stack)
        for index, (_, rgba) in enumerate(top_first):
            archive.writestr(zipfile.ZipInfo(f"data/layer{index}.png", ZIP_DATE),
                             encode_rgba(doc.width, doc.height, rgba))
        archive.writestr(zipfile.ZipInfo("mergedimage.png", ZIP_DATE),
                         encode_rgba(doc.width, doc.height, doc.merged))
        tw, th, thumb = _thumbnail(doc)
        archive.writestr(zipfile.ZipInfo("Thumbnails/thumbnail.png", ZIP_DATE),
                         encode_rgba(tw, th, thumb))


#: What each format is called, the extension it gets, and the writer for it.
FORMATS = {
    "ase": (".ase", write_ase),
    "aseprite": (".aseprite", write_ase),
    "ora": (".ora", write_ora),
    "openraster": (".ora", write_ora),
}


def write(format_name: str, path, doc: Document) -> Path:
    """Write ``doc`` as ``format_name`` and return the path written."""
    key = str(format_name).strip().lower().lstrip(".")
    if key not in FORMATS:
        known = ", ".join(sorted(FORMATS))
        raise DocumentError(f"unknown format {format_name!r}; this writes {known}")
    extension, writer = FORMATS[key]
    target = Path(path)
    if target.suffix.lower() not in (".ase", ".aseprite", ".ora"):
        target = target.with_name(target.name + extension)
    writer(target, doc)
    return target


def palette_entries(palette) -> List[Tuple[str, RGBA]]:
    """The palette of a drawing as ``(name, rgba)`` pairs, in index order."""
    return [(entry["name"], tuple(entry["rgba"])) for entry in palette.to_list()]


def layers_of(canvases: Sequence) -> List[Tuple[str, bytes]]:
    """Every canvas as ``(name, RGBA bytes)``, bottom layer first."""
    out: List[Tuple[str, bytes]] = []
    for index, canvas in enumerate(canvases):
        width, height, rgba = canvas.to_rgba()
        if (width, height) != (canvases[0].w, canvases[0].h):
            raise DocumentError(
                f"all layers must be the same size; {canvas.name or 'canvas'} is "
                f"{width}x{height}, expected {canvases[0].w}x{canvases[0].h}"
            )
        out.append((canvas.name or f"layer{index + 1}", bytes(rgba)))
    return out
