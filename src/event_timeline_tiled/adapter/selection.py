"""The dashboard's selection: ``/selection/...``.

The same reads as ``/runs/{uid}/...``, with the run (and stream) as query
parameters that may be empty: nothing selected is a normal state of the
dashboard, not an error, and so is a selected run that this Tiled does not
have (a bookmark, a shared link, a run from another server).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..views import ViewKind
from .common import AXIS, FROM, MAX_POINTS, SELECTED, TO, VIEW, check_window, unselected
from .metadata import metadata_rows
from .results import Results, no_curve
from .runs import Runs, summary_rows
from .state import Adapter
from .trends import Trends


def router(adapter: Adapter, runs: Runs, trends: Trends, results: Results) -> APIRouter:
    api = APIRouter(prefix="/selection")

    @api.get("/run")
    def selected_run(uid: SELECTED = "") -> dict[str, Any]:
        run = adapter.selection(uid)
        if run is None:
            return {"selected": bool(uid), "found": False,
                    "summary": [{"key": "run", "value": unselected(uid)}],
                    "metadata": [], "metadata_truncated": False}  # fmt: skip
        summary = runs.summary(run)
        metadata, truncated = metadata_rows(run)
        return {"selected": True, "found": True, "run": summary,
                "summary": summary_rows(summary),
                "metadata": metadata, "metadata_truncated": truncated}  # fmt: skip

    @api.get("/streams")
    def selected_streams(uid: SELECTED = "") -> list[dict[str, Any]]:
        run = adapter.selection(uid)
        return runs.streams(run) if run is not None else []

    @api.get("/fields")
    def selected_fields(uid: SELECTED = "", stream: SELECTED = "") -> list[dict[str, Any]]:
        run = adapter.selection(uid) if stream else None
        return runs.fields(run, stream) if run is not None else []

    @api.get("/data")
    def selected_data(
        uid: SELECTED = "",
        view: VIEW = None,
        kind: ViewKind | None = None,
        max_points: MAX_POINTS = 2000,
        axis: AXIS = None,
    ) -> dict[str, Any]:
        if view and kind:
            raise HTTPException(422, "give view or kind, not both")
        run = adapter.selection(uid)
        if run is None:
            return {"view": None, "rows": [], "truncated": False, "notice": unselected(uid)}
        return runs.data(run, view, kind, max_points, axis)

    @api.get("/trend")
    def selected_trend(
        t_from: FROM,
        t_to: TO,
        uid: SELECTED = "",
        max_points: MAX_POINTS = 2000,
        axis: AXIS = None,
    ) -> dict[str, Any]:
        check_window(t_from, t_to)
        run = adapter.selection(uid)
        if run is None:
            return {"view": None, "rows": [], "truncated": False, "notice": unselected(uid)}
        return trends.trend(run, t_from, t_to, max_points, axis)

    @api.get("/related")
    def selected_related(uid: SELECTED = "") -> dict[str, Any]:
        run = adapter.selection(uid)
        if run is None:
            return {"role": None, "rows": [], "truncated": False, "default": None,
                    "notice": unselected(uid)}  # fmt: skip
        return results.related(run)

    @api.get("/curve")
    def selected_curve(
        uid: SELECTED = "", view: VIEW = None, max_points: MAX_POINTS = 2000
    ) -> dict[str, Any]:
        run = adapter.selection(uid)
        if run is None:
            return no_curve(unselected(uid))
        return results.curve(run, view, max_points)

    return api
