"""The timeline service: window searches per root, run state, caching and
source health (plan §5). The adapter's endpoints call this; it reads Tiled
only through :class:`~event_timeline_tiled.layout.Layout`.

Three things are kept apart (plan §5.2, §5.3): the recorded status of a run
(stop document), its inferred activity (last event, read separately and at
most every ``activity_refresh_s``), and the health of each source (did the
window search and the activity reads succeed *now*). When a root fails, its
runs from earlier successful fetches are shown as stale, each only up to the
time it was itself last observed.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping

from attrs import define, field, frozen

from .clips import Clip, make_clip
from .layout import Layout, RunRecord
from .run_state import Activity, Status, activity, status_of
from .settings import Settings
from .tiled_http import HealthStatus, SourceError
from .views import label, stall_after_s

MAX_CACHED_RUNS = 50_000
"""Runs kept per root for stale display; beyond it the least recently observed go."""
MAX_ACTIVITY_ENTRIES = 10_000
"""Open runs whose last activity read is remembered; least recently used go."""
_STRIPES = 64


@define
class SourceState:
    """What is known about fetching one catalogue root (plan §5.3), across requests."""

    root: str
    last_attempt: float | None = None
    last_success: float | None = None
    error: HealthStatus | None = None
    detail: str = ""
    runs: dict[str, tuple[RunRecord, float]] = field(factory=dict)
    """Runs from successful fetches, with when each was last observed, to show
    stale data on failure."""


@frozen
class SourceFetch:
    """The outcome of one window request for one root."""

    root: str
    ok: bool
    """The window search succeeded now (else the runs shown are stale)."""
    error: str | None
    detail: str
    last_success: float | None
    truncated: bool | None
    """More runs matched than ``ETT_MAX_RUNS``; ``None`` if unknown (failed now)."""
    runs: int
    activity_errors: int
    """Open runs whose events could not be read now."""
    activity_error: str | None
    """Category of the first such failure."""


@frozen
class WindowResult:
    clips: list[Clip]
    truncated: bool
    """Some root had more runs in the window than ``ETT_MAX_RUNS``."""
    unplaceable: list[str]
    """Keys of runs found without a usable start time (reported, not drawn)."""
    fetches: list[SourceFetch]
    records: Mapping[tuple[str, str], RunRecord] = field(factory=dict)
    """The run record of each clip, by ``(root, uid)``: what the search
    returned, so that callers need not fetch runs one by one."""


@frozen
class _Observed:
    attempt_at: float
    last_event: float | None
    """From the latest successful read (kept across failures)."""
    error: str | None
    """Category of the latest read's failure, ``None`` if it succeeded."""


class Timeline:
    def __init__(
        self, settings: Settings, layout: Layout, clock: Callable[[], float] = time.time
    ) -> None:
        self.settings = settings
        self.layout = layout
        self.clock = clock
        self.roots: tuple[str, ...] = settings.catalog_roots or ("",)
        self._lock = threading.Lock()
        self._sources = {root: SourceState(root) for root in self.roots}
        self._activity: OrderedDict[tuple[str, str], _Observed] = OrderedDict()
        self._stripes = [threading.Lock() for _ in range(_STRIPES)]

    def sources(self) -> list[SourceState]:
        with self._lock:
            return [self._sources[r] for r in self.roots]

    def window(self, t_from: float, t_to: float) -> WindowResult:
        now = self.clock()
        horizon = self.settings.open_run_horizon_days * 86400
        clips: list[Clip] = []
        records: dict[tuple[str, str], RunRecord] = {}
        unplaceable: list[str] = []
        fetches: list[SourceFetch] = []
        for root in self.roots:
            state = self._sources[root]
            truncated: bool | None
            try:
                runs, truncated = self.layout.window_runs(
                    root, t_from, t_to, horizon, self.settings.max_runs
                )
                with self._lock:
                    state.last_attempt = state.last_success = now
                    state.error, state.detail = None, ""
                    state.runs.update({r.key: (r, now) for r in runs})
                    _prune(state)
                observed = [(r, now) for r in runs]
                source_error: SourceError | None = None
            except SourceError as exc:
                with self._lock:
                    state.last_attempt = now
                    state.error, state.detail = exc.status, exc.detail
                    observed = [
                        (r, seen)
                        for r, seen in state.runs.values()
                        if _in_window(r, t_from, t_to, horizon)
                    ]
                truncated, source_error = None, exc
            activity_errors: list[str] = []
            for run, seen in observed:
                clip = self._clip(run, now, seen, source_error)
                if clip is None:
                    unplaceable.append(run.key)
                    continue
                clips.append(clip)
                records[(clip.root, clip.uid)] = run
                if source_error is None and clip.activity.error is not None:
                    activity_errors.append(clip.activity.error)
            fetches.append(
                SourceFetch(
                    root=root,
                    ok=source_error is None,
                    error=source_error.status.value if source_error else None,
                    detail=source_error.detail if source_error else "",
                    last_success=state.last_success,
                    truncated=truncated,
                    runs=len(observed),
                    activity_errors=len(activity_errors),
                    activity_error=activity_errors[0] if activity_errors else None,
                )
            )
        return WindowResult(
            clips=clips,
            truncated=any(f.truncated for f in fetches),
            unplaceable=unplaceable,
            fetches=fetches,
            records=records,
        )

    def _clip(
        self, run: RunRecord, now: float, seen: float, source_error: SourceError | None
    ) -> Clip | None:
        status = status_of(run.stop)
        last, error = None, None
        if status is Status.OPEN:
            if source_error is None:
                last, error = self.last_event(run, now)
            else:  # stale: nothing is inferred from an old observation
                last = self._remembered(run)
                error = source_error.status.value
        act = activity(status, run.start_time, last, now, stall_after_s(run), error)
        return make_clip(run, act, source_error is not None, label(run), seen)

    def activity_of(self, run: RunRecord, now: float) -> Activity:
        """The activity of one run, as the timeline would show it."""
        status = status_of(run.stop)
        last, error = self.last_event(run, now) if status is Status.OPEN else (None, None)
        return activity(status, run.start_time, last, now, stall_after_s(run), error)

    def last_event(self, run: RunRecord, now: float) -> tuple[float | None, str | None]:
        """``(last event, error)`` of an open run. Tiled is asked at most every
        ``activity_refresh_s`` per run, whether the last read succeeded or not
        (it costs a whole ``time`` column per stream, plan §5.2); concurrent
        requests for the same run wait for one read. After a failed read, the
        last event is the one from the latest successful read, with the error."""
        key = (run.root, run.key)
        with self._stripes[hash(key) % _STRIPES]:
            with self._lock:
                cached = self._activity.get(key)
            if cached is not None and now - cached.attempt_at < self.settings.activity_refresh_s:
                return cached.last_event, cached.error
            try:
                entry = _Observed(now, self.layout.last_event_time(run), None)
            except SourceError as exc:
                entry = _Observed(now, cached.last_event if cached else None, exc.status.value)
            with self._lock:
                self._activity[key] = entry
                self._activity.move_to_end(key)
                while len(self._activity) > MAX_ACTIVITY_ENTRIES:
                    self._activity.popitem(last=False)
            return entry.last_event, entry.error

    def _remembered(self, run: RunRecord) -> float | None:
        with self._lock:
            cached = self._activity.get((run.root, run.key))
        return cached.last_event if cached else None


def _in_window(run: RunRecord, t_from: float, t_to: float, horizon_s: float) -> bool:
    """The window rule of :meth:`Layout.window_runs`, applied to a cached record."""
    start = run.start_time
    if start is None or start >= t_to:
        return False
    if run.stop is None:
        return start >= t_to - horizon_s
    stop = run.stop_time
    return stop is not None and stop >= t_from


def _prune(state: SourceState) -> None:
    """Bound the stale-display cache: keep the most recently observed runs."""
    if len(state.runs) <= MAX_CACHED_RUNS:
        return
    keep = sorted(
        state.runs.items(), key=lambda kv: (kv[1][1], kv[1][0].start_time or 0.0), reverse=True
    )[: int(MAX_CACHED_RUNS * 0.9)]
    state.runs = dict(keep)
