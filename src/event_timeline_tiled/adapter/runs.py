"""One run: ``/runs/{uid}``, its streams, fields, views and data."""

from __future__ import annotations

import json
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException

from ..clips import lane_key, lane_names
from ..layout import RunRecord
from ..run_state import role_of, status_of
from ..tiled_http import SourceError
from ..views import TABLE_ROW_LIMIT, ResolvedView, ViewKind, label
from .common import AXIS, MAX_POINTS, NOT_WRITTEN, UID, VIEW, json_value, ms, upstream
from .metadata import metadata_rows, plain
from .results import Results
from .state import Adapter

DATA_CACHE_S = 5.0
"""A view's rows are reused for this long (several panels read the same view)."""
DATA_CACHE_ENTRIES = 32


class Runs:
    """Reads of one run, with a short-lived cache of view rows."""

    def __init__(self, adapter: Adapter, results: Results, data_cache_s: float) -> None:
        self.adapter = adapter
        self.results = results
        self.data_cache_s = data_cache_s
        self._lock = threading.Lock()
        self._data: OrderedDict[tuple[str, str, str, int, int | None],
                                tuple[float, dict[str, Any]]] = OrderedDict()  # fmt: skip

    def summary(self, run: RunRecord) -> dict[str, Any]:
        act = self.adapter.timeline.activity_of(run, self.adapter.clock())
        key = lane_key(run)
        start, stop = run.start_time, run.stop_time
        reason = (run.stop or {}).get("reason")
        return {
            "uid": run.key,
            "start_uid": run.start_uid,
            "uid_mismatch": run.uid_mismatch,
            "root": run.root or "/",
            "lane": lane_names([key])[key],
            "role": role_of(run.start).value,
            "status": status_of(run.stop).value,
            "reason": reason if isinstance(reason, str) else None,
            "label": label(run),
            "plan_name": run.start.get("plan_name"),
            "start": start,
            "stop": stop,
            "duration_s": None if start is None or stop is None else round(stop - start, 3),
            "last_event": act.last_event,
            "last_event_age_s": act.age_s,
            "possibly_stalled": act.possibly_stalled,
            "activity_error": act.error,
        }

    def streams(self, run: RunRecord) -> list[dict[str, Any]]:
        layout = self.adapter.layout
        rows = []
        try:
            for stream in layout.streams(run):
                times: list[float] = []
                if stream.has_table:
                    raw = layout.event_columns(run, stream.name)["time"]
                    times = [t for t in raw if isinstance(t, int | float) and math.isfinite(t)]
                count = run.num_events(stream.name)
                rows.append(
                    {
                        "stream": stream.name,
                        "events": count if count is not None else len(times),
                        "events_source": "stop document" if count is not None else "table",
                        "first_event": min(times) if times else None,
                        "last_event": max(times) if times else None,
                        "fields": len(stream.data_keys),
                    }
                )
        except SourceError as exc:
            raise upstream(exc) from None
        return rows

    def fields(self, run: RunRecord, stream: str) -> list[dict[str, Any]]:
        try:
            streams = {s.name: s for s in self.adapter.layout.streams(run)}
        except SourceError as exc:
            raise upstream(exc) from None
        if stream not in streams:
            raise HTTPException(404, "no such stream in this run")
        info = streams[stream]
        return [
            {
                "field": name,
                "dtype": key.get("dtype"),
                "dtype_numpy": key.get("dtype_numpy"),
                "shape": json.dumps(plain(key.get("shape"))),
                "units": key.get("units"),
                "hinted": name in info.hints,
                "external": bool(key.get("external")),
            }
            for name, key in sorted(info.data_keys.items())
        ]

    def views(self, run: RunRecord) -> dict[str, Any]:
        views = self.adapter.views_of(run)
        default = next((v.name for v in views if v.available), None)
        return {
            "default": default,
            "views": [
                {
                    "name": v.name,
                    "title": v.title,
                    "kind": v.kind.value,
                    "streams": list(v.streams),
                    "fields": list(v.fields),
                    "units": dict(v.units),
                    "columns": [{"stream": s, "field": f, "units": u} for s, f, u in v.columns],
                    "available": v.available,
                    "reason": v.reason,
                    "default": v.name == default,
                }
                for v in views
            ],
        }

    def data(
        self,
        run: RunRecord,
        view: str | None,
        kind: ViewKind | None,
        max_points: int,
        axis: int | None = None,
    ) -> dict[str, Any]:
        views = self.adapter.views_of(run)
        if kind is not None:
            chosen = next((v for v in views if v.available and v.kind is kind), None)
            if chosen is None:
                reason = f"this run has no {kind.value} view"
                return {"view": None, "kind": kind.value, "rows": [], "truncated": False,
                        "reason": reason, "notice": reason}  # fmt: skip
        else:
            chosen = next((v for v in views if (v.name == view if view else v.available)), None)
        if chosen is None:
            raise HTTPException(404, "no such view for this run")
        if not chosen.available:
            return {"view": chosen.name, "kind": chosen.kind.value, "rows": [],
                    "truncated": False, "reason": chosen.reason,
                    "notice": f"{chosen.title}: {chosen.reason}"}  # fmt: skip
        key = (run.root, run.key, chosen.name, max_points, axis)
        now = time.monotonic()
        with self._lock:
            cached = self._data.get(key)
        if cached is not None and now - cached[0] < self.data_cache_s:
            return cached[1]
        try:
            body = self._read(run, chosen, max_points, axis)
        except SourceError as exc:
            raise upstream(exc) from None
        with self._lock:
            self._data[key] = (now, body)
            self._data.move_to_end(key)
            while len(self._data) > DATA_CACHE_ENTRIES:
                self._data.popitem(last=False)
        return body

    def _read(
        self, run: RunRecord, view: ResolvedView, max_points: int, axis: int | None
    ) -> dict[str, Any]:
        if view.kind is ViewKind.SUMMARY:
            rows, truncated = metadata_rows(run)
            notice = view.title + ("; long or deep values cut" if truncated else "")
            return {"view": view.name, "kind": view.kind.value, "rows": rows,
                    "truncated": truncated, "notice": notice}  # fmt: skip
        if view.kind is ViewKind.TABLE:
            return self._table(run, view)
        if view.kind is ViewKind.CURVE:
            curve = self.results.curve_rows(run, view, max_points)
            return {"view": view.name, "kind": view.kind.value, **curve,
                    "notice": f"{view.title}: {curve['description']}"}  # fmt: skip
        if view.kind is ViewKind.IMAGE:
            return {"view": view.name, "kind": view.kind.value, "rows": [], "truncated": False,
                    "notice": f"{view.title}: shown in Corrected data"}  # fmt: skip
        return self._timeseries(run, view, max_points, axis)

    def _table(self, run: RunRecord, view: ResolvedView) -> dict[str, Any]:
        """Long rows, stream by stream in view order, each stream with its own
        fields and units. Reading stops once a row beyond the limit exists."""
        rows: list[dict[str, Any]] = []
        truncated = False
        for name in view.streams:
            columns = [(f, u) for s, f, u in view.columns if s == name]
            if not columns:
                continue
            if len(rows) >= TABLE_ROW_LIMIT:
                events = run.num_events(name)
                if events == 0:
                    continue
                if events is not None:
                    truncated = True
                    break
            data = self.adapter.layout.event_columns(run, name, [f for f, _ in columns])
            for i, seq in enumerate(data["seq_num"]):
                for f, units in columns:
                    if len(rows) >= TABLE_ROW_LIMIT:
                        truncated = True
                        break
                    rows.append(
                        {
                            "stream": name,
                            "seq_num": seq,
                            "time": ms(data["time"][i]),
                            "field": f,
                            "value": json_value(data[f][i]),
                            "units": units,
                        }
                    )
                if truncated:
                    break
            if truncated:
                break
        notice = f"{view.title}: {len(rows)} rows"
        if not rows and run.stop is None:
            notice += NOT_WRITTEN
        if truncated:
            notice = f"{view.title}: only the first {TABLE_ROW_LIMIT} rows are shown"
        return {"view": view.name, "kind": view.kind.value, "rows": rows,
                "truncated": truncated, "row_limit": TABLE_ROW_LIMIT,
                "notice": notice}  # fmt: skip

    def _timeseries(
        self, run: RunRecord, view: ResolvedView, max_points: int, axis: int | None = None
    ) -> dict[str, Any]:
        """Long rows (time, field, series, value) from one stream: at most
        ``max_points`` rows over the fields returned. Events are decimated to
        ``max_points // fields``: every k-th from the first, and always the last.
        With ``axis``, only the fields of that axis: 1 for the fields in the
        first field's units, 2 for all others."""
        [stream] = view.streams
        wanted = axis_fields(view, axis)
        fields = wanted[:max_points]
        columns = self.adapter.layout.event_columns(run, stream, fields)
        n = len(columns["time"])
        budget = max_points // len(fields) if fields else 0
        indices: list[int]
        if n <= budget:
            every, indices = 1, list(range(n))
        elif budget <= 1:
            every, indices = n, [n - 1]
        else:
            every = math.ceil((n - 1) / (budget - 1))
            indices = [*range(0, n - 1, every), n - 1]
        non_finite = 0
        rows = []
        series = series_names(view, fields)
        for i in indices:
            t = columns["time"][i]
            if not isinstance(t, int | float) or not math.isfinite(t):
                non_finite += 1
                continue
            # Long format: Infinity's "timeseries" format turns it into one
            # series per field, with a real time field (no dashboard transformation).
            for f in fields:
                value = json_value(columns[f][i])
                non_finite += value is None
                rows.append({"time": round(t * 1000), "field": f, "series": series[f],
                             "value": value})  # fmt: skip
        # Infinity converts long to wide only for ascending times; event times
        # usually follow seq_num, but nothing guarantees it.
        rows.sort(key=lambda r: r["time"])
        fields_cut = len(fields) < len(wanted)
        notice = f"{view.title} of {stream}: {len(indices)} of {n} events"
        if fields_cut:
            notice += f", first {len(fields)} of {len(wanted)} fields"
        if n == 0 and run.stop is None:
            notice += NOT_WRITTEN
        return {
            "view": view.name,
            "kind": view.kind.value,
            "stream": stream,
            "axis": axis,
            "fields": fields,
            "units": dict(view.units),
            "rows": rows,
            "truncated": fields_cut,
            "decimation": {
                "method": "every k-th event, last event always kept",
                "every": every,
                "source_rows": n,
                "events": len(indices),
                "returned": len(rows),
                "max_points": max_points,
            },
            "non_finite": non_finite,
            "notice": notice,
        }


def axis_fields(view: ResolvedView, axis: int | None) -> list[str]:
    """A time series view's fields, or those of one axis: 1 for the fields in
    the first field's units, 2 for all others."""
    wanted = list(view.fields)
    left = [f for f in wanted if view.units.get(f) == view.units.get(wanted[0])] if wanted else []
    if axis is None:
        return wanted
    return left if axis == 1 else [f for f in wanted if f not in left]


def series_names(view: ResolvedView, fields: list[str]) -> dict[str, str]:
    """The legend's name: the field and its units, so each axis is readable."""
    return {f: f"{f} [{view.units[f]}]" if f in view.units else f for f in fields}


def summary_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"key": k, "value": json_value(v)} for k, v in summary.items()]


def router(adapter: Adapter, runs: Runs) -> APIRouter:
    api = APIRouter()

    @api.get("/runs/{uid}")
    def run_summary(uid: UID) -> dict[str, Any]:
        run = adapter.find(uid)
        summary = runs.summary(run)
        metadata, truncated = metadata_rows(run)
        return {
            "run": summary,
            "summary": summary_rows(summary),
            "metadata": metadata,
            "metadata_truncated": truncated,
        }

    @api.get("/runs/{uid}/streams")
    def run_streams(uid: UID) -> list[dict[str, Any]]:
        """Streams with event counts (from the stop document when closed) and the
        first and last event time (from the ``time`` column)."""
        return runs.streams(adapter.find(uid))

    @api.get("/runs/{uid}/streams/{stream}/fields")
    def stream_fields(uid: UID, stream: UID) -> list[dict[str, Any]]:
        return runs.fields(adapter.find(uid), stream)

    @api.get("/runs/{uid}/views")
    def run_views(uid: UID) -> dict[str, Any]:
        """Available views and the default (the first available one)."""
        return runs.views(adapter.find(uid))

    @api.get("/runs/{uid}/data")
    def run_data(
        uid: UID,
        view: VIEW = None,
        kind: ViewKind | None = None,
        max_points: MAX_POINTS = 2000,
        axis: AXIS = None,
    ) -> dict[str, Any]:
        """Rows for one view: ``view`` by name, else the first available view of
        ``kind``, else the default view. ``axis`` (time series only): 1 for the
        fields in the first field's units, 2 for the others."""
        if view and kind:
            raise HTTPException(422, "give view or kind, not both")
        return runs.data(adapter.find(uid), view, kind, max_points, axis)

    return api
