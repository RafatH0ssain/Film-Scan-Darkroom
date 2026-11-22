import time

import numpy as np
import pytest

from fsd.mock.synth import synth_scan
from fsd.processing.detect import detect
from fsd.processing.film import linear16_to_linear, sample_base


def _close(a, b, tol):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


@pytest.mark.parametrize("holder", [False, True])
@pytest.mark.parametrize("angle", [0.0, 0.8, -1.5, 3.0])
def test_detects_crop_and_angle(angle, holder):
    img, truth = synth_scan(angle=angle, holder=holder)
    d = detect(linear16_to_linear(img))
    assert abs(d.angle - truth.angle) <= 0.1, (d.angle, truth.angle)
    assert _close(d.crop, truth.crop, 0.005), (d.crop, truth.crop)
    assert d.confidence >= 0.8, (d.confidence, d.reasons)


@pytest.mark.parametrize("holder", [False, True])
def test_portrait_frame(holder):
    img, truth = synth_scan(portrait=True, holder=holder, angle=1.0)
    d = detect(linear16_to_linear(img))
    assert abs(d.angle - truth.angle) <= 0.1
    assert _close(d.crop, truth.crop, 0.005), (d.crop, truth.crop)
    x0, y0, x1, y1 = d.crop
    h, w = img.shape[:2]
    assert (y1 - y0) * h > (x1 - x0) * w


@pytest.mark.parametrize("holder", [False, True])
def test_base_region_samples_base(holder):
    img, truth = synth_scan(holder=holder, angle=0.8)
    lin = linear16_to_linear(img)
    d = detect(lin)
    assert d.base_region is not None
    got = sample_base(lin, region=d.base_region)
    for g, t in zip(got, truth.base):
        assert abs(g - t) <= 0.02 * t, (got, truth.base)


def test_crop_excludes_neighbours():
    img, truth = synth_scan()
    d = detect(linear16_to_linear(img))
    gap = 0.04  # synth's gap as a fraction of width
    assert d.crop[0] > truth.crop[0] - gap / 2
    assert d.crop[2] < truth.crop[2] + gap / 2


def test_blank_frame_low_confidence():
    img, _ = synth_scan(blank=True)
    d = detect(linear16_to_linear(img))
    assert d.confidence < 0.5
    assert d.reasons


def test_uniform_image_does_not_raise():
    d = detect(np.full((400, 600, 3), 0.5, np.float32))
    assert d.confidence < 0.5
    assert d.reasons


def test_wrong_aspect_lowers_confidence():
    img, _ = synth_scan(frame_aspect=1.0)
    lin = linear16_to_linear(img)
    assert detect(lin, aspect=1.5).confidence < detect(lin, aspect=1.0).confidence


def test_scale_invariant():
    img, truth = synth_scan(holder=True, angle=0.8)
    d = detect(linear16_to_linear(img) * 0.05)  # e.g. a PQ-linear HEIF sits near 0.02
    assert _close(d.crop, truth.crop, 0.005)


def test_detect_is_fast():
    img, _ = synth_scan(width=3000, height=2000, holder=True, angle=1.0)
    lin = linear16_to_linear(img)
    start = time.perf_counter()
    detect(lin)
    assert time.perf_counter() - start < 1.5


def test_frame_line_inside_window_is_cropped_off():
    img, truth = synth_scan(holder=True, window_slack=0.015, angle=0.5)
    d = detect(linear16_to_linear(img))
    assert _close(d.crop, truth.crop, 0.005), (d.crop, truth.crop)
