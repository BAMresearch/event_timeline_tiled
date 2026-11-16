"""FastAPI application. Step 2 serves ``/health`` only; the clip and run endpoints
of the plan (section 6.1) arrive in step 3."""

from __future__ import annotations

import argparse
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from .. import __version__
from ..settings import Settings
from ..tiled_http import HealthStatus, TiledReader


def create_app(settings: Settings, reader: TiledReader | None = None) -> FastAPI:
    tiled = reader or TiledReader(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        tiled.close()

    app = FastAPI(title="event-timeline-tiled adapter", version=__version__, lifespan=lifespan)

    @app.get("/health")
    def health() -> list[dict[str, Any]]:
        """One row per catalogue root, flat for Grafana tables."""
        checked_at = time.time()
        version = tiled.server_version()
        rows = []
        for root in settings.catalog_roots or ("",):
            check = tiled.check_root(root)
            rows.append(
                {
                    "component": "tiled",
                    "root": check.root or "/",
                    "status": check.status.value,
                    "ok": check.status is HealthStatus.OK,
                    "latency_ms": check.latency_ms,
                    "detail": check.detail,
                    "tiled_version": version,
                    "adapter_version": __version__,
                    "checked_at": checked_at,
                }
            )
        return rows

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the timeline adapter.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(Settings.from_env()), host=args.host, port=args.port)
