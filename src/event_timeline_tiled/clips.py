"""Clips, lanes and the timeline frame (plan §3.1, §8).

Pure functions: no Tiled access. A *clip* is one run as the timeline shows
it; its *lane key* is (facility, instrument, station, role). The frame is the
long table Grafana's State timeline splits with one "Partition by values" on
the final ``lane`` column, so lanes and sub-lanes are allocated here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from attrs import frozen

from .layout import RunRecord
from .run_state import Activity, Role, Status, role_of, status_of

UNKNOWN_INSTRUMENT = "(unknown)"
MIN_DISPLAY_FRACTION = 0.002
"""Clips shorter than this fraction of the window are widened for display
only (a 3-second run in 12 hours would be a fraction of a pixel); their true
extent stays in ``clip_from_ms``/``clip_to_ms``."""


@frozen
class LaneKey:
    facility: str | None
    instrument: str
    station: str | None
    role: Role

    def as_text(self) -> str:
        return "/".join(
            [self.facility or "-", self.instrument, self.station or "-", self.role.value]
        )


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def lane_key(run: RunRecord) -> LaneKey:
    """Runs without ``instrument`` go to a labelled fallback: their catalogue
    root if it is not the server root, else ``(unknown)``. Never one lane per run."""
    instrument = _text(run.start.get("instrument"))
    if instrument is None:
        instrument = f"({run.root})" if run.root else UNKNOWN_INSTRUMENT
    return LaneKey(
        _text(run.start.get("facility")),
        instrument,
        _text(run.start.get("station")),
        role_of(run.start),
    )


def lane_names(keys: Sequence[LaneKey]) -> dict[LaneKey, str]:
    """Display names: instrument, station, "· processing"; the facility is added
    only where two lanes would otherwise share a name."""

    def short(k: LaneKey) -> str:
        name = k.instrument + (f" {k.station}" if k.station else "")
        return name + (" · processing" if k.role is Role.PROCESSING else "")

    names = {k: short(k) for k in keys}
    counts: dict[str, int] = {}
    for name in names.values():
        counts[name] = counts.get(name, 0) + 1
    return {
        k: (f"{k.facility or '-'} {name}" if counts[name] > 1 else name)
        for k, name in names.items()
    }


@frozen
class Clip:
    """One run on the timeline. Times are epoch seconds."""

    uid: str
    start_uid: str | None
    uid_mismatch: bool
    root: str
    lane_key: LaneKey
    role: Role
    start: float
    stop: float | None
    status: Status
    activity: Activity
    stale: bool
    label: str
    reason: str | None = None
    """The stop document's ``reason`` (failed and aborted runs)."""

    def as_row(self, lane: str) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "start_uid": self.start_uid,
            "uid_mismatch": self.uid_mismatch,
            "root": self.root or "/",
            "lane_key": self.lane_key.as_text(),
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
        }


def make_clip(run: RunRecord, activity: Activity, stale: bool, label: str) -> Clip | None:
    """A clip, or ``None`` for a run without a usable start time (reported, not drawn)."""
    if run.start_time is None:
        return None
    return Clip(
        uid=run.key,
        start_uid=run.start_uid,
        uid_mismatch=run.uid_mismatch,
        root=run.root,
        lane_key=lane_key(run),
        role=role_of(run.start),
        start=run.start_time,
        stop=run.stop_time,
        status=status_of(run.stop),
        activity=activity,
        stale=stale,
        label=label,
        reason=_text((run.stop or {}).get("reason")),
    )


def display_state(clip: Clip) -> str:
    """The one visible value of a frame row: the status, except that an open
    run's inferred activity or a stale source is shown instead of "open".
    The separate ``status``, ``possibly_stalled`` and ``stale`` columns keep
    the three apart."""
    if clip.status is Status.OPEN:
        if clip.stale:
            return "stale"
        if clip.activity.possibly_stalled:
            return "possibly stalled"
    return clip.status.value


def _lane_order(key: LaneKey) -> tuple[str, str, str, int]:
    return (
        key.facility or "",
        key.instrument,
        key.station or "",
        0 if key.role is Role.ACQUISITION else 1,
    )


def frame(
    clips: Sequence[Clip], t_from: float, t_to: float, open_until: float
) -> list[dict[str, Any]]:
    """The timeline frame: one row per clip, in lane order, with ``lane`` final.

    - ``start``/``stop``: display extent in **milliseconds** (Grafana time
      fields; named so they sort in this order). Open runs, and closed runs
      without a usable stop time, end at ``open_until`` (the last successful
      fetch); clips shorter than :data:`MIN_DISPLAY_FRACTION` of the window are
      widened.
    - Sub-lanes: per lane, sorted by (display start, uid), each clip takes the
      lowest sub-lane free at its display start; touching clips may share. No
      clip is dropped.
    - ``state``: the visible value (see :func:`display_state`).
    - ``clip_from_ms``/``clip_to_ms``: the true extent, for "Zoom to run".
    """
    names = lane_names(sorted({c.lane_key for c in clips}, key=_lane_order))
    min_width = max(t_to - t_from, 0.0) * MIN_DISPLAY_FRACTION
    rows: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    ends: dict[LaneKey, list[float]] = {}
    extents = []
    for clip in clips:
        true_end = clip.stop if clip.stop is not None and clip.status is not Status.OPEN else None
        end = true_end if true_end is not None else max(open_until, clip.start)
        extents.append((clip, clip.start, max(end, clip.start + min_width), true_end, end))
    for clip, start, display_end, _true_end, end in sorted(extents, key=lambda e: (e[1], e[0].uid)):
        free = ends.setdefault(clip.lane_key, [])
        for index, busy_until in enumerate(free):
            if start >= busy_until:
                free[index] = display_end
                break
        else:
            free.append(display_end)
            index = len(free) - 1
        lane = names[clip.lane_key] + ("" if index == 0 else f" /{index + 1}")
        rows.append(
            (
                (*_lane_order(clip.lane_key), index, start, clip.uid),
                {
                    "lane": lane,
                    "start": round(start * 1000),
                    "stop": round(display_end * 1000),
                    "state": display_state(clip),
                    "status": clip.status.value,
                    "uid": clip.uid,
                    "label": clip.label,
                    "clip_from_ms": round(clip.start * 1000),
                    "clip_to_ms": round(end * 1000),
                    "possibly_stalled": clip.activity.possibly_stalled,
                    "stale": clip.stale,
                },
            )
        )
    return [row for _, row in sorted(rows, key=lambda r: r[0])]
