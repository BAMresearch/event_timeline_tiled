"""One run: ``/runs/{uid}``, its streams, fields, views and data."""

from __future__ import annotations

import json
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from datetime import UTC, datetime, tzinfo
from typing import Any

from fastapi import APIRouter, HTTPException

from ..clips import make_clip
from ..lanes import arrange
from ..layout import RunRecord
from ..run_state import role_of, status_of
from ..tiled_http import SourceError
from ..views import TABLE_ROW_LIMIT, ResolvedView, ViewKind, label
from .common import (
    AXIS,
    MAX_POINTS,
    NOT_WRITTEN,
    ROOT,
    TZ,
    UID,
    VIEW,
    X_AXIS,
    json_value,
    ms,
    upstream,
    zone,
)
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
        self._data: OrderedDict[tuple[str, str, str, int, int | None, str],
                                tuple[float, dict[str, Any]]] = OrderedDict()  # fmt: skip

    def base_lane(self, run: RunRecord, now: float) -> str | None:
        """The run's first lane in the default preset, by base name (not a
        window's disambiguated name); ``None`` if that preset places it nowhere."""
        adapter = self.adapter
        clip = make_clip(run, adapter.timeline.activity_of(run, now), False, label(run))
        if clip is None:
            return None
        arranged = arrange(
            adapter.presets.get(adapter.presets.default), [clip], {(run.root, run.key): run}, now,
            lambda r: adapter.timeline.streams_of(r, now), adapter.settings.max_lanes,
            adapter.settings.max_placements,
        )  # fmt: skip
        return arranged.lanes[0].base_name if arranged.lanes else None

    def summary(self, run: RunRecord) -> dict[str, Any]:
        now = self.adapter.clock()
        act = self.adapter.timeline.activity_of(run, now)
        start, stop = run.start_time, run.stop_time
        reason = (run.stop or {}).get("reason")
        return {
            "uid": run.key,
            "start_uid": run.start_uid,
            "uid_mismatch": run.uid_mismatch,
            "root": run.root or "/",
            "lane": self.base_lane(run, now),
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
                if stream.has_scalar_fields:
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
        x: str = "time",
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
        key = (run.root, run.key, chosen.name, max_points, axis, x)
        now = time.monotonic()
        with self._lock:
            cached = self._data.get(key)
        if cached is not None and now - cached[0] < self.data_cache_s:
            return cached[1]
        try:
            body = self._read(run, chosen, max_points, axis, x)
        except SourceError as exc:
            raise upstream(exc) from None
        with self._lock:
            self._data[key] = (now, body)
            self._data.move_to_end(key)
            while len(self._data) > DATA_CACHE_ENTRIES:
                self._data.popitem(last=False)
        return body

    def _read(
        self, run: RunRecord, view: ResolvedView, max_points: int, axis: int | None, x: str
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
        return self._timeseries(run, view, max_points, axis, x)

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
        self,
        run: RunRecord,
        view: ResolvedView,
        max_points: int,
        axis: int | None = None,
        x: str = "time",
    ) -> dict[str, Any]:
        """Long rows (time, field, series, value) from one stream: at most
        ``max_points`` rows over the fields returned. Events are decimated to
        ``max_points // fields``: every k-th from the first, and always the last.
        With ``axis``, only the fields of that axis: 1 for the fields in the
        first field's units, 2 for all others.

        With ``x="elapsed"``, wide rows instead, one per event: ``elapsed_s``
        (seconds since the run's start document, else its first event) and one
        column per field, named as its series; the same decimation."""
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
        if x == "elapsed":
            return self._elapsed(run, view, stream, columns, indices, fields, wanted, axis,
                                 every, n, max_points)  # fmt: skip
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

    def _elapsed(
        self,
        run: RunRecord,
        view: ResolvedView,
        stream: str,
        columns: Mapping[str, list[Any]],
        indices: list[int],
        fields: list[str],
        wanted: list[str],
        axis: int | None,
        every: int,
        n: int,
        max_points: int,
    ) -> dict[str, Any]:
        """:meth:`_timeseries` with ``x="elapsed"``: wide rows."""
        series = series_names(view, fields)
        times = [columns["time"][i] for i in indices]
        finite = [t for t in times if isinstance(t, int | float) and math.isfinite(t)]
        start = run.start.get("time")
        t0 = start if isinstance(start, int | float) and math.isfinite(start) else None
        if t0 is None and finite:
            t0 = min(finite)
        non_finite = len(times) - len(finite)
        rows: list[dict[str, Any]] = []
        for i, t in zip(indices, times, strict=True):
            if t0 is None or not isinstance(t, int | float) or not math.isfinite(t):
                continue
            row: dict[str, Any] = {"elapsed_s": round(t - t0, 3)}
            for f in fields:
                value = json_value(columns[f][i])
                non_finite += value is None
                row[series[f]] = value
            rows.append(row)
        rows.sort(key=lambda r: r["elapsed_s"])
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
            "x": "elapsed",
            "fields": fields,
            "series": [series[f] for f in fields],
            "units": dict(view.units),
            "start": t0,
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


SUMMARY_FIRST = (
    "status",
    "reason",
    "label",
    "plan_name",
    "start",
    "stop",
    "duration_s",
    "last_event",
    "last_event_age_s",
    "possibly_stalled",
    "activity_error",
)
"""Summary rows shown first: the run's state, then its identity (``uid``, …)."""


SUMMARY_TIMES = frozenset({"start", "stop", "last_event"})
"""Summary keys holding epoch seconds; keys ending in ``_s`` hold durations."""


def summary_rows(summary: Mapping[str, Any], tz: tzinfo = UTC) -> list[dict[str, Any]]:
    """Key/value rows of :meth:`Runs.summary`: its state first, so a short
    panel shows it, then the other keys in their order. ``text`` is the value
    for reading: times in ``tz``, durations in units, yes/no."""
    keys = [k for k in SUMMARY_FIRST if k in summary]
    keys += [k for k in summary if k not in keys]
    rows = []
    for k in keys:
        value = json_value(summary[k])
        rows.append({"key": k, "value": value, "text": readable(k, value, tz)})
    return rows


def readable(key: str, value: Any, tz: tzinfo) -> str:
    """A summary value as text: see :func:`summary_rows`."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int | float) and key in SUMMARY_TIMES:
        return datetime.fromtimestamp(value, tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    if isinstance(value, int | float) and key.endswith("_s"):
        return duration_text(value)
    return value if isinstance(value, str) else json.dumps(value)


def duration_text(seconds: float) -> str:
    """``8.5 s``, ``12 min 3 s``, ``5 h 2 min``, ``3 d 4 h``."""
    sign, rest = ("-" if seconds < 0 else ""), abs(seconds)
    if rest < 60:
        return f"{sign}{rest:.3g} s"
    m, s = divmod(round(rest), 60)
    if m < 60:
        return f"{sign}{m} min {s} s"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{sign}{h} h {m} min"
    d, h = divmod(h, 24)
    return f"{sign}{d} d {h} h"


def router(adapter: Adapter, runs: Runs) -> APIRouter:
    api = APIRouter()

    @api.get("/runs/{uid}")
    def run_summary(uid: UID, root: ROOT = None, tz: TZ = "") -> dict[str, Any]:
        """``tz``: the time zone of the summary rows' ``text``; default UTC."""
        run = adapter.find(uid, root)
        summary = runs.summary(run)
        metadata, truncated = metadata_rows(run)
        return {
            "run": summary,
            "summary": summary_rows(summary, zone(tz)),
            "metadata": metadata,
            "metadata_truncated": truncated,
        }

    @api.get("/runs/{uid}/streams")
    def run_streams(uid: UID, root: ROOT = None) -> list[dict[str, Any]]:
        """Streams with event counts (from the stop document when closed) and the
        first and last event time (from the ``time`` column)."""
        return runs.streams(adapter.find(uid, root))

    @api.get("/runs/{uid}/streams/{stream}/fields")
    def stream_fields(uid: UID, stream: UID, root: ROOT = None) -> list[dict[str, Any]]:
        return runs.fields(adapter.find(uid, root), stream)

    @api.get("/runs/{uid}/views")
    def run_views(uid: UID, root: ROOT = None) -> dict[str, Any]:
        """Available views and the default (the first available one)."""
        return runs.views(adapter.find(uid, root))

    @api.get("/runs/{uid}/data")
    def run_data(
        uid: UID,
        root: ROOT = None,
        view: VIEW = None,
        kind: ViewKind | None = None,
        max_points: MAX_POINTS = 2000,
        axis: AXIS = None,
        x: X_AXIS = "time",
    ) -> dict[str, Any]:
        """Rows for one view: ``view`` by name, else the first available view of
        ``kind``, else the default view. ``axis`` (time series only): 1 for the
        fields in the first field's units, 2 for the others. ``x`` (time series
        only): ``elapsed`` for wide rows against seconds since the run's start."""
        if view and kind:
            raise HTTPException(422, "give view or kind, not both")
        return runs.data(adapter.find(uid, root), view, kind, max_points, axis, x)

    return api
