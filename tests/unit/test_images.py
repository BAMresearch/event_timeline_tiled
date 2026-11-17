"""Rendering a 2D result as a PNG."""

import io

import numpy as np
import pytest
from PIL import Image

from event_timeline_tiled.images import LUT, Scale, reduce, render


def _pixels(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


def test_reduce_takes_block_means_of_finite_values() -> None:
    a = np.array([[1.0, 3.0, 5.0], [np.nan, np.inf, 7.0]])
    out = reduce(a, 2)  # factor 2: blocks of 2×2, the last column padded
    assert out.shape == (1, 2)
    assert out[0, 0] == pytest.approx(2.0) and out[0, 1] == pytest.approx(6.0)
    assert reduce(a, 3) is not a and reduce(a, 3).shape == (2, 3)


def test_log_scale_leaves_non_positive_values_uncoloured() -> None:
    a = np.array([[0.0, -1.0], [10.0, 1000.0]])
    r = render(a, Scale.LOG, 64)
    px = _pixels(r.png)
    assert (r.width, r.height, r.uncoloured) == (2, 2, 2)
    assert px[0, 0, 3] == 0 and px[0, 1, 3] == 0 and px[1, 0, 3] == 255
    assert tuple(px[1, 0, :3]) == tuple(LUT[0])  # the low end of the map
    assert tuple(px[1, 1, :3]) == tuple(LUT[255])  # the high end
    assert r.low is not None and r.high is not None and r.low < r.high


def test_linear_scale_colours_zero_and_negative_values() -> None:
    r = render(np.array([[0.0, -1.0], [np.nan, 2.0]]), Scale.LINEAR, 64)
    assert r.uncoloured == 1


def test_constant_and_empty_images() -> None:
    r = render(np.full((3, 3), 5.0), Scale.LOG, 64)
    assert r.uncoloured == 0 and tuple(_pixels(r.png)[0, 0, :3]) == tuple(LUT[128])
    blank = render(np.zeros((3, 3)), Scale.LOG, 64)
    assert blank.uncoloured == 9 and blank.low is None
    with pytest.raises(ValueError):
        render(np.zeros(4), Scale.LOG, 64)


def test_size_bounds_the_longer_side() -> None:
    r = render(np.random.default_rng(0).random((300, 100)) + 1, Scale.LOG, 64)
    assert (r.width, r.height) == (20, 60)  # factor 5
    assert Image.open(io.BytesIO(r.png)).size == (20, 60)
