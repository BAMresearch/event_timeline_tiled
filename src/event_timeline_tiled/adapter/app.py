"""FastAPI application: the adapter's endpoints (plan §6.1).

Times are epoch seconds, except the Grafana time fields of ``/clips/frame``
and of data rows (milliseconds). Every endpoint validates its inputs and
states truncation. Responses never contain the Tiled credential or raw
upstream bodies. The endpoint contract is documented in
``docs/technical/endpoints.md``.

The endpoints live in one module per area, each with its own caches:
:mod:`.overview` (health, queues, the window's runs), :mod:`.runs` (one
run), :mod:`.trends`, :mod:`.results` (processing results) and
:mod:`.selection` (the dashboard's selection); :mod:`.state` holds what they
share.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .. import __version__
from ..queues import ApiFactory
from ..settings import Settings
from ..tiled_http import TiledReader
from . import overview, results, runs, selection, trends
from .runs import DATA_CACHE_S
from .state import Adapter


def create_app(
    settings: Settings,
    reader: TiledReader | None = None,
    clock: Callable[[], float] = time.time,
    queue_api: ApiFactory | None = None,
    data_cache_s: float = DATA_CACHE_S,
) -> FastAPI:
    adapter = Adapter(settings, reader, clock, queue_api)
    found = results.Results(adapter)
    one_run = runs.Runs(adapter, found, data_cache_s)
    trend = trends.Trends(adapter)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            adapter.close()

    app = FastAPI(title="event-timeline-tiled adapter", version=__version__, lifespan=lifespan)
    app.include_router(overview.router(adapter))
    app.include_router(runs.router(adapter, one_run))
    app.include_router(trends.router(adapter, trend))
    app.include_router(results.router(adapter, found))
    app.include_router(selection.router(adapter, one_run, trend, found))
    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the timeline adapter.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(Settings.from_env()), host=args.host, port=args.port)
