"""Geometry fixes applied to a straightened frame: de-skew, crop and orient."""

from __future__ import annotations

import numpy as np
from PIL import Image

from fsd.processing.detect import CropBox


def _limits(dtype: np.dtype) -> tuple[float, float]:
    if np.issubdtype(dtype, np.integer):
        info = np.iinfo(dtype)
        return float(info.min), float(info.max)
    return -np.inf, np.inf


def _edge(channel: np.ndarray) -> np.ndarray:
    return np.concatenate((channel[0], channel[-1], channel[:, 0], channel[:, -1]))


def straighten(img: np.ndarray, angle: float) -> np.ndarray:
    """Rotate ``angle`` degrees CCW, keeping size, dtype and channel count.

    Each channel is rotated as float32 with Pillow's bicubic kernel, using that
    channel's edge median as the fill colour, then clipped and cast back.
    """
    if abs(angle) < 1e-6:
        return img
    arr = np.asarray(img)
    lo, hi = _limits(arr.dtype)
    planar = arr.ndim == 2
    channels = [arr] if planar else [arr[..., c] for c in range(arr.shape[2])]
    rotated = []
    for channel in channels:
        c = np.asarray(channel, np.float32)
        fill = float(np.median(_edge(c)))
        r = np.asarray(
            Image.fromarray(c, "F").rotate(
                angle, resample=Image.BICUBIC, fillcolor=fill
            )
        )
        rotated.append(np.clip(r, lo, hi).astype(arr.dtype))
    return rotated[0] if planar else np.stack(rotated, axis=-1)


def crop(img: np.ndarray, box: CropBox) -> np.ndarray:
    """Cut ``box`` (fractions of width/height) out of ``img``, at least 1x1."""
    arr = np.asarray(img)
    h, w = arr.shape[:2]
    x0, y0, x1, y1 = box
    px0 = int(np.clip(round(x0 * w), 0, w - 1))
    py0 = int(np.clip(round(y0 * h), 0, h - 1))
    px1 = int(np.clip(round(x1 * w), px0 + 1, w))
    py1 = int(np.clip(round(y1 * h), py0 + 1, h))
    return arr[py0:py1, px0:px1]


def orient(img: np.ndarray, rotate90: int, mirror: bool) -> np.ndarray:
    """Rotate CCW by ``rotate90`` quarter turns, then flip left-right."""
    out = np.rot90(np.asarray(img), int(rotate90) % 4)
    return np.fliplr(out) if mirror else out
