"""A trend across runs: the events of several runs as one time series.

Which runs belong in a trend, and reading them, is the adapter's job; this
module only turns their events into long rows (time in ms, field, series,
value) within a row limit, with a gap row (null values) halfway between
consecutive runs, where Grafana breaks the line. Runs less than 2 ms apart
get no gap, and runs that overlap in time interleave (review 06).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from attrs import frozen


@frozen
class Piece:
    """One run's events: ``time`` (epoch seconds) and a value list per field,
    all of the same length, in event order."""

    uid: str
    time: Sequence[Any]
    values: Mapping[str, Sequence[Any]]


@frozen
class Trend:
    rows: list[dict[str, Any]]
    every: int
    """Every k-th event over all runs was kept (the last one always)."""
    events: int
    """Events over all runs."""
    kept: int
    """Events with a usable time whose rows were returned."""
    runs: int
    """Runs with at least one row returned."""
    non_finite: int
    """Events dropped for a missing or non-finite time, plus null values."""


def _finite(t: Any) -> bool:
    return isinstance(t, int | float) and not isinstance(t, bool) and math.isfinite(t)


def _pick(pieces: Sequence[Piece], every: int) -> list[tuple[int, int]]:
    """``(piece, event)`` of every ``every``-th event over all pieces, and
    always the last one."""
    events = [(p, i) for p, piece in enumerate(pieces) for i in range(len(piece.time))]
    kept = events[::every]
    if events and kept[-1] != events[-1]:
        kept.append(events[-1])
    return kept


def _row_count(kept: list[tuple[int, int]], fields: int) -> int:
    runs = len({p for p, _ in kept})
    return (len(kept) + max(0, runs - 1)) * fields


def trend_rows(
    pieces: Sequence[Piece],
    fields: Sequence[str],
    series: Mapping[str, str],
    max_points: int,
    convert: Callable[[Any], Any] = lambda v: v,
) -> Trend:
    """Long rows for ``fields`` over ``pieces`` (in the order given, usually by
    run start), at most ``max_points`` rows including the gap rows: one null
    value per field between two runs, halfway between them. ``convert`` makes
    a value JSON-safe (``None`` for non-finite numbers)."""
    events = sum(len(p.time) for p in pieces)
    if not fields or not events:
        return Trend([], 1, events, 0, 0, 0)
    width = len(fields)
    every = max(1, math.ceil(events * width / max_points))
    kept = _pick(pieces, every)
    while _row_count(kept, width) > max_points and every < events:
        every += max(1, every // 4)
        kept = _pick(pieces, every)
    if _row_count(kept, width) > max_points:
        kept = kept[-1:]
    rows: list[dict[str, Any]] = []
    non_finite = 0
    used = 0
    runs: set[int] = set()
    previous: tuple[int, int] | None = None  # (piece, time in ms) of the last row
    for p, i in kept:
        t = pieces[p].time[i]
        if not _finite(t):
            non_finite += 1
            continue
        ms = round(t * 1000)
        if previous is not None and previous[0] != p:
            gap = (previous[1] + ms) // 2
            if previous[1] < gap < ms:
                rows.extend(
                    {"time": gap, "field": f, "series": series[f], "value": None} for f in fields
                )
        for f in fields:
            value = convert(pieces[p].values[f][i])
            non_finite += value is None
            rows.append({"time": ms, "field": f, "series": series[f], "value": value})
        previous = (p, ms)
        used += 1
        runs.add(p)
    # Infinity turns long rows into series only for ascending times.
    rows.sort(key=lambda r: r["time"])
    return Trend(rows, every, events, used, len(runs), non_finite)
