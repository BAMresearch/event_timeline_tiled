"""Clips and the timeline frame (plan §3.1, §8; presets.md §6).

Pure functions: no Tiled access. A *clip* is one run as the timeline shows
it. Which lanes it is drawn in is a preset's business (:mod:`.lanes`); the
frame is the long table Grafana's State timeline splits with one "Partition
by values" on the final ``lane`` column, so sub-lanes and every row's final,
unique name are allocated here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from attrs import frozen

from .layout import RunRecord
from .run_state import Activity, Role, Status, role_of, status_of

if TYPE_CHECKING:
    from .lanes import Placement

MIN_DISPLAY_FRACTION = 0.002
"""Clips shorter than this fraction of the window are widened for display
only (a 3-second run in 12 hours would be a fraction of a pixel); their true
extent stays in ``clip_from_ms``/``clip_to_ms``."""
ZOOM_PAD_FRACTION = 0.05
ZOOM_PAD_MIN_S = 5.0
""""Zoom to run" shows the run with this much time on either side (a fraction
of its duration, at least ``ZOOM_PAD_MIN_S``): never an empty range."""


class Names:
    """Hands out unique display names: a name already taken gets " (2)", " (3)", …"""

    def __init__(self) -> None:
        self._taken: set[str] = set()

    def claim(self, name: str) -> str:
        candidate, n = name, 1
        while candidate in self._taken:
            n += 1
            candidate = f"{name} ({n})"
        self._taken.add(candidate)
        return candidate

    def __contains__(self, name: str) -> bool:
        return name in self._taken


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


@frozen
class Clip:
    """One run on the timeline. Times are epoch seconds."""

    uid: str
    start_uid: str | None
    uid_mismatch: bool
    root: str
    role: Role
    start: float
    stop: float | None
    status: Status
    activity: Activity
    stale: bool
    label: str
    reason: str | None = None
    """The stop document's ``reason`` (failed and aborted runs)."""
    observed_at: float | None = None
    """When this run's record was last fetched from Tiled: an open run is
    drawn up to here and no further."""

    def as_row(self, lane_id: str | None, lane: str | None) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "start_uid": self.start_uid,
            "uid_mismatch": self.uid_mismatch,
            "root": self.root or "/",
            "lane_key": lane_id,
            "lane": lane,
            "role": self.role.value,
            "start": self.start,
            "stop": self.stop,
            "status": self.status.value,
            "last_event": self.activity.last_event,
            "last_event_age_s": self.activity.age_s,
            "possibly_stalled": self.activity.possibly_stalled,
            "stale": self.stale,
            "label": self.label,
            "reason": self.reason,
            "observed_at": self.observed_at,
            "activity_error": self.activity.error,
        }


def make_clip(
    run: RunRecord, activity: Activity, stale: bool, label: str, observed_at: float | None = None
) -> Clip | None:
    """A clip, or ``None`` for a run without a usable start time (reported, not drawn)."""
    if run.start_time is None:
        return None
    return Clip(
        uid=run.key,
        start_uid=run.start_uid,
        uid_mismatch=run.uid_mismatch,
        root=run.root,
        role=role_of(run.start),
        start=run.start_time,
        stop=run.stop_time,
        status=status_of(run.stop),
        activity=activity,
        stale=stale,
        label=label,
        reason=_text((run.stop or {}).get("reason")),
        observed_at=observed_at,
    )


def display_state(clip: Clip) -> str:
    """The one visible value of a frame row: the status, except that for an
    open run a stale source, unknown activity or inferred inactivity is shown
    instead of "open". The separate ``status``, ``possibly_stalled``,
    ``stale`` and ``activity_error`` fields keep them apart."""
    if clip.status is Status.OPEN:
        if clip.stale:
            return "stale"
        if clip.activity.error is not None:
            return "activity unknown"
        if clip.activity.possibly_stalled:
            return "possibly stalled"
    return clip.status.value


def _zoom(start: float, end: float) -> tuple[int, int]:
    pad = max((end - start) * ZOOM_PAD_FRACTION, ZOOM_PAD_MIN_S)
    return round((start - pad) * 1000), round((end + pad) * 1000)


def frame(
    placements: Sequence[Placement],
    names: dict[str, str],
    taken: Names,
    t_from: float,
    t_to: float,
    min_lanes: int = 0,
    max_rows: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """The timeline frame: one row per placement, in lane order, with ``lane``
    final; and how many placements the row budget left out.

    - ``names``: each logical lane's final name (by lane id), already claimed
      in ``taken``; sub-lanes and padding claim theirs from it, so no two
      rows share a name (Grafana partitions by it).
    - ``start``/``stop``: display extent in **milliseconds** (Grafana time
      fields; named so they sort in this order). Open runs end at their
      ``observed_at`` (the last successful fetch of their source), never
      before their start; a closed run without a usable stop time is drawn at
      its start. Clips shorter than :data:`MIN_DISPLAY_FRACTION` of the window
      are widened, but never over the next clip in their sub-lane: runs queued
      back to back stay in one lane at every zoom level.
    - Sub-lanes: per logical lane, sorted by (start, root, uid), each clip
      takes the lowest sub-lane free at its start, by **true** extent;
      touching clips may share, clips starting at the same instant may not.
    - ``max_rows``: the most final rows (lanes and sub-lanes); placements on
      further rows are left out and counted.
    - ``state``: the visible value (see :func:`display_state`).
    - ``clip_from_ms``/``clip_to_ms``: the true extent (open runs: up to
      ``observed_at``); ``zoom_from_ms``/``zoom_to_ms``: that extent padded,
      for "Zoom to run". ``lane_id``, ``track``, ``root``: which placement.
    - ``min_lanes``: with fewer rows, empty rows are added below, so that a
      panel of fixed height does not draw one lane as one huge bar. An empty
      row has an invisible, unique name (non-breaking spaces), ``start`` =
      ``stop`` = ``from`` and every other field null.
    """
    min_width = max(t_to - t_from, 0.0) * MIN_DISPLAY_FRACTION
    extents = []
    for placement in placements:
        clip = placement.clip
        if clip.status is Status.OPEN:
            end = max(clip.observed_at if clip.observed_at is not None else clip.start, clip.start)
        else:
            end = clip.stop if clip.stop is not None and clip.stop >= clip.start else clip.start
        extents.append((placement, clip.start, end))
    # Sub-lanes by true extent: (busy until, last start) per sub-lane.
    busy: dict[str, list[tuple[float, float]]] = {}
    placed: list[tuple[Placement, float, float, int]] = []
    for placement, start, end in sorted(
        extents, key=lambda e: (e[1], e[0].clip.root, e[0].clip.uid)
    ):
        free = busy.setdefault(placement.lane.id, [])
        for index, (busy_until, last_start) in enumerate(free):
            if start >= busy_until and start > last_start:
                free[index] = (end, start)
                break
        else:
            free.append((end, start))
            index = len(free) - 1
        placed.append((placement, start, end, index))
    # Rows within the budget, in lane order.
    order = {lane_id: n for n, lane_id in enumerate(dict.fromkeys(p.lane.id for p in placements))}
    rows_wanted = sorted({(order[p.lane.id], i) for p, _, _, i in placed})
    allowed = set(rows_wanted[:max_rows] if max_rows is not None else rows_wanted)
    left_out = sum(1 for p, _, _, i in placed if (order[p.lane.id], i) not in allowed)
    placed = [e for e in placed if (order[e[0].lane.id], e[3]) in allowed]
    # Widen short clips up to the next start in their sub-lane (placed is in start order).
    next_start: dict[tuple[str, int], float] = {}
    display_ends: dict[int, float] = {}
    for position in range(len(placed) - 1, -1, -1):
        placement, start, end, index = placed[position]
        limit = next_start.get((placement.lane.id, index), math.inf)
        display_ends[position] = max(end, min(start + min_width, limit))
        next_start[(placement.lane.id, index)] = start
    sub_lanes: dict[tuple[str, int], str] = {}
    for lane_id, index in sorted({(e[0].lane.id, e[3]) for e in placed if e[3] > 0},
                                 key=lambda k: (order[k[0]], k[1])):  # fmt: skip
        sub_lanes[(lane_id, index)] = taken.claim(f"{names[lane_id]} /{index + 1}")
    rows: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for position, (placement, start, end, index) in enumerate(placed):
        clip, lane = placement.clip, placement.lane
        name = names[lane.id] if index == 0 else sub_lanes[(lane.id, index)]
        zoom_from, zoom_to = _zoom(clip.start, end)
        rows.append(
            (
                (order[lane.id], index, start, clip.root, clip.uid),
                {
                    "lane": name,
                    "start": round(start * 1000),
                    "stop": round(display_ends[position] * 1000),
                    "state": display_state(clip),
                    "status": clip.status.value,
                    "uid": clip.uid,
                    "label": placement.label,
                    "clip_from_ms": round(clip.start * 1000),
                    "clip_to_ms": round(end * 1000),
                    "possibly_stalled": clip.activity.possibly_stalled,
                    "stale": clip.stale,
                    "zoom_from_ms": zoom_from,
                    "zoom_to_ms": zoom_to,
                    "activity_error": clip.activity.error,
                    "lane_id": lane.id,
                    "track": lane.track,
                    "root": clip.root or "/",
                },
            )
        )
    out = [row for _, row in sorted(rows, key=lambda r: r[0])]
    lanes = {row["lane"] for row in out}
    blank = 0
    for _ in range(min_lanes - len(lanes)):
        blank += 1
        while "\u00a0" * blank in taken:  # never a real lane's name
            blank += 1
        out.append(_empty_lane(taken.claim("\u00a0" * blank), round(t_from * 1000)))
    return out, left_out


def _empty_lane(name: str, at_ms: int) -> dict[str, Any]:
    return {"lane": name, "start": at_ms, "stop": at_ms, "state": None, "status": None,
            "uid": None, "label": None, "clip_from_ms": None, "clip_to_ms": None,
            "possibly_stalled": None, "stale": None, "zoom_from_ms": None, "zoom_to_ms": None,
            "activity_error": None, "lane_id": None, "track": None, "root": None}  # fmt: skip
