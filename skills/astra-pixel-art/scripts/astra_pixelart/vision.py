"""Prepare a drawing for a vision model's eyes.

Why this exists
---------------
Feeding a pixel-art image to a vision model is not the same as feeding it a
photograph.  Measured: upscaling a sprite straight to 512x512
and handing it over made the model judge the picture *worse* — it lost track
of overall composition and aesthetics.  The likely cause is that a hard
nearest-neighbour upscale produces enormous perfectly uniform colour blocks
on a perfectly regular grid, and a ViT spends its capacity on that
regularity instead of on the drawing.

The fix that tested best is a thin transparent margin along the **top and
left** edges before scaling, so the artwork's pixel grid no longer lines up
with the model's patch grid and no single flat region dominates.  The tested
margin is proportional to the artwork, one sixteenth of the long edge:

    16x16 -> 1 px      32x32 -> 2 px      64x64 -> 4 px      256x256 -> 16 px

which survives the resize as roughly 6% of the final image.  Nothing is added
by default at 512 and above, where there is no upscaling left to break up; an
explicit margin still applies there.

The margin is a *viewing* transform.  It is never applied to `save()`: the
deliverable is the drawing itself, at its own size, with no margin shifting
it.
"""
from __future__ import annotations

from .canvas import CanvasError
from .png import resize_nearest

TARGET = 512          # the long edge a vision module is given by default
MARGIN_DIVISOR = 16   # tested: margin = long_edge / 16
DEFAULT_EDGES = "tl"  # tested: the margin goes along the top and the left

_EDGE_ORDER = "tlbr"


def resolve_margin(long_edge: int, margin=None) -> int:
    """How many source pixels of margin this artwork gets.

    ``None`` applies the tested rule (one sixteenth of the long edge, and
    nothing at all from 512 up); an integer is taken literally.
    """
    if margin is None:
        if long_edge >= TARGET:
            return 0
        return max(1, round(long_edge / MARGIN_DIVISOR))
    value = int(margin)
    if value < 0:
        raise CanvasError(f"margin must be 0 or more, got {margin!r}")
    return value


def parse_edges(edges: str) -> tuple[bool, bool, bool, bool]:
    """Which sides get the margin, as ``(top, left, bottom, right)``.

    Accepts ``"tl"`` (the tested default), ``"all"``, ``"none"``, or any
    combination of ``t``/``l``/``b``/``r``.
    """
    if not isinstance(edges, str):
        raise CanvasError(f'edges must be a string like "tl", "all" or "none", got {edges!r}')
    spec = edges.strip().lower()
    if spec in ("none", ""):
        return (False, False, False, False)
    if spec == "all":
        return (True, True, True, True)
    if any(ch not in _EDGE_ORDER for ch in spec) or len(set(spec)) != len(spec):
        raise CanvasError(
            f'edges must be "tl", "all", "none" or a combination of t/l/b/r, got {edges!r}'
        )
    return tuple(ch in spec for ch in _EDGE_ORDER)  # type: ignore[return-value]


def describe_edges(edges: str) -> str:
    """A phrase for a message: "tl" -> "the top and left"."""
    top, left, bottom, right = parse_edges(edges)
    names = [name for name, on in (("top", top), ("left", left),
                                   ("bottom", bottom), ("right", right)) if on]
    if len(names) == 4:
        return "every side"
    if not names:
        return "no side"
    if len(names) == 1:
        return f"the {names[0]} edge"
    return "the " + " and ".join(names)


def pad(width: int, height: int, rgba: bytes | bytearray,
        margin: int, edges: str = DEFAULT_EDGES) -> tuple[int, int, bytearray, int, int]:
    """Add a transparent margin; returns ``(w, h, pixels, offset_x, offset_y)``.

    ``margin == 0`` returns the pixels untouched, which is what lets the
    faithful render and the vision render share one code path.
    """
    top, left, bottom, right = parse_edges(edges)
    if margin <= 0 or not any((top, left, bottom, right)):
        return width, height, bytearray(rgba), 0, 0
    offset_x = margin if left else 0
    offset_y = margin if top else 0
    padded_w = width + (margin if left else 0) + (margin if right else 0)
    padded_h = height + (margin if top else 0) + (margin if bottom else 0)
    out = bytearray(padded_w * padded_h * 4)          # all zero = transparent
    src_stride, dst_stride = width * 4, padded_w * 4
    for y in range(height):
        src = y * src_stride
        dst = (y + offset_y) * dst_stride + offset_x * 4
        out[dst:dst + src_stride] = rgba[src:src + src_stride]
    return padded_w, padded_h, out, offset_x, offset_y


def _fit_in_frame(width: int, height: int, pixels: bytearray,
                  frame: tuple[int, int]) -> tuple[int, int, bytearray, float]:
    """Centre-fit ``pixels`` inside an exact frame; leftover stays transparent.

    The fit preserves the aspect ratio: a frame is a shape to deliver into, not
    an excuse to distort the drawing.
    """
    frame_w, frame_h = frame
    scale = min(frame_w / width, frame_h / height)
    fit_w = min(frame_w, max(1, round(width * scale)))
    fit_h = min(frame_h, max(1, round(height * scale)))
    scaled = resize_nearest(width, height, pixels, fit_w, fit_h)
    out = bytearray(frame_w * frame_h * 4)
    off_x = (frame_w - fit_w) // 2
    off_y = (frame_h - fit_h) // 2
    for y in range(fit_h):
        src = y * fit_w * 4
        dst = ((y + off_y) * frame_w + off_x) * 4
        out[dst:dst + fit_w * 4] = scaled[src:src + fit_w * 4]
    return frame_w, frame_h, out, scale


def prepare_for_vision(width: int, height: int, rgba: bytes | bytearray,
                       size=None, margin=None, edges: str = DEFAULT_EDGES) -> dict:
    """Pad, then scale to ``size``.

    ``size`` is one of:

    * ``None`` — the long edge goes to :data:`TARGET` (512), the default;
    * an ``int`` — the long edge goes to that many pixels, **larger or
      smaller** than the artwork.  Scaling down drops pixels rather than
      blurring them, because the resampler is nearest-neighbour;
    * ``(w, h)`` — an exact frame: the drawing is fitted inside it without
      distortion and centred, and leftover frame space stays transparent.

    Returns ``{width, height, pixels, margin, edges, offset, scale, frame}``.
    """
    if width <= 0 or height <= 0:
        raise CanvasError(f"image size must be positive, got {width}x{height}")
    expected = width * height * 4
    if len(rgba) != expected:
        raise CanvasError(f"expected {expected} RGBA bytes for {width}x{height}, got {len(rgba)}")

    resolved = resolve_margin(max(width, height), margin)
    padded_w, padded_h, padded, offset_x, offset_y = pad(width, height, rgba, resolved, edges)

    if isinstance(size, (tuple, list)):
        frame = (int(size[0]), int(size[1]))
        if frame[0] <= 0 or frame[1] <= 0:
            raise CanvasError(f"frame size must be positive, got {frame[0]}x{frame[1]}")
        out_w, out_h, out, scale = _fit_in_frame(padded_w, padded_h, padded, frame)
    else:
        frame = None
        target = TARGET if size is None else int(size)
        if target <= 0:
            raise CanvasError(f"size must be positive, got {size!r}")
        scale = target / max(padded_w, padded_h)
        out_w = max(1, round(padded_w * scale))
        out_h = max(1, round(padded_h * scale))
        out = resize_nearest(padded_w, padded_h, padded, out_w, out_h)

    return {
        "width": out_w,
        "height": out_h,
        "pixels": out,
        "margin": resolved,
        "edges": edges,
        "offset": (offset_x, offset_y),
        "scale": scale,
        "frame": frame,
    }
