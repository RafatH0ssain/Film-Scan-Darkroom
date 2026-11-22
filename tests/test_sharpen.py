import numpy as np

from fsd.processing.sharpen import SharpenParams, sharpen


def test_sharpen_off_is_identity():
    img = np.arange(6 * 8 * 3, dtype=np.uint16).reshape(6, 8, 3)
    np.testing.assert_array_equal(sharpen(img, SharpenParams(amount=0.0)), img)


def test_sharpen_increases_edge_contrast():
    h, w = 32, 64
    img = np.zeros((h, w, 3), np.uint16)
    img[:, : w // 2] = 10000
    img[:, w // 2 :] = 40000
    out = sharpen(img, SharpenParams(amount=1.0))
    assert out.max() - out.min() > img.max() - img.min()


def test_sharpen_threshold_leaves_flat_noise():
    rng = np.random.default_rng(1)
    base = np.full((32, 32, 3), 30000, np.int64)
    noise = rng.integers(-50, 51, size=base.shape)
    img = np.clip(base + noise, 0, 65535).astype(np.uint16)
    out = sharpen(img, SharpenParams(amount=2.0, threshold=0.05))
    assert np.abs(out.astype(np.int64) - img.astype(np.int64)).max() <= 10


def test_sharpen_keeps_dtype_and_clips():
    img = np.full((16, 16, 3), 65000, np.uint16)
    out = sharpen(img, SharpenParams(amount=3.0))
    assert out.dtype == np.uint16
    assert out.shape == img.shape
    assert out.max() <= 65535
