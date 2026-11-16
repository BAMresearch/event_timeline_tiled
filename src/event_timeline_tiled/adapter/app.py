"""FastAPI application: the adapter's endpoints (plan §6.1).

Times are epoch seconds, except the Grafana time fields of ``/clips/frame``
(milliseconds). Every endpoint validates its inputs and states truncation.
Responses never contain the Tiled credential or raw upstream bodies. The
endpoint contract is documented in ``docs/technical/endpoints.md``.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Path, Query

from .. import __version__
from ..clips import frame, lane_key, lane_names
from ..layout import Layout, RunRecord
from ..run_state import Status, activity, role_of, status_of
from ..settings import Settings
from ..tiled_http import HealthStatus, SourceError, TiledReader
from ..timeline import Timeline
from ..views import TABLE_ROW_LIMIT, ResolvedView, ViewKind, label, resolve, stall_after_s

MAX_WINDOW_S = 400 * 86400
"""Longest window accepted (a little over a year)."""
UID = Annotated[str, Path(pattern=r"^[A-Za-z0-9_~.-]{1,200}$")]
SECONDS = Annotated[float, Query(description="epoch seconds")]


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


def _flatten(prefix: str, value: Any, out: list[tuple[str, Any]]) -> None:
    if isinstance(value, Mapping):
        for key in sorted(value, key=str):
            _flatten(f"{prefix}.{key}" if prefix else str(key), value[key], out)
    elif isinstance(value, list | tuple) and any(isinstance(v, Mapping) for v in value):
        out.append((prefix, json.dumps(value, default=str)))
    elif isinstance(value, list | tuple):
        out.append((prefix, ", ".join(str(v) for v in value)))
    else:
        out.append((prefix, _json_value(value)))


def metadata_rows(run: RunRecord) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for document, content in (("start", run.start), ("stop", run.stop or {})):
        flat: list[tuple[str, Any]] = []
        _flatten("", content, flat)
        rows += [{"document": document, "key": k, "value": v} for k, v in flat]
    return rows


def create_app(
    settings: Settings,
    reader: TiledReader | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    tiled = reader or TiledReader(settings)
    layout = Layout(tiled)
    timeline = Timeline(settings, layout, clock)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
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

    @app.get("/clips")
    def clips(
        t_from: Annotated[float, Query(alias="from")], t_to: Annotated[float, Query(alias="to")]
    ) -> dict[str, Any]:
        """Runs overlapping ``[from, to)``, one row per run (seconds)."""
        _window(t_from, t_to)
        result = timeline.window(t_from, t_to)
        names = lane_names(sorted({c.lane_key for c in result.clips}, key=lambda k: k.as_text()))
        return {
            "from": t_from,
            "to": t_to,
            "clips": [
                c.as_row(names[c.lane_key])
                for c in sorted(result.clips, key=lambda c: (c.start, c.uid))
            ],
            "truncated": result.truncated,
            "unplaceable": result.unplaceable,
            "sources": [
                {
                    "root": s.root or "/",
                    "ok": s.error is None,
                    "error": s.error.value if s.error else None,
                    "last_success": s.last_success,
                }
                for s in result.sources
            ],
        }

    @app.get("/clips/frame")
    def clips_frame(
        t_from: Annotated[float, Query(alias="from")], t_to: Annotated[float, Query(alias="to")]
    ) -> list[dict[str, Any]]:
        """The timeline frame of plan §8 (milliseconds), lanes final."""
        _window(t_from, t_to)
        result = timeline.window(t_from, t_to)
        return frame(result.clips, t_from, t_to, result.open_until)

    @app.get("/runs/{uid}")
    def run_summary(uid: UID) -> dict[str, Any]:
        run = find(uid)
        status = status_of(run.stop)
        last = timeline.last_event(run, clock()) if status is Status.OPEN else None
        act = activity(status, run.start_time, last, clock(), stall_after_s(run))
        key = lane_key(run)
        start, stop = run.start_time, run.stop_time
        reason = (run.stop or {}).get("reason")
        summary = {
            "uid": run.key,
            "start_uid": run.start_uid,
            "uid_mismatch": run.uid_mismatch,
            "root": run.root or "/",
            "lane": lane_names([key])[key],
            "role": role_of(run.start).value,
            "status": status.value,
            "reason": reason if isinstance(reason, str) else None,
            "label": label(run),
            "plan_name": run.start.get("plan_name"),
            "start": start,
            "stop": stop,
            "duration_s": None if start is None or stop is None else round(stop - start, 3),
            "last_event": act.last_event,
            "last_event_age_s": act.age_s,
            "possibly_stalled": act.possibly_stalled,
        }
        return {
            "run": summary,
            "summary": [{"key": k, "value": _json_value(v)} for k, v in summary.items()],
            "metadata": metadata_rows(run),
        }

    @app.get("/runs/{uid}/streams")
    def run_streams(uid: UID) -> list[dict[str, Any]]:
        """Streams with event counts (from the stop document when closed) and the
        first and last event time (from the ``time`` column)."""
        run = find(uid)
        rows = []
        try:
            for stream in layout.streams(run):
                times: list[float] = []
                if stream.has_table:
                    raw = layout.event_columns(run, stream.name)["time"]
                    times = [t for t in raw if isinstance(t, int | float)]
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

    @app.get("/runs/{uid}/streams/{stream}/fields")
    def stream_fields(uid: UID, stream: UID) -> list[dict[str, Any]]:
        run = find(uid)
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
                "shape": json.dumps(key.get("shape")),
                "units": key.get("units"),
                "hinted": name in info.hints,
                "external": bool(key.get("external")),
            }
            for name, key in sorted(info.data_keys.items())
        ]

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
                    "available": v.available,
                    "reason": v.reason,
                    "default": v.name == default,
                }
                for v in views
            ],
        }

    @app.get("/runs/{uid}/data")
    def run_data(
        uid: UID,
        view: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]*$")] = None,
        max_points: Annotated[int, Query(ge=10, le=20_000)] = 2000,
    ) -> dict[str, Any]:
        """Rows for one view (the default if ``view`` is not given)."""
        run = find(uid)
        views = views_of(run)
        chosen = next((v for v in views if (v.name == view if view else v.available)), None)
        if chosen is None:
            raise HTTPException(404, "no such view for this run")
        if not chosen.available:
            return {"view": chosen.name, "kind": chosen.kind.value, "rows": [],
                    "truncated": False, "reason": chosen.reason}  # fmt: skip
        try:
            if chosen.kind is ViewKind.SUMMARY:
                rows = metadata_rows(run)
                return {"view": chosen.name, "kind": chosen.kind.value, "rows": rows,
                        "truncated": False}  # fmt: skip
            if chosen.kind is ViewKind.TABLE:
                return _table(run, chosen)
            return _timeseries(run, chosen, max_points)
        except SourceError as exc:
            raise _upstream(exc) from None

    def _table(run: RunRecord, view: ResolvedView) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        truncated = False
        streams = {s.name: s for s in layout.streams(run)}
        for name in view.streams:
            fields = [f for f in view.fields if f in streams[name].data_keys]
            columns = layout.event_columns(run, name, fields)
            for i, seq in enumerate(columns["seq_num"]):
                for f in fields:
                    if len(rows) >= TABLE_ROW_LIMIT:
                        truncated = True
                        break
                    rows.append(
                        {
                            "stream": name,
                            "seq_num": seq,
                            "time": _json_value(columns["time"][i]),
                            "field": f,
                            "value": _json_value(columns[f][i]),
                            "units": view.units.get(f),
                        }
                    )
        return {"view": view.name, "kind": view.kind.value, "rows": rows,
                "truncated": truncated, "row_limit": TABLE_ROW_LIMIT}  # fmt: skip

    def _timeseries(run: RunRecord, view: ResolvedView, max_points: int) -> dict[str, Any]:
        [stream] = view.streams
        columns = layout.event_columns(run, stream, view.fields)
        n = len(columns["time"])
        every = max(1, math.ceil(n / max_points))
        indices = list(range(0, n, every))
        if n and indices[-1] != n - 1:
            indices.append(n - 1)
        non_finite = 0
        rows = []
        for i in indices:
            t = columns["time"][i]
            if not isinstance(t, int | float) or not math.isfinite(t):
                non_finite += 1
                continue
            row: dict[str, Any] = {"time": round(t * 1000)}
            for f in view.fields:
                value = _json_value(columns[f][i])
                non_finite += value is None
                row[f] = value
            rows.append(row)
        return {
            "view": view.name,
            "kind": view.kind.value,
            "stream": stream,
            "fields": list(view.fields),
            "units": dict(view.units),
            "rows": rows,
            "truncated": False,
            "decimation": {
                "method": "every k-th event, last event always kept",
                "every": every,
                "source_rows": n,
                "returned": len(rows),
            },
            "non_finite": non_finite,
        }

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the timeline adapter.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(Settings.from_env()), host=args.host, port=args.port)
