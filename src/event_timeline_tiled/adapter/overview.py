"""Health, queue status and the window's runs: ``/health``, ``/queues``,
``/clips``, ``/clips/frame``, ``/clips/status``."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from .. import __version__
from ..clips import frame, lane_names
from ..run_state import Status
from ..settings import Settings
from ..tiled_http import HealthStatus
from ..timeline import SourceFetch, WindowResult
from .common import FROM, TO, check_window
from .state import Adapter

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


def router(adapter: Adapter) -> APIRouter:
    api = APIRouter()
    settings, timeline, clock = adapter.settings, adapter.timeline, adapter.clock

    @api.get("/health")
    def health() -> list[dict[str, Any]]:
        """One row per catalogue root, flat for Grafana tables: a live check plus
        what is known from the timeline's own fetches."""
        checked_at = clock()
        version = adapter.tiled.server_version()
        rows = []
        for state in timeline.sources():
            check = adapter.tiled.check_root(state.root)
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

    @api.get("/queues")
    def queue_status() -> list[dict[str, Any]]:
        """One row per configured RE Manager (plan §5.5), read now. Unreachable
        managers have ``reachable: false`` and no state, never "idle"."""
        return adapter.queues.rows()

    @api.get("/clips")
    def clips(
        t_from: FROM,
        t_to: TO,
        status: Annotated[str | None, Query(pattern=r"^[a-z]+(,[a-z]+)*$")] = None,
    ) -> dict[str, Any]:
        """Runs overlapping ``[from, to)``, one row per run (seconds); ``status``
        keeps only runs with one of these comma-separated statuses."""
        check_window(t_from, t_to)
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

    @api.get("/clips/frame")
    def clips_frame(
        t_from: FROM, t_to: TO, min_lanes: Annotated[int, Query(ge=0, le=20)] = 0
    ) -> list[dict[str, Any]]:
        """The timeline frame of plan §8 (milliseconds), lanes final, padded with
        empty lanes up to ``min_lanes``. Whether it is complete is ``/clips/status``."""
        check_window(t_from, t_to)
        return frame(timeline.window(t_from, t_to).clips, t_from, t_to, min_lanes)

    @api.get("/clips/status")
    def clips_status(t_from: FROM, t_to: TO) -> dict[str, Any]:
        """Is the window's data complete? ``issues`` lists every reason it is
        not (one ``complete`` row if it is), worst first."""
        check_window(t_from, t_to)
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

    return api
