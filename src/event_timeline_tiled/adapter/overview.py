"""Health, queue status, presets and the window's runs: ``/health``,
``/queues``, ``/presets``, ``/clips``, ``/clips/frame``, ``/clips/status``."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from .. import __version__
from ..clips import frame
from ..lanes import Arrangement, final_names
from ..run_state import Status
from ..settings import Settings
from ..tiled_http import HealthStatus
from ..timeline import SourceFetch, WindowResult
from ..unfold import Unfolded, unfold_lane
from .common import FROM, LANE, PRESET, TO, check_window
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


def _arrangement_issues(
    arranged: Arrangement, rows_cut: int, settings: Settings
) -> list[tuple[str, str, str]]:
    """Problems with how the preset's lanes could be drawn (presets.md §6.3, §9)."""
    out: list[tuple[str, str, str]] = []
    if arranged.lanes_cut:
        out.append(("incomplete", "", f"{arranged.lanes_cut} lane(s) over the lane budget "
                                      "not drawn; narrow the preset's grouping"))  # fmt: skip
    if arranged.placements_cut:
        out.append(("incomplete", "", f"{arranged.placements_cut} placement(s) over "
                                      f"{settings.max_placements} not drawn"))  # fmt: skip
    if rows_cut:
        out.append(("incomplete", "", f"{rows_cut} run(s) on rows over "
                                      f"{settings.max_sublanes} not drawn; narrow the time "
                                      "range or the grouping"))  # fmt: skip
    if arranged.streams_unknown:
        out.append(("partial", "", f"{arranged.streams_unknown} run(s) left out of tracks "
                                   "that need streams not known yet"))  # fmt: skip
    if arranged.invalid_groups:
        out.append(("partial", "", f"{arranged.invalid_groups} placement(s) with a list or "
                                   "mapping as a group value"))  # fmt: skip
    return out


def _unfold_issues(unfolded: Unfolded | None) -> list[tuple[str, str, str]]:
    """What unfolding a lane could not draw (presets.md §6.5): completeness
    issues, never run failures."""
    if unfolded is None:
        return []
    if unfolded.lane is None:
        return [("partial", "", "the unfolded lane is not in this range: fold it, or select "
                                "another")]  # fmt: skip
    out: list[tuple[str, str, str]] = []
    notes = [n for n in unfolded.notes if n]
    if unfolded.runs_folded:
        why = f" ({'; '.join(notes)})" if notes else ""
        out.append(("partial", "", f"{unfolded.runs_folded} run(s) of the unfolded lane drawn "
                                   f"folded{why}"))  # fmt: skip
    missing = {k: v for k, v in unfolded.streams.items()
               if k in ("pending", "invalid", "unreadable", "refused") and v}  # fmt: skip
    if missing:
        listed = ", ".join(f"{v} {k}" for k, v in sorted(missing.items()))
        out.append(("partial", "", f"streams of the unfolded lane not drawn: {listed}"))
    if unfolded.stale:
        out.append(("stale", "", f"{unfolded.stale} unfolded run(s) as last read"))
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
    def queue_status(preset: PRESET = None) -> list[dict[str, Any]]:
        """One row per configured RE Manager (plan §5.5), read now. Unreachable
        managers have ``reachable: false`` and no state, never "idle". With
        ``preset``, only the managers whose instrument its scope admits."""
        if preset is None:
            return adapter.queues.rows()
        chosen = adapter.preset(preset)
        return adapter.queues.rows(chosen.admits_instrument)

    @api.get("/presets")
    def presets() -> list[dict[str, Any]]:
        """The loaded presets, ``{id, title}``: the default first, then by title."""
        return [{"id": p.id, "title": p.title} for p in adapter.presets.ordered()]

    def _drawn(
        preset: str | None, t_from: float, t_to: float, min_lanes: int | None, unfold: str | None
    ) -> tuple[WindowResult, Arrangement, list[dict[str, Any]], int, Unfolded | None]:
        chosen = adapter.preset(preset)
        result, arranged = adapter.arrange(chosen, t_from, t_to)
        names, taken = final_names(arranged.lanes)
        unfolded = None
        if unfold:
            now = clock()
            unfolded = unfold_lane(arranged, unfold, result.records,
                                   lambda run: timeline.extents_of(run, now), settings)  # fmt: skip
        padding = chosen.lanes.min_lanes if min_lanes is None else min_lanes
        rows, rows_cut = frame(arranged.placements, names, taken, t_from, t_to, padding,
                               settings.max_sublanes, unfolded)  # fmt: skip
        return result, arranged, rows, rows_cut, unfolded

    def _about(arranged: Arrangement) -> dict[str, Any]:
        return {"preset": arranged.preset, "preset_revision": adapter.presets.revision,
                "evaluated_at": arranged.evaluated_at}  # fmt: skip

    @api.get("/clips")
    def clips(
        t_from: FROM,
        t_to: TO,
        status: Annotated[str | None, Query(pattern=r"^[a-z]+(,[a-z]+)*$")] = None,
        preset: PRESET = None,
    ) -> dict[str, Any]:
        """Runs overlapping ``[from, to)`` that the preset places, one row per
        run (seconds), with every placement; ``status`` keeps only runs with
        one of these comma-separated statuses."""
        check_window(t_from, t_to)
        wanted = None if status is None else set(status.split(","))
        if wanted is not None and not wanted <= {s.value for s in Status}:
            raise HTTPException(422, f"status must be among {', '.join(s.value for s in Status)}")
        result, arranged = adapter.arrange(adapter.preset(preset), t_from, t_to)
        names, _ = final_names(arranged.lanes)
        now = clock()
        by_run: dict[tuple[str, str], list[Any]] = {}
        for p in arranged.placements:
            by_run.setdefault((p.clip.root, p.clip.uid), []).append(p)
        rows = []
        ordered = sorted(by_run.items(), key=lambda kv: (kv[1][0].clip.start, kv[0][1], kv[0][0]))
        for _, placed in ordered:
            clip, first = placed[0].clip, placed[0].lane
            if wanted is not None and clip.status.value not in wanted:
                continue
            row = clip.as_row(first.id, names[first.id])
            row["placements"] = [
                {
                    "preset": p.lane.preset,
                    "track": p.lane.track,
                    "lane_id": p.lane.id,
                    "lane": names[p.lane.id],
                    "label": p.label,
                }  # fmt: skip
                for p in placed
            ]
            rows.append(row)
        return {
            "from": t_from,
            "to": t_to,
            **_about(arranged),
            "clips": rows,
            "truncated": result.truncated,
            "unplaceable": result.unplaceable,
            "sources": [_fetch_row(f, now) for f in result.fetches],
        }

    @api.get("/clips/frame")
    def clips_frame(
        t_from: FROM,
        t_to: TO,
        min_lanes: Annotated[int | None, Query(ge=0, le=20)] = None,
        preset: PRESET = None,
        unfold: LANE = None,
    ) -> list[dict[str, Any]]:
        """The timeline frame of plan §8 (milliseconds) in the preset's lanes,
        final, padded with empty rows up to ``min_lanes`` (default: the
        preset's); with ``unfold``, that lane drawn per stream. Whether it is
        complete is ``/clips/status`` (with the same parameters)."""
        check_window(t_from, t_to)
        return _drawn(preset, t_from, t_to, min_lanes, unfold)[2]

    @api.get("/clips/status")
    def clips_status(
        t_from: FROM, t_to: TO, preset: PRESET = None, unfold: LANE = None
    ) -> dict[str, Any]:
        """Is the window's data complete, as the preset draws it? ``issues``
        lists every reason it is not (one ``complete`` row if it is), worst
        first."""
        check_window(t_from, t_to)
        result, arranged, rows, rows_cut, unfolded = _drawn(preset, t_from, t_to, 0, unfold)
        now = clock()
        issues = sorted(
            [*_issues(result, settings, now), *_arrangement_issues(arranged, rows_cut, settings),
             *_unfold_issues(unfolded)],
            key=lambda i: -_SEVERITY[i[0]],
        )  # fmt: skip
        state = issues[0][0] if issues else "complete"
        if not issues:
            roots = len(result.fetches)
            issues = [
                ("complete", "",
                 f"{len(result.clips)} runs from {roots} root(s), read now, in "
                 f"{len(arranged.lanes)} lane(s); open runs from up to "
                 f"{settings.open_run_horizon_days:g} days before the range end")
            ]  # fmt: skip
        return {
            **_about(arranged),
            "matched_runs": arranged.matched,
            "out_of_scope": arranged.out_of_scope,
            "placements": len(arranged.placements),
            "lanes": len(arranged.lanes),
            "rows": len({r["lane"] for r in rows}),
            "lanes_cut": arranged.lanes_cut,
            "placements_cut": arranged.placements_cut,
            "rows_cut": rows_cut,
            "streams_unknown": arranged.streams_unknown,
            "invalid_groups": arranged.invalid_groups,
            "unfold": unfolded.as_status() if unfolded is not None else None,
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
