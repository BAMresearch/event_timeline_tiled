"""A 2D result rendered as a PNG (plan §6: rendered here from a typed binary
slice, not by Tiled, whose PNG is an 8-bit preview without log scale).

- Reduced to at most ``size`` pixels per side by block means of finite values.
- ``log`` scale: values that are zero, negative or not finite have no colour
  (transparent); ``linear``: only non-finite values.
- Colours span the 1st to the 99.5th percentile of the coloured values; a
  constant image is drawn in the middle colour.
- Row 0 of the array is the top row of the image.
"""

from __future__ import annotations

import io
import math
import warnings
from enum import StrEnum
from typing import Any

import numpy as np
from attrs import frozen
from PIL import Image

# Approximately matplotlib's viridis at 0, 1/8, …, 1 (perceptually uniform).
_VIRIDIS = np.array(
    [
        (68, 1, 84),
        (71, 44, 122),
        (59, 81, 139),
        (44, 113, 142),
        (33, 144, 141),
        (39, 173, 129),
        (92, 200, 99),
        (170, 220, 50),
        (253, 231, 37),
    ],
    dtype=float,
)
_STOPS = np.linspace(0.0, 1.0, len(_VIRIDIS))
LUT = (
    np.stack(
        [np.interp(np.linspace(0.0, 1.0, 256), _STOPS, _VIRIDIS[:, c]) for c in range(3)], axis=1
    )
    .round()
    .astype(np.uint8)
)
LOW_PERCENTILE, HIGH_PERCENTILE = 1.0, 99.5


class Scale(StrEnum):
    LOG = "log"
    LINEAR = "linear"


@frozen
class Rendered:
    png: bytes
    width: int
    height: int
    uncoloured: int
    """Pixels without colour (non-finite, or not positive on a log scale)."""
    low: float | None
    high: float | None
    """Values at the ends of the colour map (in the data's units)."""


def reduce(array: np.ndarray[Any, Any], size: int) -> np.ndarray[Any, Any]:
    """Block means of finite values, so that neither side exceeds ``size``."""
    h, w = array.shape
    factor = max(1, math.ceil(max(h, w) / size))
    if factor == 1:
        return array.astype(np.float64)
    ph, pw = -(-h // factor) * factor, -(-w // factor) * factor
    padded = np.full((ph, pw), np.nan)
    padded[:h, :w] = np.where(np.isfinite(array), array, np.nan)
    blocks = padded.reshape(ph // factor, factor, pw // factor, factor)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN blocks stay NaN
        out: np.ndarray[Any, Any] = np.nanmean(blocks, axis=(1, 3))
    return out


def render(array: np.ndarray[Any, Any], scale: Scale, size: int) -> Rendered:
    if array.ndim != 2 or 0 in array.shape:
        raise ValueError("an image needs a non-empty 2D array")
    data = reduce(array, size)
    values = data.copy()
    if scale is Scale.LOG:
        with np.errstate(divide="ignore", invalid="ignore"):
            values = np.where(values > 0, np.log10(values), np.nan)
    coloured = np.isfinite(values)
    rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
    low = high = None
    if coloured.any():
        lo, hi = np.percentile(values[coloured], [LOW_PERCENTILE, HIGH_PERCENTILE])
        if hi > lo:
            t = np.clip((values - lo) / (hi - lo), 0.0, 1.0)
        else:
            t = np.full(values.shape, 0.5)
        index = np.zeros(values.shape, dtype=np.intp)
        index[coloured] = np.round(t[coloured] * 255).astype(np.intp)
        rgba[coloured, :3] = LUT[index[coloured]]
        rgba[coloured, 3] = 255
        low, high = (float(10**lo), float(10**hi)) if scale is Scale.LOG else (float(lo), float(hi))
    buffer = io.BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG")
    return Rendered(
        buffer.getvalue(),
        width=values.shape[1],
        height=values.shape[0],
        uncoloured=int((~coloured).sum()),
        low=low,
        high=high,
    )
