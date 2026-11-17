"""FastAPI application: the adapter's endpoints (plan §6.1).

Times are epoch seconds, except the Grafana time fields of ``/clips/frame``
and of data rows (milliseconds). Every endpoint validates its inputs and
states truncation. Responses never contain the Tiled credential or raw
upstream bodies. The endpoint contract is documented in
``docs/technical/endpoints.md``.
"""

from __future__ import annotations

import argparse
import json
import math
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Path, Query

from .. import __version__
from ..clips import frame, lane_key, lane_names
from ..layout import Layout, RunRecord
from ..queues import ApiFactory, QueueMonitor
from ..run_state import Status, role_of, status_of
from ..settings import Settings
from ..tiled_http import HealthStatus, SourceError, TiledReader
from ..timeline import SourceFetch, Timeline, WindowResult
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
DATA_CACHE_S = 5.0
"""A view's rows are reused for this long (several panels read the same view)."""
NO_SELECTION = "Select a run on the timeline or in Runs in range"
# A selection from a bookmark, a shared link or another Tiled: a message, not an error.
NOT_FOUND = "The selected run is not in Tiled (under the configured roots): select another"
NOT_WRITTEN = " (none in Tiled yet)"


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


def _plain(value: Any, depth: int = 0) -> Any:
    """``value`` as plain JSON data: non-finite numbers become ``None``,
    unknown types their ``str``, nesting beyond the limit ``"…"``."""
    if depth > METADATA_MAX_DEPTH:
        return "…"
    if isinstance(value, Mapping):
        return {str(k): _plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v, depth + 1) for v in value]
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
            text = json.dumps(_plain(value, depth), allow_nan=False, default=str)
            return self.add(document, prefix, text)
        if isinstance(value, list | tuple):
            parts = [v if isinstance(v, str) else json.dumps(_plain(v)) for v in value]
            return self.add(document, prefix, ", ".join(parts))
        return self.add(document, prefix, _plain(value))


def metadata_rows(run: RunRecord) -> tuple[list[dict[str, Any]], bool]:
    """The start and stop documents as ``(rows, truncated)``."""
    rows = _Rows()
    for document, content in (("start", run.start), ("stop", run.stop or {})):
        if not rows.flatten(document, "", content):
            break
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
        t_from: Annotated[float, Query(alias="from")], t_to: Annotated[float, Query(alias="to")]
    ) -> list[dict[str, Any]]:
        """The timeline frame of plan §8 (milliseconds), lanes final. Whether it
        is complete is ``/clips/status``."""
        _window(t_from, t_to)
        return frame(timeline.window(t_from, t_to).clips, t_from, t_to)

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

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the timeline adapter.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(Settings.from_env()), host=args.host, port=args.port)
