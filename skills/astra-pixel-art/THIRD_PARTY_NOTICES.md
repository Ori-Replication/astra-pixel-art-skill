# Third-party notices

Two bitmap fonts travel with this skill. Everything else — the canvas engine,
the rasterisers, the PNG codec, the path flattener, the layered writers and
the session log — was written for this project, in the standard library.

## PixelBench (MIT) — the 5×7 font

`scripts/astra_pixelart/font5x7.py` — the 5×7 bitmap font and its glyph
table — was adapted from the MIT-licensed **PixelBench** project:

> PixelBench — an agentic pixel-art benchmark harness.
> Copyright (c) 2025 PixelBench contributors.
> MIT License.

The MIT permission notice covering that code is reproduced in full in the
[LICENSE](LICENSE) file next to this one.

## Fusion Pixel Font (OFL-1.1) — the 12×12 font

`scripts/astra_pixelart/fontfusion8.bin`,
`…/fontfusion10.bin` and `…/fontfusion12.bin` — the 8×8, 10×10 and 12×12
bitmap tables used for Latin and CJK text — are derived from **Fusion Pixel Font**, 12px monospaced,
Simplified Chinese, release `2026.09.01`:

> Fusion Pixel Font — https://github.com/TakWolf/fusion-pixel-font
> Copyright (c) 2022, TakWolf (https://takwolf.com).
> Licensed under the SIL Open Font License, Version 1.1.

The full licence text travels with the data, beside it at
[`scripts/astra_pixelart/FUSION-PIXEL-OFL-1.1.txt`](scripts/astra_pixelart/FUSION-PIXEL-OFL-1.1.txt),
as the OFL requires of a derived work. The font is not sold on its own and
nothing here is sold at all; the table is a Modified Version in OFL terms,
built from the upstream BDF release by
[`tools/build_fusion_font.py`](../../tools/build_fusion_font.py), and it
carries no Reserved Font Name — the upstream declares none. Fusion Pixel
Font is itself assembled from OFL-1.1 sources — Ark Pixel Font, Misaki,
MisekiBitmap, BoutiqueBitmap 7×7 and 9×9, Cubic 11 and Galmuri — whose
notices are listed in the upstream repository.
