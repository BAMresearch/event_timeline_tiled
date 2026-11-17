"""Clips, lanes and the timeline frame (plan §3.1, §8).

Pure functions: no Tiled access. A *clip* is one run as the timeline shows
it; its *lane key* is (facility, instrument, station, role). The frame is the
long table Grafana's State timeline splits with one "Partition by values" on
the final ``lane`` column, so lanes and sub-lanes are allocated here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

from attrs import frozen

from .layout import RunRecord
from .run_state import Activity, Role, Status, role_of, status_of

UNKNOWN_INSTRUMENT = "(unknown)"
MIN_DISPLAY_FRACTION = 0.002
"""Clips shorter than this fraction of the window are widened for display
only (a 3-second run in 12 hours would be a fraction of a pixel); their true
extent stays in ``clip_from_ms``/``clip_to_ms``."""
ZOOM_PAD_FRACTION = 0.05
ZOOM_PAD_MIN_S = 5.0
""""Zoom to run" shows the run with this much time on either side (a fraction
of its duration, at least ``ZOOM_PAD_MIN_S``): never an empty range."""


def _component(value: str | None) -> str:
    """One lane-key component: percent-encoded, ``-`` for a missing one."""
    if value is None:
        return "-"
    return "%2D" if value == "-" else quote(value, safe="")


@frozen
class LaneKey:
    facility: str | None
    instrument: str
    station: str | None
    role: Role

    def as_text(self) -> str:
        """``facility/instrument/station/role``, each part percent-encoded and
        ``-`` for a missing one, so different keys never share a text."""
        return "/".join(
            [
                _component(self.facility),
                _component(self.instrument),
                _component(self.station),
                self.role.value,
            ]
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
    """Display names, unique per key: instrument, station, "· processing"; the
    facility is added only where two lanes would otherwise share a name, and
    a number in brackets where even that is not enough (in lane order)."""

    def short(k: LaneKey) -> str:
        name = k.instrument + (f" {k.station}" if k.station else "")
        return name + (" · processing" if k.role is Role.PROCESSING else "")

    ordered = sorted(set(keys), key=lambda k: (*_lane_order(k), k.as_text()))
    names = {k: short(k) for k in ordered}
    counts: dict[str, int] = {}
    for name in names.values():
        counts[name] = counts.get(name, 0) + 1
    taken = _Names()
    return {
        k: taken.claim(f"{k.facility or '-'} {name}" if counts[name] > 1 else name)
        for k, name in names.items()
    }


class _Names:
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
    observed_at: float | None = None
    """When this run's record was last fetched from Tiled: an open run is
    drawn up to here and no further."""

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
        lane_key=lane_key(run),
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


def _lane_order(key: LaneKey) -> tuple[str, str, str, int]:
    return (
        key.facility or "",
        key.instrument,
        key.station or "",
        0 if key.role is Role.ACQUISITION else 1,
    )


def _zoom(start: float, end: float) -> tuple[int, int]:
    pad = max((end - start) * ZOOM_PAD_FRACTION, ZOOM_PAD_MIN_S)
    return round((start - pad) * 1000), round((end + pad) * 1000)


def frame(
    clips: Sequence[Clip], t_from: float, t_to: float, min_lanes: int = 0
) -> list[dict[str, Any]]:
    """The timeline frame: one row per clip, in lane order, with ``lane`` final.

    - ``start``/``stop``: display extent in **milliseconds** (Grafana time
      fields; named so they sort in this order). Open runs end at their
      ``observed_at`` (the last successful fetch of their source), never
      before their start; a closed run without a usable stop time is drawn at
      its start. Clips shorter than :data:`MIN_DISPLAY_FRACTION` of the window
      are widened, but never over the next clip in their sub-lane: runs queued
      back to back stay in one lane at every zoom level.
    - Sub-lanes: per lane, sorted by (start, uid), each clip takes the lowest
      sub-lane free at its start, by **true** extent; touching clips may share,
      clips starting at the same instant may not. No clip is dropped. Every
      (lane, sub-lane) has its own final name.
    - ``state``: the visible value (see :func:`display_state`).
    - ``clip_from_ms``/``clip_to_ms``: the true extent (open runs: up to
      ``observed_at``); ``zoom_from_ms``/``zoom_to_ms``: that extent padded,
      for "Zoom to run".
    - ``min_lanes``: with fewer lanes (sub-lanes included), empty lanes are
      added below, so that a panel of fixed height does not draw one lane
      as one huge bar. An empty lane is one row with an invisible, unique
      name (non-breaking spaces), ``start`` = ``stop`` = ``from`` and every
      other field null.
    """
    names = lane_names([c.lane_key for c in clips])
    min_width = max(t_to - t_from, 0.0) * MIN_DISPLAY_FRACTION
    rows: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    final = _Names()
    for name in names.values():
        final.claim(name)
    sub_lanes: dict[tuple[LaneKey, int], str] = {}
    extents = []
    for clip in clips:
        if clip.status is Status.OPEN:
            end = max(clip.observed_at if clip.observed_at is not None else clip.start, clip.start)
        else:
            end = clip.stop if clip.stop is not None and clip.stop >= clip.start else clip.start
        extents.append((clip, clip.start, end))
    # Sub-lanes by true extent: (busy until, last start) per sub-lane.
    busy: dict[LaneKey, list[tuple[float, float]]] = {}
    placed: list[tuple[Clip, float, float, int]] = []
    for clip, start, end in sorted(extents, key=lambda e: (e[1], e[0].uid)):
        free = busy.setdefault(clip.lane_key, [])
        for index, (busy_until, last_start) in enumerate(free):
            if start >= busy_until and start > last_start:
                free[index] = (end, start)
                break
        else:
            free.append((end, start))
            index = len(free) - 1
        placed.append((clip, start, end, index))
    # Widen short clips up to the next start in their sub-lane (placed is in start order).
    next_start: dict[tuple[LaneKey, int], float] = {}
    display_ends: dict[int, float] = {}
    for position in range(len(placed) - 1, -1, -1):
        clip, start, end, index = placed[position]
        limit = next_start.get((clip.lane_key, index), math.inf)
        display_ends[position] = max(end, min(start + min_width, limit))
        next_start[(clip.lane_key, index)] = start
    for position, (clip, start, end, index) in enumerate(placed):
        display_end = display_ends[position]
        if index == 0:
            lane = names[clip.lane_key]
        else:
            lane = sub_lanes.get((clip.lane_key, index)) or sub_lanes.setdefault(
                (clip.lane_key, index), final.claim(f"{names[clip.lane_key]} /{index + 1}")
            )
        zoom_from, zoom_to = _zoom(clip.start, end)
        rows.append(
            (
                (*_lane_order(clip.lane_key), clip.lane_key.as_text(), index, start, clip.uid),
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
                    "zoom_from_ms": zoom_from,
                    "zoom_to_ms": zoom_to,
                    "activity_error": clip.activity.error,
                },
            )
        )
    out = [row for _, row in sorted(rows, key=lambda r: r[0])]
    lanes = {row["lane"] for row in out}
    blank = 0
    for _ in range(min_lanes - len(lanes)):
        blank += 1
        while "\u00a0" * blank in lanes:  # never a real lane's name
            blank += 1
        out.append(_empty_lane("\u00a0" * blank, round(t_from * 1000)))
    return out


def _empty_lane(name: str, at_ms: int) -> dict[str, Any]:
    return {"lane": name, "start": at_ms, "stop": at_ms, "state": None, "status": None,
            "uid": None, "label": None, "clip_from_ms": None, "clip_to_ms": None,
            "possibly_stalled": None, "stale": None, "zoom_from_ms": None, "zoom_to_ms": None,
            "activity_error": None}  # fmt: skip
