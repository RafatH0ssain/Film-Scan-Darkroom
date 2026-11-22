"""Source decoding: extension routing, native depth, and linearisation."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from fsd.processing.source import SourceError, decode
from fsd.processing.tiffio import write_tiff


@pytest.fixture
def jpeg(tmp_path):
    path = tmp_path / "frame.jpg"
    pixels = (np.arange(48 * 64 * 3) % 256).astype(np.uint8).reshape(48, 64, 3)
    Image.fromarray(pixels, "RGB").save(path, "JPEG")
    return path


def test_decode_jpeg_is_srgb8(jpeg):
    decoded = decode(jpeg)
    assert decoded.kind == "srgb8"
    assert decoded.pixels.shape == (48, 64, 3)
    assert decoded.pixels.dtype == np.uint8


def test_decode_half_halves_dimensions(jpeg):
    decoded = decode(jpeg, half=True)
    assert decoded.pixels.shape == (24, 32, 3)


def test_decode_16bit_tiff(tmp_path):
    pixels = (np.arange(48 * 64 * 3) % 4096).astype(np.uint16).reshape(48, 64, 3) * 16
    path = tmp_path / "frame.tif"
    write_tiff(path, pixels)

    decoded = decode(path)

    assert decoded.kind == "srgb16"
    assert decoded.pixels.dtype == np.uint16
    np.testing.assert_array_equal(decoded.pixels, pixels)


def test_decode_unknown_extension_raises(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("not an image")
    with pytest.raises(SourceError):
        decode(path)


def test_decode_missing_file_raises(tmp_path):
    with pytest.raises(SourceError):
        decode(tmp_path / "missing.jpg")


def test_corrupt_tiff_raises_source_error(tmp_path):
    path = tmp_path / "x.tif"
    path.write_bytes(b"not a tiff")
    with pytest.raises(SourceError):
        decode(path)


def test_corrupt_raw_raises_source_error(tmp_path):
    path = tmp_path / "x.CR3"
    path.write_bytes(b"junk")
    with pytest.raises(SourceError):
        decode(path)


@pytest.mark.parametrize("kind", ["srgb8", "srgb16"])
def test_linear_is_float32_in_0_1(kind, jpeg, tmp_path):
    if kind == "srgb8":
        decoded = decode(jpeg)
    else:
        pixels = (np.arange(48 * 64 * 3) % 4096).astype(np.uint16).reshape(48, 64, 3) * 16
        path = tmp_path / "frame16.tif"
        write_tiff(path, pixels)
        decoded = decode(path)

    linear = decoded.linear()

    assert linear.dtype == np.float32
    assert linear.shape == decoded.pixels.shape
    assert float(linear.min()) >= 0.0
    assert float(linear.max()) <= 1.0
