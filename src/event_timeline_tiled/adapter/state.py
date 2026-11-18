"""What the endpoint modules share at run time: settings, the Tiled reader,
the timeline, queue status and the clock, and finding a run and its views."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException

from ..layout import Layout, RunRecord
from ..queues import ApiFactory, QueueMonitor
from ..run_state import role_of
from ..settings import Settings
from ..tiled_http import SourceError, TiledReader
from ..timeline import Timeline
from ..views import ResolvedView, resolve
from .common import upstream


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
        self.queues = (
            QueueMonitor(settings.queues, queue_api, clock)
            if queue_api
            else QueueMonitor(settings.queues, clock=clock)
        )

    def close(self) -> None:
        self.queues.close()
        self.tiled.close()

    def find(self, uid: str) -> RunRecord:
        """The run stored under ``uid``, or ``404``."""
        try:
            run = self.layout.find_run(self.timeline.roots, uid)
        except SourceError as exc:
            raise upstream(exc) from None
        if run is None:
            raise HTTPException(404, "no such run under the configured catalogue roots")
        return run

    def selection(self, uid: str) -> RunRecord | None:
        """The selected run; ``None`` if nothing is selected or Tiled does not
        have it (a bookmark, a shared link, a run from another server)."""
        try:
            return self.find(uid) if uid else None
        except HTTPException as exc:
            if exc.status_code == 404:
                return None
            raise

    def views_of(self, run: RunRecord) -> list[ResolvedView]:
        try:
            return resolve(run, role_of(run.start), self.layout.streams(run))
        except SourceError as exc:
            raise upstream(exc) from None
