"""Unfolding one lane into its streams (presets.md §6.5).

Which of the lane's runs are read, what each stream shows, and what is
reported. The frame draws the result; :mod:`.timeline` reads and keeps the
extents. One lane at a time, within run, stream, event and time budgets.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from attrs import field, frozen

from .lanes import Arrangement, Lane
from .layout import RunRecord, StreamExtent, StreamState
from .settings import Settings
from .timeline import RunExtents


@frozen
class Unfolded:
    requested: str
    lane: Lane | None
    """``None``: the requested lane is not in this preset's range."""
    extents: Mapping[tuple[str, str], tuple[StreamExtent, ...]] = field(factory=dict)
    """The drawable streams of each unfolded run, by ``(root, uid)``."""
    runs_unfolded: int = 0
    runs_folded: int = 0
    """Runs of the lane drawn folded: beyond the run cap or the time budget,
    or with no drawable stream."""
    streams: Mapping[str, int] = field(factory=dict)
    """Streams by state (``drawn``, ``empty``, …), over the unfolded runs."""
    stale: int = 0
    """Runs whose latest read failed: their last good extents are shown."""
    notes: tuple[str, ...] = ()
    """Which budgets applied, and other reasons for folded runs."""

    def as_status(self) -> dict[str, Any]:
        return {
            "lane": self.lane.id if self.lane else None,
            "requested": self.requested,
            "found": self.lane is not None,
            "runs_unfolded": self.runs_unfolded,
            "runs_folded": self.runs_folded,
            "streams": dict(self.streams),
            "stale": self.stale,
            "notes": list(self.notes),
        }


def unfold_lane(
    arranged: Arrangement,
    lane_id: str,
    records: Mapping[tuple[str, str], RunRecord],
    extents_of: Callable[[RunRecord], RunExtents],
    settings: Settings,
    monotonic: Callable[[], float] = time.monotonic,
) -> Unfolded:
    """Plan the unfolding of ``lane_id``: the lane's latest runs (at most
    ``ETT_UNFOLD_MAX_RUNS``, read within ``ETT_UNFOLD_TIME_S``) with their
    drawable streams; everything else of the lane stays folded."""
    lane = arranged.lane(lane_id)
    if lane is None:
        return Unfolded(lane_id, None, notes=("the unfolded lane is not in this range",))
    runs = sorted(arranged.runs_in(lane.id), key=lambda c: (-c.start, c.root, c.uid))
    deadline = monotonic() + settings.unfold_time_s
    drawn: dict[tuple[str, str], tuple[StreamExtent, ...]] = {}
    states: Counter[str] = Counter()
    stale = folded = 0
    notes: list[str] = []
    for index, clip in enumerate(runs):
        if index >= settings.unfold_max_runs:
            folded += len(runs) - index
            notes.append(f"only the latest {settings.unfold_max_runs} runs are unfolded")
            break
        if monotonic() > deadline:
            folded += len(runs) - index
            notes.append(f"reading stopped after {settings.unfold_time_s:g} s; "
                         f"{len(runs) - index} runs left folded")  # fmt: skip
            break
        extents = extents_of(records[(clip.root, clip.uid)])
        stale += extents.stale
        if extents.error is not None and not extents.streams:
            notes.append(f"streams of a run could not be listed ({extents.error})")
        states.update(e.state.value for e in extents.streams)
        visible = tuple(e for e in extents.streams if e.state is StreamState.DRAWN)
        if visible:
            drawn[(clip.root, clip.uid)] = visible
        else:
            folded += 1
    return Unfolded(
        lane_id,
        lane,
        drawn,
        runs_unfolded=len(drawn),
        runs_folded=folded,
        streams=dict(states),
        stale=stale,
        notes=tuple(dict.fromkeys(notes)),
    )
