"""The one render pipeline: decoded negative in, finished positive out."""

from __future__ import annotations

import numpy as np

from fsd.mock.synth import synth_scan
from fsd.processing.film import FilmParams
from fsd.processing.geometry import crop
from fsd.processing.render import FrameSettings, render
from fsd.processing.sharpen import SharpenParams
from fsd.processing.source import Decoded


def test_render_inverts():
    img, truth = synth_scan(width=600, height=400, angle=0.0, seed=1)
    decoded = Decoded("raw", img)
    settings = FrameSettings(crop=truth.crop, film=FilmParams(mode="bw"))

    out = render(decoded, settings)

    y_in = crop(img, truth.crop).astype(np.float64).mean(axis=2).ravel()
    y_out = out.astype(np.float64).mean(axis=2).ravel()
    corr = np.corrcoef(y_in, y_out)[0, 1]
    assert corr < -0.8, f"input/output luminance correlation is {corr:.3f}"


def test_render_output_shape_follows_crop_and_rotate90():
    img, truth = synth_scan(width=480, height=320, angle=0.0, seed=2)
    ch, cw = crop(img, truth.crop).shape[:2]

    plain = render(Decoded("raw", img), FrameSettings(crop=truth.crop))
    assert plain.shape == (ch, cw, 3)
    assert plain.dtype == np.uint16

    turned = render(Decoded("raw", img), FrameSettings(crop=truth.crop, rotate90=1))
    assert turned.shape == (cw, ch, 3)
    assert turned.dtype == np.uint16

    mirrored = render(
        Decoded("raw", img), FrameSettings(crop=truth.crop, rotate90=1, mirror=True)
    )
    assert mirrored.shape == turned.shape


def test_render_half_scale_matches_full():
    img, _ = synth_scan(width=640, height=480, angle=0.0, seed=3)
    half = img[::2, ::2]
    box = (0.15, 0.15, 0.85, 0.85)
    settings = FrameSettings(crop=box, sharpen=SharpenParams(amount=0.4, radius=2.0))

    full = render(Decoded("raw", img), settings, scale=1.0)
    small = render(Decoded("raw", half), settings, scale=0.5)

    full_down = full[::2, ::2]
    h, w = min(full_down.shape[0], small.shape[0]), min(full_down.shape[1], small.shape[1])
    diff = (
        np.abs(
            full_down[:h, :w].astype(np.int32) - small[:h, :w].astype(np.int32)
        ).mean()
    )
    assert diff < 0.02 * 65535, f"mean abs diff is {diff:.1f} counts"


def test_render_blank_frame_no_nan():
    img, truth = synth_scan(width=400, height=300, blank=True, seed=4)
    with np.errstate(all="raise"):
        out = render(Decoded("raw", img), FrameSettings(crop=truth.crop))

    assert not np.isnan(out).any()
    assert out.dtype == np.uint16


def test_render_bw_is_neutral():
    img, truth = synth_scan(width=400, height=300, seed=5)
    out = render(
        Decoded("raw", img),
        FrameSettings(crop=truth.crop, film=FilmParams(mode="bw")),
    )
    assert np.array_equal(out[..., 0], out[..., 1])
    assert np.array_equal(out[..., 1], out[..., 2])


def _lit_unevenly(img: np.ndarray, centre_gain: float) -> np.ndarray:
    """A backlight brighter in the middle than at the edges, as on the copy stand."""
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2)) / np.sqrt(2)
    gain = centre_gain - (centre_gain - 1.0) * r**2
    return np.clip(img * gain[..., None], 0, 65535).astype(np.uint16)


def test_uneven_backlight_does_not_crush_shadows():
    # Roll001 frame 50: the backlight is brighter in the middle than at the
    # sprocket strip the base is sampled from, so the thinnest negative read
    # "clearer than clear film" and went to pure black (12% of the frame).
    h, w = 200, 300
    lin = np.zeros((h, w, 3), np.float32)
    lin[:30] = 0.5  # rebate strip at the edge: clear film, dimly lit
    ramp = np.linspace(0.02, 0.6, w, dtype=np.float32)  # picture, up to 1.2x the strip
    lin[40:] = ramp[None, :, None]
    img = (lin * 65535).astype(np.uint16)
    out = render(
        Decoded("raw", img),
        FrameSettings(crop=(0.0, 0.2, 1.0, 1.0), base_region=(0.1, 0.02, 0.8, 0.1)),
    )
    assert (out == 0).mean() < 0.005


def test_highlights_roll_off_instead_of_clipping():
    # Auto exposure lifts the midtones; whatever goes past white must roll off,
    # not pile up at 65535 (Roll001 frame 50 had 26% pure white).
    img, truth = synth_scan(holder=True, seed=5)
    out = render(Decoded("raw", img), FrameSettings(crop=truth.crop))
    assert (out == 65535).mean() < 0.005
