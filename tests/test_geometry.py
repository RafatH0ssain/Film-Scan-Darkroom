import numpy as np

from fsd.processing.geometry import crop, orient, straighten


def test_straighten_zero_is_identity():
    img = np.arange(4 * 5 * 3, dtype=np.uint16).reshape(4, 5, 3)
    np.testing.assert_array_equal(straighten(img, 0.0), img)


def test_straighten_round_trip():
    y, x = np.mgrid[0:128, 0:128]
    card = np.zeros((128, 128, 3), np.uint16)
    for c in range(3):
        card[..., c] = np.clip(x * 300 + y * 150 + c * 1000, 0, 65535).astype(np.uint16)

    rotated = straighten(straighten(card, 2.0), -2.0)

    y0, y1 = card.shape[0] // 4, 3 * card.shape[0] // 4
    x0, x1 = card.shape[1] // 4, 3 * card.shape[1] // 4
    a = card[y0:y1, x0:x1].astype(np.float64)
    b = rotated[y0:y1, x0:x1].astype(np.float64)
    assert np.abs(a - b).mean() < 0.01 * 65535


def test_crop_fractions():
    out = crop(np.zeros((100, 200, 3)), (0.1, 0.2, 0.6, 0.7))
    assert out.shape == (50, 100, 3)


def test_crop_degenerate_box_returns_1px():
    out = crop(np.zeros((10, 10, 3)), (0.5, 0.5, 0.5, 0.5))
    assert out.shape == (1, 1, 3)


def test_orient_rot_and_mirror():
    a = np.arange(6).reshape(2, 3)
    expected = np.array([[5, 2], [4, 1], [3, 0]])
    np.testing.assert_array_equal(orient(a, 1, True), expected)
