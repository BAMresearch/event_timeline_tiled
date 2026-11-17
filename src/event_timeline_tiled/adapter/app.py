"""FastAPI application: the adapter's endpoints (plan §6.1).

Times are epoch seconds, except the Grafana time fields of ``/clips/frame``
and of data rows (milliseconds). Every endpoint validates its inputs and
states truncation. Responses never contain the Tiled credential or raw
upstream bodies. The endpoint contract is documented in
``docs/technical/endpoints.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Annotated, Any

import numpy as np
from fastapi import FastAPI, HTTPException, Path, Query, Request, Response

from .. import __version__
from ..clips import frame, lane_key, lane_names
from ..images import Scale, render
from ..layout import Layout, RunRecord
from ..queues import ApiFactory, QueueMonitor
from ..related import Family, default_result, families, family_of, problems, usable
from ..run_state import Role, Status, role_of, status_of
from ..settings import Settings
from ..tiled_http import HealthStatus, SourceError, TiledReader
from ..timeline import SourceFetch, Timeline, WindowResult
from ..trend import Piece, trend_rows
from ..views import TABLE_ROW_LIMIT, ResolvedView, ViewKind, label, resolve

MAX_WINDOW_S = 400 * 86400
"""Longest window accepted (a little over a year)."""
UID_PATTERN = r"[A-Za-z0-9_~.-]{1,200}"
UID = Annotated[str, Path(pattern=f"^{UID_PATTERN}$")]
SELECTED = Annotated[str, Query(pattern=f"^({UID_PATTERN})?$")]
"""A dashboard selection: a run key or stream name, or empty for none."""
VIEW = Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]*$")]
MAX_POINTS = Annotated[int, Query(ge=10, le=20_000)]
AXIS = Annotated[int | None, Query(ge=1, le=2)]

METADATA_MAX_ROWS = 2000
"""Metadata rows per run at most (start and stop together)."""
METADATA_MAX_DEPTH = 8
"""Nesting flattened into dotted keys; deeper values are shown as JSON text."""
METADATA_MAX_CHARS = 2000
"""Characters per metadata value at most; longer values end in "…"."""
METADATA_CUT = "Some values were cut: long ones end in …, deeply nested ones are …"
"""The value of the last metadata row when anything was cut."""
DATA_CACHE_S = 5.0
"""A view's rows are reused for this long (several panels read the same view)."""
NO_SELECTION = "Select a run on the timeline or in Runs in range"
# A selection from a bookmark, a shared link or another Tiled: a message, not an error.
NOT_FOUND = "The selected run is not in Tiled (under the configured roots): select another"
NOT_WRITTEN = " (none in Tiled yet)"
TREND_MAX_RUNS = 200
"""Most runs in one trend: the latest ones in the window."""
TREND_CACHE_VALUES = 2_000_000
"""Values (times included) of finished runs kept in memory for trends. A run
with more is read for each request and never kept."""
TREND_CACHE_ENTRIES = 5000
"""Finished runs kept for trends at most (also bounds empty pieces)."""
TREND_WORKERS = 4
"""Runs read at the same time for a trend."""
RELATED_MAX_RUNS = 500
"""Processing runs of one measurement listed at most (per catalogue root)."""
RELATED_MAX_PARENTS = 50
"""Measurements of one processing run looked up at most."""
RESULT_CACHE_ENTRIES = 32
"""Curves of finished processing runs kept in memory."""
IMAGE_CACHE_BYTES = 32 * 1024 * 1024
"""PNGs of finished processing runs kept in memory."""
IMAGE_SIZE = Annotated[int, Query(ge=64, le=2048)]
IMAGE_READ_SIDE = 2048
"""An image is read from Tiled strided to at most this many pixels per side
(about 4 million values, 32 MiB as float64), whatever its size there."""
IMAGE_RENDERS = 2
"""Images rendered at the same time at most (the others wait)."""


def _window(t_from: float, t_to: float) -> None:
    if not (math.isfinite(t_from) and math.isfinite(t_to)):
        raise HTTPException(422, "from and to must be finite epoch seconds")
    if not t_from < t_to:
        raise HTTPException(422, "from must be before to")
    if t_to - t_from > MAX_WINDOW_S:
        raise HTTPException(422, f"window longer than {MAX_WINDOW_S // 86400} days")


def _upstream(exc: SourceError) -> HTTPException:
    code = 404 if exc.status is HealthStatus.NOT_FOUND else 502
    return HTTPException(code, f"tiled {exc.status.value}: {exc.detail}")


def _json_value(value: Any) -> Any:
    """A JSON-safe scalar: non-finite numbers become ``None``."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _ms(seconds: Any) -> int | None:
    """Epoch seconds as integer milliseconds, ``None`` if not a finite number."""
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        return None
    return round(seconds * 1000) if math.isfinite(seconds) else None


# Metadata ------------------------------------------------------------------------------


def _plain(value: Any, depth: int = 0, cut: list[bool] | None = None) -> Any:
    """``value`` as plain JSON data: non-finite numbers become ``None``,
    unknown types their ``str``, nesting beyond the limit ``"…"`` (then
    ``cut[0]`` is set, if given)."""
    if depth > METADATA_MAX_DEPTH:
        if cut is not None:
            cut[0] = True
        return "…"
    if isinstance(value, Mapping):
        return {str(k): _plain(v, depth + 1, cut) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v, depth + 1, cut) for v in value]
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return _json_value(value)
    return str(value)


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | bool | int | float)


class _Rows:
    """Flattened metadata rows within the row and value-length limits."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.truncated = False

    def add(self, document: str, key: str, value: Any) -> bool:
        if len(self.rows) >= METADATA_MAX_ROWS:
            self.truncated = True
            return False
        if isinstance(value, str) and len(value) > METADATA_MAX_CHARS:
            value, self.truncated = value[: METADATA_MAX_CHARS - 1] + "…", True
        self.rows.append({"document": document, "key": key, "value": value})
        return True

    def flatten(self, document: str, prefix: str, value: Any, depth: int = 0) -> bool:
        """Mappings become dotted keys; lists of scalars are joined with ``, ``
        (strings as they are, other values as JSON); other lists and nesting
        beyond the limit are JSON text. Returns ``False`` once full."""
        if isinstance(value, Mapping) and depth < METADATA_MAX_DEPTH:
            for key in sorted(value, key=str):
                name = f"{prefix}.{key}" if prefix else str(key)
                if not self.flatten(document, name, value[key], depth + 1):
                    return False
            return True
        if isinstance(value, Mapping) or (
            isinstance(value, list | tuple) and not all(_scalar(v) for v in value)
        ):
            cut = [False]
            text = json.dumps(
                _plain(value, depth, cut), allow_nan=False, default=str, ensure_ascii=False
            )
            self.truncated = self.truncated or cut[0]
            return self.add(document, prefix, text)
        if isinstance(value, list | tuple):
            parts = [
                v if isinstance(v, str) else json.dumps(_plain(v), ensure_ascii=False)
                for v in value
            ]
            return self.add(document, prefix, ", ".join(parts))
        return self.add(document, prefix, _plain(value))


def metadata_rows(run: RunRecord) -> tuple[list[dict[str, Any]], bool]:
    """The start and stop documents as ``(rows, truncated)``."""
    rows = _Rows()
    for document, content in (("start", run.start), ("stop", run.stop or {})):
        if not rows.flatten(document, "", content):
            break
    if rows.truncated:  # shown as the table's last row
        rows.rows.append({"document": "", "key": "(cut)", "value": METADATA_CUT})
    return rows.rows, rows.truncated


# Completeness --------------------------------------------------------------------------

_SEVERITY = {"complete": 0, "partial": 1, "stale": 2, "incomplete": 3}


def _age(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def _issues(result: WindowResult, settings: Settings, now: float) -> list[tuple[str, str, str]]:
    """``(state, root, message)`` per problem with this window's data."""
    out: list[tuple[str, str, str]] = []
    for f in result.fetches:
        root = f.root or "/"
        if not f.ok and f.last_success is None:
            out.append(("incomplete", root, f"{root}: {f.error}, no data from this root yet"))
        elif not f.ok and f.last_success is not None:
            age = _age(now - f.last_success)
            out.append(("stale", root, f"{root}: {f.error}; runs as last seen, {age} ago"))
        if f.truncated:
            out.append(
                ("incomplete", root,
                 f"{root}: more than {settings.max_runs} runs; open runs first, "
                 "then the earliest written; narrow the time range")
            )  # fmt: skip
        if f.activity_errors:
            out.append(
                ("partial", root,
                 f"{root}: activity of {f.activity_errors} open run(s) unknown "
                 f"({f.activity_error})")
            )  # fmt: skip
    if result.unplaceable:
        n = len(result.unplaceable)
        out.append(("partial", "", f"{n} run(s) without a usable start time not drawn"))
    return out


def _fetch_row(f: SourceFetch, now: float) -> dict[str, Any]:
    return {
        "root": f.root or "/",
        "ok": f.ok,
        "error": f.error,
        "last_success": f.last_success,
        "data_age_s": None if f.last_success is None else round(now - f.last_success, 1),
        "truncated": f.truncated,
        "runs": f.runs,
        "activity_errors": f.activity_errors,
        "activity_error": f.activity_error,
    }


def create_app(
    settings: Settings,
    reader: TiledReader | None = None,
    clock: Callable[[], float] = time.time,
    queue_api: ApiFactory | None = None,
    data_cache_s: float = DATA_CACHE_S,
) -> FastAPI:
    tiled = reader or TiledReader(settings)
    layout = Layout(tiled)
    timeline = Timeline(settings, layout, clock)
    queues = (
        QueueMonitor(settings.queues, queue_api, clock)
        if queue_api
        else QueueMonitor(settings.queues, clock=clock)
    )
    data_cache: OrderedDict[tuple[str, str, str, int, int | None], tuple[float, dict[str, Any]]] = (
        OrderedDict()
    )
    data_lock = threading.Lock()
    # Finished runs never change: their trend pieces are kept until evicted.
    trend_cache: OrderedDict[tuple[str, ...], tuple[Piece, bool, int]] = OrderedDict()
    trend_size = [0]
    trend_stripes = [threading.Lock() for _ in range(64)]
    # Outputs of finished processing runs are final (processing-runs.md §1.2).
    result_lock = threading.Lock()
    curve_cache: OrderedDict[tuple[str, ...], dict[str, Any]] = OrderedDict()
    image_cache: OrderedDict[tuple[str, ...], bytes] = OrderedDict()
    renders = threading.BoundedSemaphore(IMAGE_RENDERS)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            queues.close()
            tiled.close()

    app = FastAPI(title="event-timeline-tiled adapter", version=__version__, lifespan=lifespan)

    def find(uid: str) -> RunRecord:
        try:
            run = layout.find_run(timeline.roots, uid)
        except SourceError as exc:
            raise _upstream(exc) from None
        if run is None:
            raise HTTPException(404, "no such run under the configured catalogue roots")
        return run

    @app.get("/health")
    def health() -> list[dict[str, Any]]:
        """One row per catalogue root, flat for Grafana tables: a live check plus
        what is known from the timeline's own fetches."""
        checked_at = clock()
        version = tiled.server_version()
        rows = []
        for state in timeline.sources():
            check = tiled.check_root(state.root)
            rows.append(
                {
                    "component": "tiled",
                    "root": check.root or "/",
                    "status": check.status.value,
                    "ok": check.status is HealthStatus.OK,
                    "latency_ms": check.latency_ms,
                    "detail": check.detail,
                    "last_success": state.last_success,
                    "last_attempt": state.last_attempt,
                    "data_age_s": None
                    if state.last_success is None
                    else round(checked_at - state.last_success, 1),
                    "last_error": state.error.value if state.error else None,
                    "last_error_detail": state.detail,
                    "tiled_version": version,
                    "adapter_version": __version__,
                    "checked_at": checked_at,
                }
            )
        return rows

    @app.get("/queues")
    def queue_status() -> list[dict[str, Any]]:
        """One row per configured RE Manager (plan §5.5), read now. Unreachable
        managers have ``reachable: false`` and no state, never "idle"."""
        return queues.rows()

    @app.get("/clips")
    def clips(
        t_from: Annotated[float, Query(alias="from")],
        t_to: Annotated[float, Query(alias="to")],
        status: Annotated[str | None, Query(pattern=r"^[a-z]+(,[a-z]+)*$")] = None,
    ) -> dict[str, Any]:
        """Runs overlapping ``[from, to)``, one row per run (seconds); ``status``
        keeps only runs with one of these comma-separated statuses."""
        _window(t_from, t_to)
        wanted = None if status is None else set(status.split(","))
        if wanted is not None and not wanted <= {s.value for s in Status}:
            raise HTTPException(422, f"status must be among {', '.join(s.value for s in Status)}")
        result = timeline.window(t_from, t_to)
        now = clock()
        names = lane_names([c.lane_key for c in result.clips])
        return {
            "from": t_from,
            "to": t_to,
            "clips": [
                c.as_row(names[c.lane_key])
                for c in sorted(result.clips, key=lambda c: (c.start, c.uid))
                if wanted is None or c.status.value in wanted
            ],
            "truncated": result.truncated,
            "unplaceable": result.unplaceable,
            "sources": [_fetch_row(f, now) for f in result.fetches],
        }

    @app.get("/clips/frame")
    def clips_frame(
        t_from: Annotated[float, Query(alias="from")],
        t_to: Annotated[float, Query(alias="to")],
        min_lanes: Annotated[int, Query(ge=0, le=20)] = 0,
    ) -> list[dict[str, Any]]:
        """The timeline frame of plan §8 (milliseconds), lanes final, padded with
        empty lanes up to ``min_lanes``. Whether it is complete is ``/clips/status``."""
        _window(t_from, t_to)
        return frame(timeline.window(t_from, t_to).clips, t_from, t_to, min_lanes)

    @app.get("/clips/status")
    def clips_status(
        t_from: Annotated[float, Query(alias="from")], t_to: Annotated[float, Query(alias="to")]
    ) -> dict[str, Any]:
        """Is the window's data complete? ``issues`` lists every reason it is
        not (one ``complete`` row if it is), worst first."""
        _window(t_from, t_to)
        result = timeline.window(t_from, t_to)
        now = clock()
        issues = sorted(_issues(result, settings, now), key=lambda i: -_SEVERITY[i[0]])
        state = issues[0][0] if issues else "complete"
        if not issues:
            roots = len(result.fetches)
            issues = [
                ("complete", "",
                 f"{len(result.clips)} runs from {roots} root(s), read now; open runs "
                 f"from up to {settings.open_run_horizon_days:g} days before the range end")
            ]  # fmt: skip
        return {
            "from": t_from,
            "to": t_to,
            "state": state,
            "complete": state == "complete",
            "runs": len(result.clips),
            "truncated": result.truncated,
            "unplaceable": result.unplaceable,
            "open_run_horizon_days": settings.open_run_horizon_days,
            "max_runs": settings.max_runs,
            "sources": [_fetch_row(f, now) for f in result.fetches],
            "issues": [{"state": s, "root": r, "message": m} for s, r, m in issues],
        }

    # One run -----------------------------------------------------------------------------

    def summary_of(run: RunRecord) -> dict[str, Any]:
        act = timeline.activity_of(run, clock())
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

    def summary_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [{"key": k, "value": _json_value(v)} for k, v in summary.items()]

    @app.get("/runs/{uid}")
    def run_summary(uid: UID) -> dict[str, Any]:
        run = find(uid)
        summary = summary_of(run)
        metadata, truncated = metadata_rows(run)
        return {
            "run": summary,
            "summary": summary_rows(summary),
            "metadata": metadata,
            "metadata_truncated": truncated,
        }

    def streams_of(run: RunRecord) -> list[dict[str, Any]]:
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
            raise _upstream(exc) from None
        return rows

    @app.get("/runs/{uid}/streams")
    def run_streams(uid: UID) -> list[dict[str, Any]]:
        """Streams with event counts (from the stop document when closed) and the
        first and last event time (from the ``time`` column)."""
        return streams_of(find(uid))

    def fields_of(run: RunRecord, stream: str) -> list[dict[str, Any]]:
        try:
            streams = {s.name: s for s in layout.streams(run)}
        except SourceError as exc:
            raise _upstream(exc) from None
        if stream not in streams:
            raise HTTPException(404, "no such stream in this run")
        info = streams[stream]
        return [
            {
                "field": name,
                "dtype": key.get("dtype"),
                "dtype_numpy": key.get("dtype_numpy"),
                "shape": json.dumps(_plain(key.get("shape"))),
                "units": key.get("units"),
                "hinted": name in info.hints,
                "external": bool(key.get("external")),
            }
            for name, key in sorted(info.data_keys.items())
        ]

    @app.get("/runs/{uid}/streams/{stream}/fields")
    def stream_fields(uid: UID, stream: UID) -> list[dict[str, Any]]:
        return fields_of(find(uid), stream)

    def views_of(run: RunRecord) -> list[ResolvedView]:
        try:
            return resolve(run, role_of(run.start), layout.streams(run))
        except SourceError as exc:
            raise _upstream(exc) from None

    @app.get("/runs/{uid}/views")
    def run_views(uid: UID) -> dict[str, Any]:
        """Available views and the default (the first available one)."""
        views = views_of(find(uid))
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

    def data_of(
        run: RunRecord,
        view: str | None,
        kind: ViewKind | None,
        max_points: int,
        axis: int | None = None,
    ) -> dict[str, Any]:
        views = views_of(run)
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
        with data_lock:
            cached = data_cache.get(key)
        if cached is not None and now - cached[0] < data_cache_s:
            return cached[1]
        try:
            if chosen.kind is ViewKind.SUMMARY:
                rows, truncated = metadata_rows(run)
                notice = chosen.title + ("; long or deep values cut" if truncated else "")
                body = {"view": chosen.name, "kind": chosen.kind.value, "rows": rows,
                        "truncated": truncated, "notice": notice}  # fmt: skip
            elif chosen.kind is ViewKind.TABLE:
                body = _table(run, chosen)
            elif chosen.kind is ViewKind.CURVE:
                curve = curve_rows(run, chosen, max_points)
                body = {"view": chosen.name, "kind": chosen.kind.value, **curve,
                        "notice": f"{chosen.title}: {curve['description']}"}  # fmt: skip
            elif chosen.kind is ViewKind.IMAGE:
                body = {"view": chosen.name, "kind": chosen.kind.value, "rows": [],
                        "truncated": False,
                        "notice": f"{chosen.title}: shown in Corrected data"}  # fmt: skip
            else:
                body = _timeseries(run, chosen, max_points, axis)
        except SourceError as exc:
            raise _upstream(exc) from None
        with data_lock:
            data_cache[key] = (now, body)
            data_cache.move_to_end(key)
            while len(data_cache) > 32:
                data_cache.popitem(last=False)
        return body

    @app.get("/runs/{uid}/data")
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
        return data_of(find(uid), view, kind, max_points, axis)

    def _table(run: RunRecord, view: ResolvedView) -> dict[str, Any]:
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
            data = layout.event_columns(run, name, [f for f, _ in columns])
            for i, seq in enumerate(data["seq_num"]):
                for f, units in columns:
                    if len(rows) >= TABLE_ROW_LIMIT:
                        truncated = True
                        break
                    rows.append(
                        {
                            "stream": name,
                            "seq_num": seq,
                            "time": _ms(data["time"][i]),
                            "field": f,
                            "value": _json_value(data[f][i]),
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
        run: RunRecord, view: ResolvedView, max_points: int, axis: int | None = None
    ) -> dict[str, Any]:
        """Long rows (time, field, series, value) from one stream: at most
        ``max_points`` rows over the fields returned. Events are decimated to
        ``max_points // fields``: every k-th from the first, and always the last.
        With ``axis``, only the fields of that axis: 1 for the fields in the
        first field's units, 2 for all others."""
        [stream] = view.streams
        wanted = list(view.fields)
        left = [f for f in wanted if wanted and view.units.get(f) == view.units.get(wanted[0])]
        if axis is not None:
            wanted = left if axis == 1 else [f for f in wanted if f not in left]
        fields = wanted[:max_points]
        columns = layout.event_columns(run, stream, fields)
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
        # The legend's name: the field and its units, so each axis is readable.
        series = {f: f"{f} [{view.units[f]}]" if f in view.units else f for f in fields}
        for i in indices:
            t = columns["time"][i]
            if not isinstance(t, int | float) or not math.isfinite(t):
                non_finite += 1
                continue
            # Long format: Infinity's "timeseries" format turns it into one
            # series per field, with a real time field (no dashboard transformation).
            for f in fields:
                value = _json_value(columns[f][i])
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

    # Trend: the selected run and the runs like it ----------------------------------------

    def _trend_piece(
        run: RunRecord, view: ResolvedView, fields: list[str]
    ) -> tuple[Piece, bool] | str:
        """This run's events of ``fields`` in ``view``'s stream, and whether
        some fields were missing (their values are null); or why there are none:
        ``"no view"`` or ``"unreadable"``. A finished run is read once: requests
        for it at the same time (the trend panel's queries) wait for that read."""
        if run.stop is None:
            return _read_trend_piece(run, view, fields)
        # Everything the piece depends on: which fields are kept depends on the
        # selected run's units of them (review 06, finding 1).
        units = [f"{f}\x00{view.units.get(f, '')}" for f in fields]
        key = (run.root, run.key, view.name, *view.streams, *units)
        with trend_stripes[hash(key) % len(trend_stripes)]:
            with data_lock:
                if (hit := trend_cache.get(key)) is not None:
                    trend_cache.move_to_end(key)
                    return hit[0], hit[1]
            result = _read_trend_piece(run, view, fields)
            if isinstance(result, str):
                return result
            size = max(1, len(result[0].time) * (len(fields) + 1))
            if size <= TREND_CACHE_VALUES:
                with data_lock:
                    trend_cache[key] = (*result, size)
                    trend_size[0] += size
                    while (
                        trend_size[0] > TREND_CACHE_VALUES or len(trend_cache) > TREND_CACHE_ENTRIES
                    ):
                        _, (_, _, old_size) = trend_cache.popitem(last=False)
                        trend_size[0] -= old_size
            return result

    def _read_trend_piece(
        run: RunRecord, view: ResolvedView, fields: list[str]
    ) -> tuple[Piece, bool] | str:
        [stream] = view.streams
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

    def trend_of(
        run: RunRecord, t_from: float, t_to: float, max_points: int, axis: int | None
    ) -> dict[str, Any]:
        """The selected run's time series view over every run like it in the
        window: same lane, role and ``plan_name``, and the same view of the same
        stream. One series per field; a gap between runs."""
        view = next(
            (v for v in views_of(run) if v.available and v.kind is ViewKind.TIMESERIES), None
        )
        if view is None:
            reason = "this run has no timeseries view"
            return {"view": None, "kind": ViewKind.TIMESERIES.value, "rows": [],
                    "truncated": False, "reason": reason, "notice": reason}  # fmt: skip
        [stream] = view.streams
        wanted = list(view.fields)
        left = (
            [f for f in wanted if view.units.get(f) == view.units.get(wanted[0])] if wanted else []
        )
        if axis is not None:
            wanted = left if axis == 1 else [f for f in wanted if f not in left]
        fields = wanted[: max_points // 3]
        result = timeline.window(t_from, t_to)
        key, role, plan = lane_key(run), role_of(run.start), run.start.get("plan_name")
        like = sorted(
            (c for c in result.clips
             if c.lane_key == key and c.role is role
             and result.records[(c.root, c.uid)].start.get("plan_name") == plan),
            key=lambda c: (c.start, c.uid),
        )  # fmt: skip
        runs_cut = len(like) > TREND_MAX_RUNS
        chosen = like[-TREND_MAX_RUNS:]
        records = [result.records[(c.root, c.uid)] for c in chosen]
        if fields and records:
            with ThreadPoolExecutor(TREND_WORKERS) as pool:
                outcomes = list(pool.map(lambda r: _trend_piece(r, view, fields), records))
            # Retried one at a time: some Tiled table stores (DuckDB in Tiled
            # 0.2.21) fail reads made at the same time.
            outcomes = [
                _trend_piece(r, view, fields) if o == "unreadable" else o
                for r, o in zip(records, outcomes, strict=True)
            ]
        else:
            outcomes = []
        pieces = [o[0] for o in outcomes if not isinstance(o, str)]
        incomplete = sum(1 for o in outcomes if not isinstance(o, str) and o[1])
        no_view = outcomes.count("no view")
        unreadable = outcomes.count("unreadable")
        series = {f: f"{f} [{view.units[f]}]" if f in view.units else f for f in fields}
        trend = trend_rows(pieces, fields, series, max_points, _json_value)
        fields_cut = len(fields) < len(wanted)
        what = plan if isinstance(plan, str) else "unnamed"
        notice = (f"{view.title} of {stream} over {trend.runs} {what} runs in range: "
                  f"{trend.kept} of {trend.events} events")  # fmt: skip
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

    @app.get("/runs/{uid}/trend")
    def run_trend(
        uid: UID,
        t_from: Annotated[float, Query(alias="from")],
        t_to: Annotated[float, Query(alias="to")],
        max_points: MAX_POINTS = 2000,
        axis: AXIS = None,
    ) -> dict[str, Any]:
        """The run's time series view over the runs like it in ``[from, to)``."""
        _window(t_from, t_to)
        return trend_of(find(uid), t_from, t_to, max_points, axis)

    # Processing results (processing-runs.md §1.2; plan §4, step 6) ----------------------

    def processing_of(run: RunRecord) -> tuple[list[RunRecord], bool]:
        """Processing runs of a measurement, over all roots, and whether the
        list may be incomplete (a search reached its cap)."""
        out: list[RunRecord] = []
        cut = False
        for root in timeline.roots:
            found, truncated = layout.derived_runs(root, run.start_uid or run.key, RELATED_MAX_RUNS)
            out += [r for r in found if role_of(r.start) is Role.PROCESSING]
            cut = cut or truncated
        return out, cut

    def related_row(
        relation: str, run: RunRecord | None, uid: str, family: Family | None = None
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "relation": relation, "uid": uid, "label": "(not in Tiled)", "family": None,
            "attempt": None, "status": None, "usable": None, "latest_attempt": False,
            "latest_usable": False, "mark": None, "start": None, "stop": None, "reason": None,
            "problems": None,
        }  # fmt: skip
        if run is None:
            return row
        attempt, reason = run.start.get("attempt"), (run.stop or {}).get("reason")
        processing = role_of(run.start) is Role.PROCESSING
        issues = problems(run) if processing else []
        return row | {
            "uid": run.key,
            "label": label(run),
            "family": family_of(run.start) if processing else None,
            "attempt": attempt if isinstance(attempt, int) and not isinstance(attempt, bool)
            else None,
            "status": status_of(run.stop).value,
            "usable": usable(run) if processing else None,
            "latest_attempt": family is not None and family.latest_attempt is run,
            "latest_usable": family is not None and family.latest_usable is run,
            "mark": _mark(family, run),
            "start": run.start_time,
            "stop": run.stop_time,
            "reason": reason if isinstance(reason, str) and reason else None,
            "problems": "; ".join(issues) or None,
        }  # fmt: skip

    def related_of(run: RunRecord) -> dict[str, Any]:
        """A measurement's processing runs, by family (the latest attempt and
        the latest usable result marked); or a processing run's measurements.
        If the search may be incomplete, nothing is marked latest."""
        try:
            if role_of(run.start) is Role.PROCESSING:
                listed = run.start.get("derived_from")
                parents = (
                    [p for p in listed if isinstance(p, str)] if isinstance(listed, list) else []
                )
                cut = len(parents) > RELATED_MAX_PARENTS
                parents = list(dict.fromkeys(parents[:RELATED_MAX_PARENTS]))
                found: dict[str, RunRecord] = {}
                for root in timeline.roots:
                    missing = [p for p in parents if p not in found]
                    for record in layout.runs_by_start_uid(root, missing):
                        found.setdefault(record.start_uid or record.key, record)
                rows = [related_row("measurement", found.get(p), p) for p in parents]
                absent = sum(1 for p in parents if p not in found)
                notice = f"Processing run of {len(parents)} measurement(s)" + (
                    f", {absent} not in Tiled" if absent else ""
                )
                if cut:
                    notice += f", the first {RELATED_MAX_PARENTS} listed"
                if issues := problems(run):
                    notice += f"; breaks the processing contract: {'; '.join(issues)}"
                return {"role": Role.PROCESSING.value, "rows": rows, "truncated": cut,
                        "default": None, "notice": notice}  # fmt: skip
            runs, cut = processing_of(run)
        except SourceError as exc:
            raise _upstream(exc) from None
        found_families = [] if cut else families(runs)
        if cut:  # list them unranked: the latest may be among those not found
            rows = [related_row("processing", r, r.key) for r in runs]
            notice = _cut_notice()
        else:
            rows = [related_row("processing", r, r.key, f) for f in found_families for r in f.runs]
            notice = _results_notice(runs, found_families, default_result(found_families))
        default = default_result(found_families)
        return {"role": Role.ACQUISITION.value, "rows": rows, "truncated": cut,
                "default": default.key if default else None, "notice": notice}  # fmt: skip

    def corrected(
        run: RunRecord, kind: ViewKind, view: str | None
    ) -> tuple[RunRecord, ResolvedView, bool] | str:
        """The run whose ``kind`` view to show, that view, and whether it was
        followed from a measurement to its latest usable result; or why
        there is none. ``view`` restricts the choice to a view of that name.
        A measurement is followed only if its processing runs were all found."""

        def pick(views: list[ResolvedView]) -> ResolvedView | None:
            return next((v for v in views if v.kind is kind and v.name == (view or v.name)
                         and v.available), None)  # fmt: skip

        chosen = pick(views_of(run))
        if chosen is not None:
            return run, chosen, False
        if role_of(run.start) is Role.PROCESSING:
            return f"this processing run has no {kind.value}" + (
                f" ({status_of(run.stop).value})" if run.stop is not None else " yet"
            )
        try:
            runs, cut = processing_of(run)
        except SourceError as exc:
            raise _upstream(exc) from None
        if cut:
            return _cut_notice()
        found_families = families(runs)
        target = default_result(found_families)
        if target is None:
            return _results_notice(runs, found_families, None)
        chosen = pick(views_of(target))
        if chosen is None:
            return f"the latest usable result, {_identity(target)}, has no {kind.value}"
        return target, chosen, True

    def _source(run: RunRecord, view: ResolvedView, followed: bool) -> tuple[dict[str, Any], str]:
        note = f"{view.title} of {_identity(run)}"
        if followed:
            note += ", the latest usable result of the selected measurement"
        elif role_of(run.start) is Role.PROCESSING and not usable(run):
            issues = problems(run)
            why = "; ".join(issues) if issues else status_of(run.stop).value
            note += f": not a usable result ({why})"
        source = {"uid": run.key, "label": label(run), "family": family_of(run.start),
                  "usable": usable(run), "followed": followed}  # fmt: skip
        return source, note

    def curve_rows(run: RunRecord, view: ResolvedView, max_points: int) -> dict[str, Any]:
        """Rows ``x, y, y_lower, y_upper`` for log–log axes: points with a
        non-finite or non-positive x or y are left out (and counted); the
        bounds are y ∓ the uncertainty, the lower one left out where it is
        not positive. Evenly decimated to ``max_points``."""
        final = run.stop is not None
        key = (run.root, run.key, view.name, str(max_points))
        if final:
            with result_lock:
                if key in curve_cache:
                    curve_cache.move_to_end(key)
                    return curve_cache[key]
        [stream] = view.streams
        names = view.fields
        arrays = []
        for name in names:
            info = layout.result_info(run, stream, name)
            if len(info.shape) != 1:
                raise SourceError(HealthStatus.ERROR, f"{stream}/{name} is not 1D")
            arrays.append(layout.result_array(run, stream, name, info))
        if len({a.shape for a in arrays}) != 1:
            raise SourceError(HealthStatus.ERROR, f"{stream}: fields of different lengths")
        x, y = arrays[0], arrays[1]
        sigma = arrays[2] if len(arrays) > 2 else None
        finite = np.isfinite(x) & np.isfinite(y)
        plotted = finite & (x > 0) & (y > 0)
        index = np.flatnonzero(plotted)
        every = max(1, math.ceil(len(index) / max_points))
        index = index[::every]
        rows = []
        lower_dropped = bad_sigma = 0
        with np.errstate(over="ignore", invalid="ignore"):
            for i in index:
                lower = upper = None
                if sigma is not None:
                    s_i = float(sigma[i])
                    if not math.isfinite(s_i) or s_i < 0:
                        bad_sigma += 1  # no bounds: an uncertainty is finite and ≥ 0
                    else:
                        hi, lo = float(y[i]) + s_i, float(y[i]) - s_i
                        upper = hi if math.isfinite(hi) else None
                        if math.isfinite(lo) and lo > 0:
                            lower = lo
                        else:
                            lower_dropped += 1
                rows.append(
                    {"x": float(x[i]), "y": float(y[i]), "y_lower": lower, "y_upper": upper}
                )
        units = dict(view.units)

        def named(f: str) -> str:
            return f"{f} [{units[f]}]" if f in units else f

        description = f"{named(names[1])} against {named(names[0])}"
        if sigma is not None:
            description += f", bounds ± {names[2]}"
        non_finite = int(len(x) - finite.sum())
        non_positive = int(finite.sum() - plotted.sum())
        if non_positive:
            description += f"; {non_positive} points not positive (not on log axes)"
        if non_finite:
            description += f"; {non_finite} points not finite"
        if lower_dropped:
            description += f"; lower bound left out at {lower_dropped} points"
        if bad_sigma:
            description += f"; no bounds at {bad_sigma} points (uncertainty negative or not finite)"
        if every > 1:
            description += f"; every {every}th point"
        body = {"x": names[0], "y": names[1], "uncertainty": names[2] if sigma is not None
                else None, "units": units, "rows": rows, "truncated": every > 1,
                "points": len(x), "plotted": len(rows), "decimation": every,
                "non_finite": non_finite, "non_positive": non_positive,
                "lower_bound_dropped": lower_dropped, "uncertainty_invalid": bad_sigma,
                "description": description}  # fmt: skip
        if final:
            with result_lock:
                curve_cache[key] = body
                while len(curve_cache) > RESULT_CACHE_ENTRIES:
                    curve_cache.popitem(last=False)
        return body

    def curve_of(run: RunRecord, view: str | None, max_points: int) -> dict[str, Any]:
        found = corrected(run, ViewKind.CURVE, view)
        if isinstance(found, str):
            notice = found[:1].upper() + found[1:]
            return {"view": None, "kind": ViewKind.CURVE.value, "source": None, "rows": [],
                    "truncated": False, "notice": notice}  # fmt: skip
        target, chosen, followed = found
        source, note = _source(target, chosen, followed)
        try:
            curve = curve_rows(target, chosen, max_points)
        except SourceError as exc:
            problem = f"{note}: could not be read ({exc.status.value}: {exc.detail})"
            return {"view": chosen.name, "kind": chosen.kind.value, "source": source,
                    "rows": [], "truncated": False, "notice": problem}  # fmt: skip
        return {"view": chosen.name, "kind": chosen.kind.value, "source": source, **curve,
                "notice": f"{note}: {curve['description']}"}  # fmt: skip

    @app.get("/runs/{uid}/related")
    def run_related(uid: UID) -> dict[str, Any]:
        """A measurement's processing runs, or a processing run's measurements."""
        return related_of(find(uid))

    @app.get("/runs/{uid}/curve")
    def run_curve(uid: UID, view: VIEW = None, max_points: MAX_POINTS = 2000) -> dict[str, Any]:
        """The run's curve view; for a measurement, that of its latest usable
        processing result."""
        return curve_of(find(uid), view, max_points)

    @app.get("/runs/{uid}/image")
    @app.get("/run-images/{uid}/image")
    def run_image(
        uid: UID,
        request: Request,
        view: VIEW = None,
        scale: Scale = Scale.LOG,
        size: IMAGE_SIZE = 768,
    ) -> Response:
        """The run's image view as a PNG; for a measurement, that of its latest
        usable processing result. ``/run-images/...`` is the same, for the
        proxy's signed-in image route (plan §6)."""
        found = corrected(find(uid), ViewKind.IMAGE, view)
        if isinstance(found, str):
            raise HTTPException(404, found)
        target, chosen, followed = found
        final = target.stop is not None
        key = (target.root, target.key, chosen.name, scale.value, str(size))
        etag = '"' + hashlib.sha256("\0".join(key).encode()).hexdigest()[:32] + '"'
        headers = {"Cache-Control": "private, no-store"}
        if final:
            # A measurement can get a newer result: revalidate; a result never changes.
            age = "no-cache" if followed else "max-age=86400"
            headers = {"Cache-Control": f"private, {age}", "ETag": etag}
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=headers)
        with result_lock:
            png = image_cache.get(key)
        if png is None:
            [stream] = chosen.streams
            [name] = chosen.fields
            with renders:
                try:
                    info = layout.result_info(target, stream, name)
                    if len(info.shape) != 2 or 0 in info.shape:
                        raise SourceError(HealthStatus.ERROR, f"{stream}/{name} is not 2D")
                    read_side = min(2 * size, IMAGE_READ_SIDE)
                    step = max(1, math.ceil(max(info.shape) / read_side))
                    array = layout.result_array(target, stream, name, info, step)
                except SourceError as exc:
                    raise _upstream(exc) from None
                png = render(array, scale, size).png
            if final:
                with result_lock:
                    image_cache[key] = png
                    while sum(map(len, image_cache.values())) > IMAGE_CACHE_BYTES:
                        image_cache.popitem(last=False)
        return Response(png, media_type="image/png", headers=headers)

    # The dashboard's selection -----------------------------------------------------------
    # The same reads as /runs/{uid}/..., with the run (and stream) as query
    # parameters that may be empty: nothing selected is a normal state of the
    # dashboard, not an error, and so is a selected run that this Tiled does
    # not have (a bookmark, a shared link, a run from another server).

    def selection(uid: str) -> RunRecord | None:
        try:
            return find(uid) if uid else None
        except HTTPException as exc:
            if exc.status_code == 404:
                return None
            raise

    @app.get("/selection/run")
    def selected_run(uid: SELECTED = "") -> dict[str, Any]:
        run = selection(uid)
        if run is None:
            message = NOT_FOUND if uid else NO_SELECTION
            return {"selected": bool(uid), "found": False,
                    "summary": [{"key": "run", "value": message}],
                    "metadata": [], "metadata_truncated": False}  # fmt: skip
        summary = summary_of(run)
        metadata, truncated = metadata_rows(run)
        return {"selected": True, "found": True, "run": summary,
                "summary": summary_rows(summary),
                "metadata": metadata, "metadata_truncated": truncated}  # fmt: skip

    @app.get("/selection/streams")
    def selected_streams(uid: SELECTED = "") -> list[dict[str, Any]]:
        run = selection(uid)
        return streams_of(run) if run is not None else []

    @app.get("/selection/fields")
    def selected_fields(uid: SELECTED = "", stream: SELECTED = "") -> list[dict[str, Any]]:
        run = selection(uid) if stream else None
        return fields_of(run, stream) if run is not None else []

    @app.get("/selection/data")
    def selected_data(
        uid: SELECTED = "",
        view: VIEW = None,
        kind: ViewKind | None = None,
        max_points: MAX_POINTS = 2000,
        axis: AXIS = None,
    ) -> dict[str, Any]:
        if view and kind:
            raise HTTPException(422, "give view or kind, not both")
        run = selection(uid)
        if run is None:
            notice = NOT_FOUND if uid else NO_SELECTION
            return {"view": None, "rows": [], "truncated": False, "notice": notice}
        return data_of(run, view, kind, max_points, axis)

    @app.get("/selection/trend")
    def selected_trend(
        t_from: Annotated[float, Query(alias="from")],
        t_to: Annotated[float, Query(alias="to")],
        uid: SELECTED = "",
        max_points: MAX_POINTS = 2000,
        axis: AXIS = None,
    ) -> dict[str, Any]:
        _window(t_from, t_to)
        run = selection(uid)
        if run is None:
            notice = NOT_FOUND if uid else NO_SELECTION
            return {"view": None, "rows": [], "truncated": False, "notice": notice}
        return trend_of(run, t_from, t_to, max_points, axis)

    @app.get("/selection/related")
    def selected_related(uid: SELECTED = "") -> dict[str, Any]:
        run = selection(uid)
        if run is None:
            notice = NOT_FOUND if uid else NO_SELECTION
            return {"role": None, "rows": [], "truncated": False, "default": None,
                    "notice": notice}  # fmt: skip
        return related_of(run)

    @app.get("/selection/curve")
    def selected_curve(
        uid: SELECTED = "", view: VIEW = None, max_points: MAX_POINTS = 2000
    ) -> dict[str, Any]:
        run = selection(uid)
        if run is None:
            notice = NOT_FOUND if uid else NO_SELECTION
            return {"view": None, "kind": ViewKind.CURVE.value, "source": None, "rows": [],
                    "truncated": False, "notice": notice}  # fmt: skip
        return curve_of(run, view, max_points)

    return app


def _identity(run: RunRecord) -> str:
    """A processing run as operators can tell it apart: label, family, UID."""
    return f"{label(run)} ({family_of(run.start)}, {run.key[:8]})"


def _cut_notice() -> str:
    return (f"More than {RELATED_MAX_RUNS} processing runs of this measurement: which is the "
            "latest is not known here; select a result in Related runs")  # fmt: skip


def _mark(family: Family | None, run: RunRecord) -> str | None:
    """``latest usable``, ``latest attempt``, both, or ``None``: for a table."""
    if family is None:
        return None
    marks = [m for m, r in (("latest usable", family.latest_usable),
                            ("latest attempt", family.latest_attempt)) if r is run]  # fmt: skip
    return " · ".join(marks) or None


def _results_notice(runs: list[RunRecord], found: list[Family], default: RunRecord | None) -> str:
    """One line on a measurement's processing results."""
    if not runs:
        return "No processing run of this measurement"
    open_runs = sum(1 for r in runs if r.stop is None)
    tail = f"; {open_runs} still open" if open_runs else ""
    if default is None:
        latest = max((f.latest_attempt for f in found if f.latest_attempt), default=None,
                      key=lambda r: (r.stop_time or 0.0, r.start_uid or ""))  # fmt: skip
        last = f"; latest attempt {status_of(latest.stop).value}" if latest else ""
        return f"No usable processing result of this measurement{last}{tail}"
    family = next(f for f in found if f.latest_usable is default)
    later = family.latest_attempt
    note = ""
    if later is not None and later is not default:
        note = f"; a later attempt {status_of(later.stop).value}"
    return f"Latest usable result: {_identity(default)}{note}{tail}"


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the timeline adapter.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(Settings.from_env()), host=args.host, port=args.port)
