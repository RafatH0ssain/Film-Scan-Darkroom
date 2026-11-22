"""Find the frame in a copy-stand capture: where to crop, how far to straighten,
and where the film base can be sampled.

A capture holds more than the frame: the edges of the neighbouring frames, the
sprocket rebate, and on Rafat's rig an opaque holder with a window for each.
What bounds the frame is a *barrier* -- a band that is not picture:

- **holder mode** (the holder's bars are in shot): barrier = far darker than
  the picture. Measured on Roll001: holder ~0.0005 linear, picture ~0.03,
  i.e. two decades apart, so a one-decade threshold has room either side.
- **bare-strip mode** (film alone on the light): barrier = the clear base
  between frames, as bright or nearly as bright as the brightest film.

Starting from the centre, which is always inside the frame on a copy stand,
walk outward in narrow bands until a barrier is met. The hit points along each
side are fitted with a line, which gives the skew; the frame is then measured
again on the straightened image.

Everything runs on a luminance image no larger than 1000 px, in log10 units,
which makes it indifferent to the absolute scale of ``linear`` -- sensor
linear, sRGB linear and PQ-linear all work.

Every decision carries a confidence. Below the app's threshold the frame is
flagged for review instead of being trusted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

CropBox = tuple[float, float, float, float]  # x0, y0, x1, y1
Region = tuple[float, float, float, float]  # x, y, w, h -- sample_base's convention

_MAX_SIDE = 1000
_EPS = 1e-5
_BANDS = 24
#: Fraction of a window's pixels that must be barrier to call it one.
_HIT = 0.6
_MAX_ANGLE = 5.0


@dataclass
class Detection:
    crop: CropBox
    angle: float
    base_region: Region | None
    confidence: float
    reasons: list[str] = field(default_factory=list)


def _log_lum(linear: np.ndarray) -> np.ndarray:
    lum = np.asarray(linear, np.float32)
    if lum.ndim == 3:
        lum = lum[..., :3].mean(axis=2)
    h, w = lum.shape
    scale = min(1.0, _MAX_SIDE / max(h, w))
    if scale < 1.0:
        size = (max(1, round(w * scale)), max(1, round(h * scale)))
        lum = np.asarray(Image.fromarray(lum, "F").resize(size, Image.BOX))
    return np.log10(np.maximum(lum, 0) + _EPS)


def _rotate(img: np.ndarray, angle: float, fill: float) -> np.ndarray:
    if abs(angle) < 1e-6:
        return img
    return np.asarray(
        Image.fromarray(img, "F").rotate(angle, resample=Image.BICUBIC, fillcolor=fill)
    )


def _barrier(L: np.ndarray) -> tuple[np.ndarray, str, float]:
    """Boolean barrier mask, the mode name, and the barrier threshold."""
    h, w = L.shape
    centre = L[int(h * 0.35) : int(h * 0.65), int(w * 0.35) : int(w * 0.65)]
    c = float(np.median(centre))
    dark = L < c - 1.0
    if dark.mean() > 0.03:
        return dark, "holder", c - 1.0
    top = float(np.percentile(L, 99))
    # Halfway between the picture and the brightest film, in log units.
    level = c + 0.6 * (top - c)
    return L > level, "strip", level


def _walk(B: np.ndarray, side: str, span: tuple[float, float]) -> list[tuple[float, float]]:
    """Per band, the first non-barrier position walking from centre to ``side``.

    Returns ``(along, position)`` pairs in pixels: ``along`` is the band centre
    on the axis parallel to the side.
    """
    if side in ("top", "bottom"):
        B = B.T
    h, w = B.shape
    cx = w // 2
    a0, a1 = int(h * span[0]), int(h * span[1])
    edges = np.linspace(a0, a1, _BANDS + 1).astype(int)
    hits = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        if hi <= lo:
            continue
        frac = B[lo:hi].mean(axis=0)
        frac = np.convolve(frac, np.ones(5) / 5, mode="same")
        if side in ("left", "top"):
            idx = np.nonzero(frac[:cx][::-1] > _HIT)[0]
            if idx.size:
                hits.append(((lo + hi) / 2, float(cx - idx[0])))
        else:
            idx = np.nonzero(frac[cx:] > _HIT)[0]
            if idx.size:
                hits.append(((lo + hi) / 2, float(cx + idx[0] - 1)))
    return hits


def _fit(points: list[tuple[float, float]]) -> tuple[float, float, float, int] | None:
    """Trimmed least squares ``pos = a * along + b``: (a, b, rms, inliers)."""
    if len(points) < 4:
        return None
    p = np.array(points, np.float64)
    a, b = np.polyfit(p[:, 0], p[:, 1], 1)
    resid = np.abs(p[:, 1] - (a * p[:, 0] + b))
    keep = resid <= np.percentile(resid, 75)
    if keep.sum() >= 3:
        a, b = np.polyfit(p[keep, 0], p[keep, 1], 1)
    resid = np.abs(p[keep, 1] - (a * p[keep, 0] + b))
    return float(a), float(b), float(np.sqrt(np.mean(resid**2))), int(keep.sum())


def _skew(B: np.ndarray) -> tuple[float, list[float]]:
    """Straightening angle in degrees (counter-clockwise), and per-side RMS."""
    angles, rms = [], []
    for side in ("left", "right", "top", "bottom"):
        fit = _fit(_walk(B, side, (0.2, 0.8)))
        if fit is None or fit[3] < 8:
            continue
        a, _, r, _ = fit
        # A side's slope is tan(skew); the sign flips between vertical and
        # horizontal sides because y grows downward.
        theta = math.degrees(math.atan(a))
        angles.append(-theta if side in ("left", "right") else theta)
        rms.append(r)
    if not angles:
        return 0.0, rms
    return float(np.clip(np.median(angles), -_MAX_ANGLE, _MAX_ANGLE)), rms


def _base_region(
    L: np.ndarray, box: tuple[int, int, int, int], is_base, angle: float
) -> Region | None:
    """The largest patch of bare film base beside the frame.

    The rebate -- sprocket holes, edge print, mostly clear base -- runs along
    the frame's *long* sides on 35 mm and 120 alike, and that is where the
    holder's strip windows are. The neighbours sit on the short sides and can
    hold thin picture almost as bright as base, so they are not searched.
    Candidates are runs of rows (or columns) whose median ``is_base`` accepts:
    not holder-dark in holder mode, barrier-bright on a bare strip. A thin
    negative's picture can sit within 0.15 decades of its base (Roll001), so
    "brighter than the picture" is not a usable test. The brightest run wins. Medians shrug off holes and print.
    # lazy: a square frame (6x6) has no long side, so all four are searched
    # and the brightest run wins. Upgrade: find the film direction from where
    # the neighbours are.
    """
    h, w = L.shape
    x0, y0, x1, y1 = box
    cands = []

    def runs(values: np.ndarray, start: int) -> None:
        bright = is_base(values)
        i = 0
        while i < len(bright):
            if bright[i]:
                j = i
                while j < len(bright) and bright[j]:
                    j += 1
                cands.append((start + i, start + j, float(np.median(values[i:j]))))
                i = j
            else:
                i += 1

    pad_x = max(1, (x1 - x0) // 10)
    pad_y = max(1, (y1 - y0) // 10)
    found = []
    bw, bh = x1 - x0, y1 - y0
    if bw > bh * 1.1:
        sides = ("top", "bottom")
    elif bh > bw * 1.1:
        sides = ("left", "right")
    else:
        sides = ("top", "bottom", "left", "right")
    for side in sides:
        cands.clear()
        if side == "top" and y0 > 2:
            runs(np.median(L[:y0, x0 + pad_x : x1 - pad_x], axis=1), 0)
            found += [((x0 + pad_x, a, x1 - pad_x, b), m) for a, b, m in cands]
        elif side == "bottom" and y1 < h - 2:
            runs(np.median(L[y1:, x0 + pad_x : x1 - pad_x], axis=1), y1)
            found += [((x0 + pad_x, a, x1 - pad_x, b), m) for a, b, m in cands]
        elif side == "left" and x0 > 2:
            runs(np.median(L[y0 + pad_y : y1 - pad_y, :x0], axis=0), 0)
            found += [((a, y0 + pad_y, b, y1 - pad_y), m) for a, b, m in cands]
        elif side == "right" and x1 < w - 2:
            runs(np.median(L[y0 + pad_y : y1 - pad_y, x1:], axis=0), x1)
            found += [((a, y0 + pad_y, b, y1 - pad_y), m) for a, b, m in cands]

    min_area = 0.002 * h * w
    found = [(r, m) for r, m in found if (r[2] - r[0]) * (r[3] - r[1]) >= min_area]
    if not found:
        return None
    # Brightest, not largest: a strip window cut off by the edge of the capture
    # sits in the lens's fall-off and reads up to 45% low (Roll001 frames 48,
    # 56, 66). Bare light cannot win -- holes are a minority of every row on
    # the rebate side, and the row medians ignore them.
    (rx0, ry0, rx1, ry1), _ = max(found, key=lambda f: f[1])
    # Shrink away from the run's edges, where the transition blurs.
    sx, sy = (rx1 - rx0) * 0.2, (ry1 - ry0) * 0.2
    rx0, rx1, ry0, ry1 = rx0 + sx, rx1 - sx, ry0 + sy, ry1 - sy
    # The box was found on the straightened image; map the centre back to the
    # source, and shrink a little more to cover the rotation.
    cx, cy = (rx0 + rx1) / 2 - w / 2, (ry0 + ry1) / 2 - h / 2
    t = math.radians(angle)
    sx_, sy_ = cx * math.cos(t) - cy * math.sin(t), cx * math.sin(t) + cy * math.cos(t)
    rw, rh = (rx1 - rx0) * 0.9, (ry1 - ry0) * 0.9
    ox, oy = sx_ + w / 2 - rw / 2, sy_ + h / 2 - rh / 2
    return (ox / w, oy / h, rw / w, rh / h)


def detect(linear: np.ndarray, *, aspect: float = 1.5, margin: float = 0.01) -> Detection:
    """Locate the frame in ``linear`` -- an ``(H, W, 3)`` or ``(H, W)`` linear image."""
    L = _log_lum(linear)
    h, w = L.shape
    reasons: list[str] = []

    B, mode, level = _barrier(L)
    angle, rms = _skew(B)

    fill = float(np.percentile(L, 1)) if mode == "holder" else float(np.percentile(L, 99))
    Lr = _rotate(L, angle, fill)
    Br, _, _ = _barrier(Lr)

    box = []
    found = 0
    for side, limit in (("left", 0), ("top", 0), ("right", w - 1), ("bottom", h - 1)):
        hits = _walk(Br, side, (0.15, 0.85))
        if len(hits) >= _BANDS // 3:
            box.append(int(round(np.median([p for _, p in hits]))))
            found += 1
        else:
            box.append(limit)
            reasons.append(f"no edge found on the {side}")
    x0, y0, x1, y1 = box
    if x1 - x0 < 4 or y1 - y0 < 4:
        x0, y0, x1, y1 = 0, 0, w - 1, h - 1
        reasons.append("edges found do not enclose a frame")
        found = 0

    # Above the threshold is "not holder" in holder mode and "clear base" on a
    # bare strip -- the base either way, once the picture is excluded.
    base = _base_region(Lr, (x0, y0, x1 + 1, y1 + 1), lambda v: v > level, angle)
    if base is None:
        reasons.append("no clear film base found beside the frame")

    m = margin * min(h, w)
    crop = (
        max(0.0, (x0 + m) / w),
        max(0.0, (y0 + m) / h),
        min(1.0, (x1 + 1 - m) / w),
        min(1.0, (y1 + 1 - m) / h),
    )

    sides = (0.1, 0.25, 0.5, 0.75, 1.0)[found]
    fit = float(np.mean([max(0.0, 1 - r / 6) for r in rms])) if rms else 0.5
    bw, bh = (x1 - x0) * 1.0, (y1 - y0) * 1.0
    r = max(bw, bh) / max(1.0, min(bw, bh))
    shape = max(0.0, 1 - abs(math.log(r / aspect)) / 0.25)
    if shape < 0.5:
        reasons.append(f"frame shape {r:.2f}:1 is far from the expected {aspect:.2f}:1")
    inside = Lr[y0 : y1 + 1, x0 : x1 + 1]
    if mode == "holder":
        spread = float(np.percentile(inside, 95) - np.percentile(inside, 5))
    else:
        spread = float(np.percentile(Lr, 99) - np.median(inside))
    contrast = min(1.0, spread / 0.5)
    if contrast < 0.5:
        reasons.append("little contrast inside the frame -- blank or fogged?")
    confidence = float(sides * fit * shape * contrast)

    return Detection(crop=crop, angle=angle, base_region=base, confidence=confidence, reasons=reasons)
