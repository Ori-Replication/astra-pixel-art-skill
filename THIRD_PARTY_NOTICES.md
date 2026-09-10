# Third-party notices

## PixelBench (MIT)

`skills/astra-pixel-art/scripts/astra_pixelart/font5x7.py` — the 5×7
bitmap font and its glyph table — was adapted from the MIT-licensed
**PixelBench** project:

> PixelBench — an agentic pixel-art benchmark harness.
> Copyright (c) 2025 PixelBench contributors.
> MIT License.

The original licence text is reproduced in full in [`LICENSE`](LICENSE).

The font is the only derived part. The canvas engine, the rasterisers, the
palette, the PNG codec and the session log were written for this project, in
the standard library, because the target sandboxes permit no runtime package
installation.

## Fusion Pixel Font (OFL-1.1)

`skills/astra-pixel-art/scripts/astra_pixelart/fontfusion8.bin`,
`fontfusion10.bin` and `fontfusion12.bin` — the 8×8, 10×10 and 12×12 bitmap
tables that draw Latin and CJK text — are derived from **Fusion Pixel Font**,
monospaced, Simplified Chinese, release `2026.09.01`:

> Fusion Pixel Font — https://github.com/TakWolf/fusion-pixel-font
> Copyright (c) 2022, TakWolf (https://takwolf.com).
> Licensed under the SIL Open Font License, Version 1.1.

Upstream ships the font as TrueType/OpenType, WOFF2, OTB, PCF and BDF, at 8,
10 and 12 pixels. The skill has no font rasteriser and must not gain one, so
the tables are built offline from the **BDF** releases — BDF is plain-text
bitmaps, so the converter needs nothing but the standard library either — by
[`tools/build_fusion_font.py`](tools/build_fusion_font.py). The output is not
a font file: each is a sorted table of 27,977 8×8 cells (13 bytes), 24,832
10×10 cells (18 bytes) or 36,533 12×12 cells (23 bytes), read with
`int.from_bytes` and bit tests.

The OFL text travels with the data at
`skills/astra-pixel-art/scripts/astra_pixelart/FUSION-PIXEL-OFL-1.1.txt`, as
the licence requires of a derived work. The table is a Modified Version in
OFL terms, it is not sold on its own, and no Reserved Font Name is declared
upstream, so the name is kept and credited. The upstream font is itself
assembled from OFL-1.1 sources (Ark Pixel Font, Misaki, MisekiBitmap,
BoutiqueBitmap 7×7 and 9×9, Cubic 11, Galmuri); their notices are in the
upstream repository. Each size covers a different set of characters, because
each was assembled from different sources: 14,717 hanzi of the 20,992 in CJK
Unified at 8px, 10,560 at 10px and 19,214 at 12px. Glyphs that do not fit a
cell are not carried over — 6 at 10px and 12px (4 private-use, 2 vertical
typography marks), none at 8px.

## Runtime dependencies

None. The engine imports only the Python standard library
(`zlib`, `struct`, `json`, `string`, `io`, `os`, `time`, `traceback`,
`contextlib`, `pathlib`, `unicodedata`, `typing`, and `zipfile` for layered
export).

Pillow is an **optional** extra: it is used only when an agent chooses to
import a JPEG or GIF source image to pixelate. It is not required to
draw, render or export anything, and it is never imported by
`pixelart.py` or by `canvas.py`.
