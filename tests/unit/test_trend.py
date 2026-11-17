"""Rows of a trend across runs: gaps between runs, the row bound, the last event."""

from __future__ import annotations

import math

import pytest

from event_timeline_tiled.trend import Piece, trend_rows

SERIES = {"v": "v [V]", "i": "i [A]"}


def piece(uid: str, start: float, n: int, step: float = 1.0) -> Piece:
    times = [start + k * step for k in range(n)]
    return Piece(uid, times, {"v": [float(k) for k in range(n)], "i": [0.5] * n})


def test_a_gap_between_runs_and_none_within() -> None:
    trend = trend_rows([piece("a", 100, 3), piece("b", 200, 2)], ["v"], SERIES, 2000)
    assert [r["time"] for r in trend.rows] == [100_000, 101_000, 102_000, 151_000, 200_000, 201_000]
    gaps = [r for r in trend.rows if r["value"] is None]
    assert gaps == [{"time": 151_000, "field": "v", "series": "v [V]", "value": None}]
    assert (trend.events, trend.kept, trend.runs, trend.every) == (5, 5, 2, 1)


def test_runs_back_to_back_within_a_millisecond_get_no_gap() -> None:
    trend = trend_rows([piece("a", 100, 1), piece("b", 100.0005, 1)], ["v"], SERIES, 2000)
    assert all(r["value"] is not None for r in trend.rows)


@pytest.mark.parametrize("runs", [1, 3, 50, 400])
@pytest.mark.parametrize("max_points", [10, 11, 100])
def test_rows_never_exceed_max_points_and_the_last_event_is_kept(
    runs: int, max_points: int
) -> None:
    pieces = [piece(f"r{k}", 1000.0 * k, 3) for k in range(runs)]
    trend = trend_rows(pieces, ["v", "i"], SERIES, max_points)
    assert len(trend.rows) <= max_points
    assert trend.rows[-1]["time"] == round(pieces[-1].time[-1] * 1000)
    times = [r["time"] for r in trend.rows]
    assert times == sorted(times)
    if runs * 3 * 2 + (runs - 1) * 2 <= max_points:
        assert trend.every == 1 and trend.kept == runs * 3


def test_events_without_a_time_are_dropped_and_counted() -> None:
    bad = Piece("a", [1.0, math.nan, None, 4.0], {"v": [1.0, 2.0, 3.0, math.inf]})
    trend = trend_rows([bad], ["v"], SERIES, 2000, lambda v: v if math.isfinite(v) else None)
    assert [r["time"] for r in trend.rows] == [1000, 4000]
    assert trend.non_finite == 3  # two times and one value


def test_nothing_to_plot() -> None:
    assert trend_rows([], ["v"], SERIES, 100).rows == []
    assert trend_rows([piece("a", 0, 3)], [], SERIES, 100).rows == []
