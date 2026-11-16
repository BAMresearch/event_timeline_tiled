"""Shared fixtures: an in-process Tiled (SQLite) seeded with the window scenario.

PostgreSQL contract tests join in step 3, with the search logic.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest


@pytest.fixture(scope="session")
def tiled_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Any]:
    from tiled.server import SimpleTiledServer

    root = tmp_path_factory.mktemp("tiled")
    processed = root / "processed"
    processed.mkdir()
    server = SimpleTiledServer(directory=root / "server", readable_storage=[processed])
    server.processed_dir = processed  # type: ignore[attr-defined]
    try:
        yield server
    finally:
        server.close()


@pytest.fixture(scope="session")
def tiled_base_url(tiled_server: Any) -> str:
    parts = urlsplit(tiled_server.uri)
    return f"{parts.scheme}://{parts.netloc}"


@pytest.fixture(scope="session")
def window_manifest(tiled_server: Any, tiled_base_url: str) -> dict[str, Any]:
    from tiled.client import from_uri

    from event_timeline_tiled.testing import FixtureWriter, window_scenario

    client = from_uri(tiled_base_url, api_key=tiled_server.api_key)
    fw = FixtureWriter(
        client,
        Path(tiled_server.processed_dir),
    )
    return window_scenario(fw, 1_700_000_000.0)
