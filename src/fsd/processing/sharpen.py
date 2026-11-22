"""Sharpening of finished positives: an unsharp mask on luminance only."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


@dataclass(frozen=True)
class SharpenParams:
    amount: float = 0.0
    radius: float = 1.0
    threshold: float = 0.0


def _box_blur(img: np.ndarray, rad: int) -> np.ndarray:
    """One box-blur pass via cumulative sums, edge padding, separable."""
    h, w = img.shape
    win = 2 * rad + 1

    padded = np.pad(img, ((0, 0), (rad, rad)), mode="edge")
    cs = np.concatenate(
        (np.zeros((h, 1), np.float64), np.cumsum(padded, axis=1, dtype=np.float64)),
        axis=1,
    )
    rows = (cs[:, win:] - cs[:, :-win]) / win

    padded = np.pad(rows, ((rad, rad), (0, 0)), mode="edge")
    cs = np.concatenate(
        (np.zeros((1, w), np.float64), np.cumsum(padded, axis=0, dtype=np.float64)),
        axis=0,
    )
    return (cs[win:, :] - cs[:-win, :]) / win


def sharpen(rgb16: np.ndarray, p: SharpenParams, *, scale: float = 1.0) -> np.ndarray:
    """Return a uint16 RGB image sharpened with an unsharp mask on luminance."""
    if p.amount == 0.0:
        return rgb16.copy()

    img = np.asarray(rgb16, np.float32) / 65535.0
    lum = img @ _LUMA
    rad = max(1, int(round(p.radius * scale)))
    # lazy: 3x box blur approximates a Gaussian; switch to a true separable Gaussian if halos show.
    blurred = _box_blur(_box_blur(_box_blur(lum, rad), rad), rad)
    detail = lum - blurred
    if p.threshold > 0:
        detail = np.where(np.abs(detail) > p.threshold, detail, 0.0)
    out = np.clip(img + (p.amount * detail)[..., None], 0.0, 1.0)
    return np.round(out * 65535.0).astype(np.uint16)
