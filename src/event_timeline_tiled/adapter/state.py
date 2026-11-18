"""What the endpoint modules share at run time: settings, the Tiled reader,
the timeline, queue status, the presets and the clock; finding a run, its
views, and a preset's arrangement of a window."""

from __future__ import annotations

import json
from collections.abc import Callable

from attrs import frozen
from fastapi import HTTPException

from ..lanes import Arrangement, arrange
from ..layout import Layout, RunRecord
from ..presets import Preset, Registry, load_registry
from ..queues import ApiFactory, QueueMonitor
from ..run_state import role_of
from ..settings import Settings
from ..tiled_http import SourceError, TiledReader
from ..timeline import Timeline, WindowResult
from ..views import ResolvedView, resolve
from .common import AMBIGUOUS, unselected, upstream


@frozen
class Selected:
    """A selection: the run, or why there is none (a notice)."""

    run: RunRecord | None
    notice: str


class Adapter:
    """One per application; endpoint modules hold their own caches."""

    def __init__(
        self,
        settings: Settings,
        reader: TiledReader | None,
        clock: Callable[[], float],
        queue_api: ApiFactory | None,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.tiled = reader or TiledReader(settings)
        self.layout = Layout(self.tiled)
        self.timeline = Timeline(settings, self.layout, clock)
        # Every preset file is loaded now: a bad one stops the adapter.
        self.presets: Registry = load_registry(
            settings.presets_dir, self.timeline.roots, settings.default_preset, settings.max_lanes
        )
        self.queues = (
            QueueMonitor(settings.queues, queue_api, clock)
            if queue_api
            else QueueMonitor(settings.queues, clock=clock)
        )

    def close(self) -> None:
        self.queues.close()
        self.tiled.close()

    def root(self, root: str | None) -> str | None:
        """A configured root from a request (``/`` is the server root), or 422."""
        if root is None:
            return None
        value = root.strip("/")
        if value not in self.timeline.roots:
            raise HTTPException(422, "root is not a configured catalogue root")
        return value

    def find(self, uid: str, root: str | None = None) -> RunRecord:
        """The run stored under ``uid`` (in ``root``, if given), or ``404``; a key
        under several roots without ``root`` is ``409``, never a guess."""
        chosen = self.root(root)
        try:
            found = self.layout.find_runs(self.timeline.roots if chosen is None else (chosen,), uid)
        except SourceError as exc:
            raise upstream(exc) from None
        if not found:
            raise HTTPException(404, "no such run under the configured catalogue roots")
        if len(found) > 1:
            raise HTTPException(409, "the run key is under several catalogue roots: give root")
        return found[0]

    def selection(self, uid: str, root: str | None = None) -> Selected:
        """The selected run; none if nothing is selected, Tiled does not have it
        (a bookmark, a shared link, a run from another server) or its key is
        ambiguous across roots."""
        if not uid:
            return Selected(None, unselected(uid))
        try:
            return Selected(self.find(uid, root), "")
        except HTTPException as exc:
            if exc.status_code == 404:
                return Selected(None, unselected(uid))
            if exc.status_code == 409:
                return Selected(None, AMBIGUOUS)
            raise

    def views_of(self, run: RunRecord) -> list[ResolvedView]:
        try:
            return resolve(run, role_of(run.start), self.layout.streams(run))
        except SourceError as exc:
            raise upstream(exc) from None

    def preset(self, preset_id: str | None) -> Preset:
        """The requested preset, the default without one; ``422`` if unknown."""
        try:
            return self.presets.get(preset_id or self.presets.default)
        except KeyError:
            raise HTTPException(422, "no such preset (see /presets)") from None

    def arrange(
        self, preset: Preset, t_from: float, t_to: float
    ) -> tuple[WindowResult, Arrangement]:
        """``preset`` applied to the window: its roots and scope narrow the
        search (scope only as a candidate set), then every run is checked."""
        scope = [(".".join(leaf.path), tuple(leaf.value) if leaf.op == "in" else (leaf.value,))
                 for leaf in preset.scope]  # fmt: skip
        scope_key = json.dumps(sorted(scope, key=str), sort_keys=True, default=str) if scope else ""
        result = self.timeline.window(t_from, t_to, preset.roots, scope, scope_key)
        now = self.clock()
        return result, arrange(
            preset,
            result.clips,
            result.records,
            now,
            lambda run: self.timeline.streams_of(run, now),
            self.settings.max_lanes,
            self.settings.max_placements,
        )
