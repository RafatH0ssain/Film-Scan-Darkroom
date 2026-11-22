"""Synthetic copy-stand scans with known answers, for testing frame detection.

Modelled on real captures from the EDSDK scanner (Roll001, an R7 over a
backlit holder). Two layouts:

- **holder** (``holder=True``, the real rig): an opaque black holder with a
  window for the frame, a window above and below over the sprocket strips, and
  a window at each side showing part of the neighbouring frame. The edge to
  find is the frame window's; the film base shows in the strip windows.
- **bare strip** (``holder=False``): the film alone on the light. Frames are
  separated by gaps of clear base, with a sprocket rebate along the top and
  bottom.

The whole film is rotated by ``-angle`` about the centre, so ``Truth.angle``
is what to pass to ``rotate()`` to straighten it. ``Truth.crop`` is the frame
box in the straightened image, already inset by the default 1% margin
(:data:`MARGIN`), because that is what :func:`fsd.processing.detect.detect`
returns.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

#: Must match the default ``margin`` of :func:`fsd.processing.detect.detect`.
MARGIN = 0.01

#: Bare light through a sprocket hole or an empty window: brighter than base.
_BARE = 0.95
#: An opaque holder still leaks a little.
_HOLDER = 0.0008


@dataclass(frozen=True)
class Truth:
    crop: tuple[float, float, float, float]
    angle: float
    base: tuple[float, float, float]


def _content(h: int, w: int, rng: np.random.Generator) -> np.ndarray:
    """Negative density, in decades: 0.3 (thin) to 1.8 (dense)."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    d = 0.6 + 0.5 * (x / w) + 0.3 * np.sin(y / h * 3.1)
    for _ in range(12):
        cy, cx = rng.uniform(0, h), rng.uniform(0, w)
        r = rng.uniform(0.04, 0.2) * min(h, w)
        d += rng.uniform(-0.4, 0.6) * np.exp(-((y - cy) ** 2 + (x - cx) ** 2) / (2 * r * r))
    d += rng.normal(0, 0.03, (h, w)).astype(np.float32)
    return np.clip(d, 0.3, 1.8)


def _rotate(img: np.ndarray, angle: float, fill: float) -> np.ndarray:
    if angle == 0:
        return img
    out = np.empty_like(img)
    for c in range(img.shape[2]):
        out[..., c] = np.asarray(
            Image.fromarray(img[..., c], "F").rotate(
                angle, resample=Image.BICUBIC, fillcolor=fill
            )
        )
    return out


def synth_scan(
    width: int = 1200,
    height: int = 800,
    *,
    frame_aspect: float = 1.5,
    angle: float = 0.0,
    base: tuple[float, float, float] = (0.80, 0.55, 0.35),
    portrait: bool = False,
    holder: bool = False,
    blank: bool = False,
    window_slack: float = 0.0,
    lip_shadow: float = 0.0,
    seed: int = 0,
) -> tuple[np.ndarray, Truth]:
    """A linear uint16 RGB ``(H, W, 3)`` scan, and the right answer for it.

    ``window_slack`` (holder only) widens the frame window past the frame's
    right edge by that fraction of the width, as on Roll001 frame 50.

    ``lip_shadow`` (holder only) shades the frame window's edges over that
    fraction of the short side; the truth crop excludes the shading.

    ``portrait`` lays the film strip vertically (the camera turned, or the
    holder rotated): drawn landscape, then transposed, so the result is
    ``(width, height, 3)``.
    """
    rng = np.random.default_rng(seed)
    H, W = height, width
    base_arr = np.array(base, np.float32)
    density = np.zeros((H, W), np.float32)  # 0 = clear base

    # Frame geometry: rebate strips 12% top and bottom, frame fills between.
    strip = int(round(0.12 * H))
    fy0, fy1 = strip, H - strip
    fw = int(round((fy1 - fy0) * frame_aspect))
    fx0 = (W - fw) // 2
    fx1 = fx0 + fw
    gap = int(round(0.04 * W))

    if not blank:
        density[fy0:fy1, fx0:fx1] = _content(fy1 - fy0, fw, rng)
    # Neighbours, cut off by the edges of the capture.
    for nx0 in (fx0 - gap - fw, fx1 + gap):
        a, b = max(nx0, 0), min(nx0 + fw, W)
        if b > a:
            density[fy0:fy1, a:b] = _content(fy1 - fy0, b - a, rng)

    img = base_arr * np.power(10.0, -density)[..., None]

    # Sprocket holes (bare light) and edge print (dense) in the rebate.
    period = max(W // 24, 8)
    hole_w, hole_h = int(period * 0.45), int(strip * 0.35)
    for x in range(period // 3, W - hole_w, period):
        for y in (int(strip * 0.15), H - int(strip * 0.15) - hole_h):
            img[y : y + hole_h, x : x + hole_w] = _BARE
    for k in range(4):
        x = fx0 + int(fw * (0.1 + 0.25 * k))
        y = int(strip * 0.62)
        img[y : y + int(strip * 0.25), x : x + int(fw * 0.08)] *= 0.1

    if holder:
        # Frame window sits just inside the frame; its edge is the crop.
        inset = int(round(0.01 * min(H, W)))
        wx0, wy0, wx1, wy1 = fx0 + inset, fy0 + inset, fx1 - inset, fy1 - inset
        if window_slack:
            # Window wider than the frame: the clear frame line shows inside.
            wx1 = fx1 + int(round(window_slack * W))
        bar = int(round(0.035 * W))
        mask = np.zeros((H, W), bool)
        mask[wy0:wy1, wx0:wx1] = True
        mask[int(0.01 * H) : fy0 - inset, wx0 + bar : wx1 - bar] = True  # top strip
        mask[fy1 + inset : H - int(0.01 * H), wx0 + bar : wx1 - bar] = True  # bottom
        mask[wy0:wy1, int(0.01 * W) : wx0 - bar] = True  # left neighbour
        mask[wy0:wy1, wx1 + bar : W - int(0.01 * W)] = True  # right neighbour
        img = np.where(mask[..., None], img, np.float32(_HOLDER))
        box = (wx0, wy0, min(wx1, fx1), wy1)
        if lip_shadow:
            # The holder's lip shades the window's edge: up to half a decade
            # darker at the edge, easing to nothing ``ramp`` px in (Roll001
            # frame 03 measured 0.5 decades over ~12 px at half size).
            ramp = max(1, int(round(lip_shadow * min(H, W))))
            yy, xx = np.mgrid[0:H, 0:W]
            d = np.minimum.reduce([xx - wx0, yy - wy0, wx1 - 1 - xx, wy1 - 1 - yy]).astype(np.float32)
            shade = np.where((d >= 0) & (d < ramp), 0.5 * (1 - d / ramp) ** 2, 0.0)
            img = img * np.power(10.0, -shade)[..., None].astype(np.float32)
            box = (box[0] + ramp, box[1] + ramp, box[2] - ramp, box[3] - ramp)
        fill = _HOLDER
    else:
        box = (fx0, fy0, fx1, fy1)
        fill = float(base_arr.mean())

    img = _rotate(img.astype(np.float32), -angle, fill)
    # A little sensor noise everywhere, as a real capture has.
    img = img + rng.normal(0, 0.0004, img.shape).astype(np.float32)
    out = (np.clip(img, 0, 1) * 65535).astype(np.uint16)

    m = MARGIN * min(H, W)
    x0, y0, x1, y1 = box
    crop = ((x0 + m) / W, (y0 + m) / H, (x1 - m) / W, (y1 - m) / H)
    if portrait:
        out = np.ascontiguousarray(out.transpose(1, 0, 2))
        crop = (crop[1], crop[0], crop[3], crop[2])
        # Transposing mirrors the rotation direction.
        angle = -angle
    return out, Truth(crop=crop, angle=angle, base=tuple(float(v) for v in base_arr))


def main(argv: list[str] | None = None) -> int:
    """Write an example roll: ``python -m fsd.mock.synth --write DIR``."""
    from fsd.processing.tiffio import write_tiff

    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--write", type=Path, required=True, help="roll folder to create")
    ap.add_argument("--frames", type=int, default=4)
    args = ap.parse_args(argv)
    roll = args.write
    roll.mkdir(parents=True, exist_ok=True)
    name = roll.name.replace(" ", "")
    for n in range(1, args.frames + 1):
        img, _ = synth_scan(holder=True, angle=0.6 * (n % 3 - 1), seed=n)
        write_tiff(roll / f"{name}_Frame{n:02d}.tif", img)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
