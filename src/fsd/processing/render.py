"""The one render pipeline: decoded negative in, finished positive out.

Every consumer -- half-size preview or full-size export -- calls :func:`render`,
so the two cannot drift. The base is measured on the whole capture *before*
cropping (the rebate lives outside the frame), then the frame is straightened,
cropped, inverted in the source's own transfer, oriented and sharpened. The
output is always 16-bit RGB, whatever the source depth.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from fsd.processing.detect import CropBox, Region
from fsd.processing.film import (
    FilmParams,
    analyse,
    invert_pq,
    invert_preview,
    invert_raw,
    sample_base,
)
from fsd.processing.geometry import crop, orient, straighten
from fsd.processing.sharpen import SharpenParams, sharpen
from fsd.processing.source import Decoded


@dataclass(frozen=True)
class FrameSettings:
    """Everything that changes how one frame is turned into a positive."""

    crop: CropBox = (0.0, 0.0, 1.0, 1.0)
    angle: float = 0.0
    rotate90: int = 0
    mirror: bool = False
    base_region: Region | None = None
    film: FilmParams = field(default_factory=lambda: FilmParams(mode="bw"))
    sharpen: SharpenParams = field(default_factory=SharpenParams)


def render(decoded: Decoded, s: FrameSettings, *, scale: float = 1.0) -> np.ndarray:
    """Turn a decoded negative into a uint16 RGB positive.

    ``scale`` is 1.0 for a full-size export and 0.5 for a half-size preview; it
    only scales the sharpen radius, so the two sizes show the same image.
    """
    lin = decoded.linear()
    px = crop(straighten(decoded.pixels, s.angle), s.crop)
    lin_c = crop(straighten(lin, s.angle), s.crop)

    # The rebate sits at the edge of the capture, where the backlight is
    # dimmest. Thin negative in the brighter middle can read clearer than that
    # base and would invert to flat black (12% of Roll001 frame 50). Nothing in
    # the picture can be clearer than clear film, so the frame's own brightest
    # pixels set a floor under the base.
    strip = sample_base(lin, region=s.base_region)
    brightest = sample_base(lin_c, percentile=99.9)
    base = tuple(max(a, b) for a, b in zip(strip, brightest))
    params = s.film.replace(base=base)

    measured = analyse(lin_c, params)
    kind = decoded.kind
    if kind == "raw":
        result = invert_raw(px, params, measured)
    elif kind == "pq":
        result = invert_pq(px, params, measured)
    elif kind == "srgb16":
        result = invert_raw((lin_c * 65535).astype(np.uint16), params, measured)
    elif kind == "srgb8":
        bgr8 = invert_preview(px[..., ::-1], params, measured)
        result = (bgr8[..., ::-1].astype(np.uint16) * 257).astype(np.uint16)
    else:
        raise ValueError(f"unknown decoded kind {kind!r}")

    result = orient(result, s.rotate90, s.mirror)
    return sharpen(result, s.sharpen, scale=scale)
