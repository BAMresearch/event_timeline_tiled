"""The dashboard's selection: ``/selection/...``.

The same reads as ``/runs/{uid}/...``, with the run (and stream) as query
parameters that may be empty: nothing selected is a normal state of the
dashboard, not an error, and so is a selected run that this Tiled does not
have (a bookmark, a shared link, a run from another server) or a run key
found under several catalogue roots without ``root``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from ..lanes import final_names
from ..views import ViewKind
from .common import (
    AXIS,
    FROM,
    LANE,
    MAX_POINTS,
    PRESET,
    ROOT,
    SELECTED,
    TO,
    TZ,
    VIEW,
    X_AXIS,
    check_window,
    zone,
)
from .metadata import metadata_rows
from .results import Results, no_curve
from .runs import Runs, summary_rows
from .state import Adapter
from .trends import Trends

RUNS_LIMIT = Annotated[int, Query(ge=1, le=500)]


def router(adapter: Adapter, runs: Runs, trends: Trends, results: Results) -> APIRouter:
    api = APIRouter(prefix="/selection")

    @api.get("/run")
    def selected_run(uid: SELECTED = "", root: ROOT = None, tz: TZ = "") -> dict[str, Any]:
        chosen = adapter.selection(uid, root)
        if chosen.run is None:
            return {"selected": bool(uid), "found": False,
                    "summary": [{"key": "run", "value": chosen.notice,
                                 "text": chosen.notice}],
                    "metadata": [], "metadata_truncated": False}  # fmt: skip
        summary = runs.summary(chosen.run)
        metadata, truncated = metadata_rows(chosen.run)
        return {"selected": True, "found": True, "run": summary,
                "summary": summary_rows(summary, zone(tz)),
                "metadata": metadata, "metadata_truncated": truncated}  # fmt: skip

    @api.get("/streams")
    def selected_streams(uid: SELECTED = "", root: ROOT = None) -> list[dict[str, Any]]:
        chosen = adapter.selection(uid, root)
        return runs.streams(chosen.run) if chosen.run is not None else []

    @api.get("/fields")
    def selected_fields(
        uid: SELECTED = "", stream: SELECTED = "", root: ROOT = None
    ) -> list[dict[str, Any]]:
        chosen = adapter.selection(uid, root) if stream else None
        return runs.fields(chosen.run, stream) if chosen and chosen.run is not None else []

    @api.get("/data")
    def selected_data(
        uid: SELECTED = "",
        view: VIEW = None,
        kind: ViewKind | None = None,
        max_points: MAX_POINTS = 2000,
        axis: AXIS = None,
        root: ROOT = None,
        x: X_AXIS = "time",
    ) -> dict[str, Any]:
        if view and kind:
            raise HTTPException(422, "give view or kind, not both")
        chosen = adapter.selection(uid, root)
        if chosen.run is None:
            return {"view": None, "rows": [], "truncated": False, "notice": chosen.notice}
        return runs.data(chosen.run, view, kind, max_points, axis, x)

    @api.get("/trend")
    def selected_trend(
        t_from: FROM,
        t_to: TO,
        uid: SELECTED = "",
        max_points: MAX_POINTS = 2000,
        axis: AXIS = None,
        preset: PRESET = None,
        lane: LANE = None,
        root: ROOT = None,
    ) -> dict[str, Any]:
        check_window(t_from, t_to)
        chosen = adapter.selection(uid, root)
        if chosen.run is None:
            return {"view": None, "rows": [], "truncated": False, "notice": chosen.notice}
        return trends.trend(chosen.run, t_from, t_to, max_points, axis, preset, lane)

    @api.get("/runs")
    def selected_lane_runs(
        t_from: FROM,
        t_to: TO,
        uid: SELECTED = "",
        preset: PRESET = None,
        lane: LANE = None,
        root: ROOT = None,
        limit: RUNS_LIMIT = 50,
    ) -> dict[str, Any]:
        """The latest runs on the selected lane, each once, newest first; with
        no lane, the preset's runs in the window. A run without a lane (a
        direct link) uses its first lane in the preset (presets.md §10)."""
        check_window(t_from, t_to)
        result, arranged = adapter.arrange(adapter.preset(preset), t_from, t_to)
        names, _ = final_names(arranged.lanes)
        chosen = adapter.selection(uid, root) if uid and not lane else None
        in_lane = arranged.lane(lane) if lane else None
        notice = ""
        if lane and in_lane is None:
            return {"preset": arranged.preset, "lane": None, "lane_name": None, "rows": [],
                    "truncated": False,
                    "notice": "That lane is not in this preset's range: select a run"}  # fmt: skip
        if chosen is not None and chosen.run is not None:
            in_lane = arranged.first_lane(chosen.run.root, chosen.run.key)
            notice = ", the selected run's first lane" if in_lane else ""
        placements = [p for p in arranged.placements if in_lane is None or p.lane.id == in_lane.id]
        seen: dict[tuple[str, str], Any] = {}
        for p in sorted(placements, key=lambda p: (-p.clip.start, p.clip.root, p.clip.uid)):
            seen.setdefault((p.clip.root, p.clip.uid), p)
        latest = list(seen.values())
        rows = [
            {
                "uid": p.clip.uid,
                "root": p.clip.root or "/",
                "label": p.label,
                "start": p.clip.start,
                "stop": p.clip.stop,
                "status": p.clip.status.value,
                "plan_name": result.records[(p.clip.root, p.clip.uid)].start.get("plan_name"),
                "lane_id": p.lane.id,
                "lane": names[p.lane.id],
            }  # fmt: skip
            for p in latest[:limit]
        ]
        where = f"on {names[in_lane.id]}" if in_lane else "in this preset"
        truncated = len(latest) > limit or result.truncated
        return {"preset": arranged.preset, "lane": in_lane.id if in_lane else None,
                "lane_name": names[in_lane.id] if in_lane else None, "rows": rows,
                "truncated": truncated,
                "notice": f"The latest {len(rows)} of {len(latest)} runs "
                          f"{where}{notice}"}  # fmt: skip

    @api.get("/related")
    def selected_related(uid: SELECTED = "", root: ROOT = None) -> dict[str, Any]:
        chosen = adapter.selection(uid, root)
        if chosen.run is None:
            return {"role": None, "rows": [], "truncated": False, "default": None,
                    "notice": chosen.notice}  # fmt: skip
        return results.related(chosen.run)

    @api.get("/curve")
    def selected_curve(
        uid: SELECTED = "", view: VIEW = None, max_points: MAX_POINTS = 2000, root: ROOT = None
    ) -> dict[str, Any]:
        chosen = adapter.selection(uid, root)
        if chosen.run is None:
            return no_curve(chosen.notice)
        return results.curve(chosen.run, view, max_points)

    return api
