"""Astra Pixel Art engine — pure-standard-library pixel art for agents.

Nothing here imports a third-party package: PNG encoding/decoding,
rasterisation, the bitmap font and the session log are all standard
library.  Pillow is optional and only used for formats PNG cannot express
(JPEG/GIF import); see :func:`astra_pixelart.optional.read_image`.
"""
from __future__ import annotations

from .canvas import Canvas, CanvasError, flatten, hex_of, parse_color
from .session import Session, SessionError, StepResult

__version__ = "0.4.0"

__all__ = [
    "Canvas",
    "CanvasError",
    "Session",
    "SessionError",
    "StepResult",
    "flatten",
    "hex_of",
    "parse_color",
    "__version__",
]
