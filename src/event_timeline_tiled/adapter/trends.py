"""The trend: the selected run's time series over the runs like it,
``/runs/{uid}/trend``."""

from __future__ import annotations

import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter

from ..lanes import final_names
from ..layout import RunRecord
from ..run_state import role_of
from ..tiled_http import SourceError
from ..trend import Piece, trend_rows
from ..views import ResolvedView, ViewKind, resolve
from .common import AXIS, FROM, LANE, MAX_POINTS, PRESET, ROOT, TO, UID, check_window, json_value
from .runs import axis_fields, series_names
from .state import Adapter

TREND_MAX_RUNS = 200
"""Most runs in one trend: the latest ones in the window."""
TREND_CACHE_VALUES = 2_000_000
"""Values (times included) of finished runs kept in memory for trends. A run
with more is read for each request and never kept."""
TREND_CACHE_ENTRIES = 5000
"""Finished runs kept for trends at most (also bounds empty pieces)."""
TREND_WORKERS = 4
"""Runs read at the same time for a trend."""


class Trends:
    """Trends, with the events of finished runs kept until evicted (finished
    runs never change)."""

    def __init__(self, adapter: Adapter) -> None:
        self.adapter = adapter
        self._lock = threading.Lock()
        self._pieces: OrderedDict[tuple[str, ...], tuple[Piece, bool, int]] = OrderedDict()
        self._size = 0
        self._stripes = [threading.Lock() for _ in range(64)]

    def _piece(
        self, run: RunRecord, view: ResolvedView, fields: list[str]
    ) -> tuple[Piece, bool] | str:
        """This run's events of ``fields`` in ``view``'s stream, and whether
        some fields were missing (their values are null); or why there are none:
        ``"no view"`` or ``"unreadable"``. A finished run is read once: requests
        for it at the same time (the trend panel's queries) wait for that read."""
        if run.stop is None:
            return self._read_piece(run, view, fields)
        # Everything the piece depends on: which fields are kept depends on the
        # selected run's units of them (review 06, finding 1).
        units = [f"{f}\x00{view.units.get(f, '')}" for f in fields]
        key = (run.root, run.key, view.name, *view.streams, *units)
        with self._stripes[hash(key) % len(self._stripes)]:
            with self._lock:
                if (hit := self._pieces.get(key)) is not None:
                    self._pieces.move_to_end(key)
                    return hit[0], hit[1]
            result = self._read_piece(run, view, fields)
            if isinstance(result, str):
                return result
            size = max(1, len(result[0].time) * (len(fields) + 1))
            if size <= TREND_CACHE_VALUES:
                with self._lock:
                    self._pieces[key] = (*result, size)
                    self._size += size
                    while (
                        self._size > TREND_CACHE_VALUES or len(self._pieces) > TREND_CACHE_ENTRIES
                    ):
                        _, (_, _, old_size) = self._pieces.popitem(last=False)
                        self._size -= old_size
            return result

    def _read_piece(
        self, run: RunRecord, view: ResolvedView, fields: list[str]
    ) -> tuple[Piece, bool] | str:
        [stream] = view.streams
        layout = self.adapter.layout
        try:
            mine = next(
                (v for v in resolve(run, role_of(run.start), layout.streams(run))
                 if v.name == view.name and v.available and v.kind is ViewKind.TIMESERIES
                 and v.streams == view.streams),
                None,
            )  # fmt: skip
            if mine is None:
                return "no view"
            # A field in other units than the selected run's is left out, not mixed in.
            present = [f for f in fields if f in mine.fields
                       and mine.units.get(f) == view.units.get(f)]  # fmt: skip
            data = layout.event_columns(run, stream, present)
        except SourceError:
            return "unreadable"
        n = len(data["time"])
        values = {f: data[f] if f in present else [None] * n for f in fields}
        return Piece(run.key, data["time"], values), len(present) < len(fields)

    def trend(
        self,
        run: RunRecord,
        t_from: float,
        t_to: float,
        max_points: int,
        axis: int | None,
        preset: str | None = None,
        lane: str | None = None,
    ) -> dict[str, Any]:
        """The selected run's time series view over every run like it in the
        window: in the selected lane of the preset (else the run's first lane
        there), with the same role and ``plan_name``, and the same view of the
        same stream. One series per field; a gap between runs."""
        view = next(
            (v for v in self.adapter.views_of(run)
             if v.available and v.kind is ViewKind.TIMESERIES),
            None,
        )  # fmt: skip
        if view is None:
            reason = "this run has no timeseries view"
            return {"view": None, "kind": ViewKind.TIMESERIES.value, "rows": [],
                    "truncated": False, "reason": reason, "notice": reason}  # fmt: skip
        [stream] = view.streams
        wanted = axis_fields(view, axis)
        fields = wanted[: max_points // 3]
        result, arranged = self.adapter.arrange(self.adapter.preset(preset), t_from, t_to)
        in_lane = arranged.lane(lane) if lane else arranged.first_lane(run.root, run.key)
        names, _ = final_names(arranged.lanes)
        role, plan = role_of(run.start), run.start.get("plan_name")
        like = sorted(
            (c for c in (arranged.runs_in(in_lane.id) if in_lane else [])
             if c.role is role and result.records[(c.root, c.uid)].start.get("plan_name") == plan),
            key=lambda c: (c.start, c.uid),
        )  # fmt: skip
        runs_cut = len(like) > TREND_MAX_RUNS
        chosen = like[-TREND_MAX_RUNS:]
        records = [result.records[(c.root, c.uid)] for c in chosen]
        if fields and records:
            with ThreadPoolExecutor(TREND_WORKERS) as pool:
                outcomes = list(pool.map(lambda r: self._piece(r, view, fields), records))
            # Retried one at a time: some Tiled table stores (DuckDB in Tiled
            # 0.2.21) fail reads made at the same time.
            outcomes = [
                self._piece(r, view, fields) if o == "unreadable" else o
                for r, o in zip(records, outcomes, strict=True)
            ]
        else:
            outcomes = []
        pieces = [o[0] for o in outcomes if not isinstance(o, str)]
        incomplete = sum(1 for o in outcomes if not isinstance(o, str) and o[1])
        no_view = outcomes.count("no view")
        unreadable = outcomes.count("unreadable")
        trend = trend_rows(pieces, fields, series_names(view, fields), max_points, json_value)
        fields_cut = len(fields) < len(wanted)
        what = plan if isinstance(plan, str) else "unnamed"
        place = f" on {names[in_lane.id]}" if in_lane else ""
        notice = (f"{view.title} of {stream} over {trend.runs} {what} runs{place} in range: "
                  f"{trend.kept} of {trend.events} events")  # fmt: skip
        if in_lane is None:
            notice += ", no runs like it: " + (
                "that lane is not in this preset's range"
                if lane
                else "the run is in no lane of this preset in range"
            )
        elif not lane:
            notice += ", lane: the run's first in this preset"
        if runs_cut:
            notice += f", the latest {TREND_MAX_RUNS} of {len(like)} runs only"
        if result.truncated:
            notice += ", not every run in range was read (ETT_MAX_RUNS)"
        if no_view:
            notice += f", {no_view} without this view"
        if unreadable:
            notice += f", {unreadable} could not be read"
        if incomplete:
            notice += f", {incomplete} lacking some fields"
        if fields_cut:
            notice += f", first {len(fields)} of {len(wanted)} fields"
        return {
            "view": view.name,
            "kind": view.kind.value,
            "stream": stream,
            "axis": axis,
            "fields": fields,
            "units": dict(view.units),
            "plan_name": plan,
            "preset": arranged.preset,
            "lane": in_lane.id if in_lane else None,
            "lane_name": names[in_lane.id] if in_lane else None,
            "rows": trend.rows,
            "truncated": fields_cut or runs_cut or result.truncated,
            "runs": {
                "like": len(like),
                "read": len(records),
                "plotted": trend.runs,
                "without_view": no_view,
                "unreadable": unreadable,
                "lacking_fields": incomplete,
                "max_runs": TREND_MAX_RUNS,
            },
            "decimation": {
                "method": "every k-th event over all runs, last event always kept",
                "every": trend.every,
                "source_rows": trend.events,
                "events": trend.kept,
                "returned": len(trend.rows),
                "max_points": max_points,
            },
            "non_finite": trend.non_finite,
            "notice": notice,
        }


def router(adapter: Adapter, trends: Trends) -> APIRouter:
    api = APIRouter()

    @api.get("/runs/{uid}/trend")
    def run_trend(
        uid: UID,
        t_from: FROM,
        t_to: TO,
        max_points: MAX_POINTS = 2000,
        axis: AXIS = None,
        preset: PRESET = None,
        lane: LANE = None,
        root: ROOT = None,
    ) -> dict[str, Any]:
        """The run's time series view over the runs like it in ``[from, to)``."""
        check_window(t_from, t_to)
        run = adapter.find(uid, root)
        return trends.trend(run, t_from, t_to, max_points, axis, preset, lane)

    return api
