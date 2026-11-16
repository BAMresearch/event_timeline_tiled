"""The timeline service: window searches per root, run state, caching and
source health (plan §5). The adapter's endpoints call this; it reads Tiled
only through :class:`~event_timeline_tiled.layout.Layout`.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from attrs import define, field, frozen

from .clips import Clip, make_clip
from .layout import Layout, RunRecord
from .run_state import Status, activity, status_of
from .settings import Settings
from .tiled_http import HealthStatus, SourceError
from .views import label, stall_after_s


@define
class SourceState:
    """What is known about fetching one catalogue root (plan §5.3)."""

    root: str
    last_attempt: float | None = None
    last_success: float | None = None
    error: HealthStatus | None = None
    detail: str = ""
    runs: dict[str, RunRecord] = field(factory=dict)
    """Runs from the last successful fetch, kept to show stale data on failure."""


@frozen
class WindowResult:
    clips: list[Clip]
    truncated: bool
    unplaceable: list[str]
    """Keys of runs found without a usable start time (reported, not drawn)."""
    sources: list[SourceState]
    open_until: float
    """End of open clips: the oldest last successful fetch among the roots
    that returned runs (normally now)."""


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
        self._last_event: dict[tuple[str, str], tuple[float, float | None]] = {}

    def sources(self) -> list[SourceState]:
        with self._lock:
            return [self._sources[r] for r in self.roots]

    def window(self, t_from: float, t_to: float) -> WindowResult:
        now = self.clock()
        horizon = self.settings.open_run_horizon_days * 86400
        clips: list[Clip] = []
        unplaceable: list[str] = []
        truncated = False
        successes: list[float] = []
        for root in self.roots:
            state = self._sources[root]
            stale = False
            try:
                runs, cut = self.layout.window_runs(
                    root, t_from, t_to, horizon, self.settings.max_runs
                )
                truncated |= cut
                with self._lock:
                    state.last_attempt = state.last_success = now
                    state.error, state.detail = None, ""
                    # Keep the latest record of every run seen, for stale display.
                    state.runs.update({r.key: r for r in runs})
                    _prune(state, t_to - horizon)
                successes.append(now)
            except SourceError as exc:
                with self._lock:
                    state.last_attempt = now
                    state.error, state.detail = exc.status, exc.detail
                    runs = [r for r in state.runs.values() if _overlaps(r, t_from, t_to)]
                stale = True
                if state.last_success is not None:
                    successes.append(state.last_success)
            for run in runs:
                clip = self._clip(run, now, stale)
                if clip is None:
                    unplaceable.append(run.key)
                else:
                    clips.append(clip)
        return WindowResult(
            clips=clips,
            truncated=truncated,
            unplaceable=unplaceable,
            sources=self.sources(),
            open_until=min(successes) if successes else now,
        )

    def _clip(self, run: RunRecord, now: float, stale: bool) -> Clip | None:
        status = status_of(run.stop)
        last = self.last_event(run, now) if status is Status.OPEN and not stale else None
        if stale and status is Status.OPEN:
            last = self._last_event.get((run.root, run.key), (0.0, None))[1]
        act = activity(status, run.start_time, last, now, stall_after_s(run))
        return make_clip(run, act, stale, label(run))

    def last_event(self, run: RunRecord, now: float) -> float | None:
        """Last event of an open run, read at most every ``activity_refresh_s``
        (it costs a whole ``time`` column per stream, plan §5.2)."""
        key = (run.root, run.key)
        with self._lock:
            cached = self._last_event.get(key)
        if cached is not None and now - cached[0] < self.settings.activity_refresh_s:
            return cached[1]
        try:
            value = self.layout.last_event_time(run)
        except SourceError:
            return cached[1] if cached else None
        with self._lock:
            self._last_event[key] = (now, value)
        return value


def _overlaps(run: RunRecord, t_from: float, t_to: float) -> bool:
    start = run.start_time
    if start is None or start >= t_to:
        return False
    stop = run.stop_time
    return run.stop is None or (stop is not None and stop >= t_from)


def _prune(state: SourceState, oldest_open_start: float) -> None:
    """Bound the stale-display cache: drop runs that can no longer be shown."""
    if len(state.runs) <= 50_000:
        return
    state.runs = {k: r for k, r in state.runs.items() if (r.start_time or 0.0) >= oldest_open_start}
