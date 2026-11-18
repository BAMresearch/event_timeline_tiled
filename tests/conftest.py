"""Shared fixtures: in-process Tiled servers seeded with the window scenario.

- ``tiled_server``: SQLite catalogue (Tiled's ``SimpleTiledServer``).
- ``catalog_server``: parametrised over SQLite and PostgreSQL, for the search
  contract. PostgreSQL comes from ``ETT_TEST_POSTGRES_URI`` (an admin URL such
  as ``postgresql://user:password@host:5432/postgres``) or, without it, from a
  throwaway Docker container. If neither is available those tests **fail**;
  deselect them explicitly with ``-m "not postgres"``.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest

POSTGRES_IMAGE = (
    "docker.io/library/postgres:16"
    "@sha256:dd2bf3ce2dc83a81674cf7f217fcfd8a5009a4b325dda5b843bfee82a06cff6f"
)


def _container_cli() -> str | None:
    """``ETT_CONTAINER_CLI`` if set, else the first of ``docker`` and
    ``podman`` that answers ``info`` (the two accept the same commands here)."""
    chosen = os.environ.get("ETT_CONTAINER_CLI")
    for cli in [chosen] if chosen else ["docker", "podman"]:
        if shutil.which(cli) and not subprocess.run([cli, "info"], capture_output=True).returncode:
            return cli
    return None


T0 = 1_700_000_000.0
# Tiled 0.2.21's build_app() writes the single-user API key into the
# process-wide cached settings (tiled.server.settings.get_settings), so all
# in-process servers of a test session must share one key.
TEST_API_KEY = secrets.token_hex(16)


class CatalogServer:
    """A Tiled server on a background thread with the given catalogue URI.

    Like ``tiled.server.SimpleTiledServer`` (Tiled 0.2.21), but with any
    catalogue URI, so that the same tests run against PostgreSQL. Tables go to
    SQLite unless ``table_storage`` says otherwise; not to DuckDB, as in
    ``SimpleTiledServer``, whose single shared connection fails reads made at
    the same time (one database engine fewer, and no flaky tests).
    """

    def __init__(
        self, backend: str, catalog_uri: str, directory: Path, table_storage: str | None = None
    ) -> None:
        import uvicorn
        from tiled.catalog import from_uri as catalog_from_uri
        from tiled.config import Authentication, StreamingCacheConfig
        from tiled.server.app import build_app
        from tiled.server.simple import ThreadedServer

        self.backend = backend
        self.api_key = TEST_API_KEY
        self.processed_dir = directory / "processed"
        self.processed_dir.mkdir(parents=True)
        (directory / "data").mkdir()
        catalog = catalog_from_uri(
            catalog_uri,
            writable_storage=[
                directory / "data",
                table_storage or f"sqlite:///{directory / 'tables.sqlite'}",
            ],
            readable_storage=[self.processed_dir],
            init_if_not_exists=True,
            cache_config=StreamingCacheConfig(uri="memory").model_dump(),
        )
        app = build_app(catalog, authentication=Authentication(single_user_api_key=self.api_key))
        self._server = ThreadedServer(
            uvicorn.Config(app, port=0, loop="asyncio", log_level="warning")
        )
        self._cm = self._server.run_in_thread()
        base = self._cm.__enter__()
        self.url = base.rstrip("/")
        _wait_http(f"{self.url}/healthz")

    def close(self) -> None:
        self._cm.__exit__(None, None, None)


def _wait_http(url: str, timeout_s: float = 60) -> None:
    import httpx

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise TimeoutError(f"{url} did not become ready")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _create_database(admin_url: str, name: str) -> None:
    import asyncpg

    deadline = time.monotonic() + 60
    while True:
        try:
            conn = await asyncpg.connect(admin_url, timeout=5)
            break
        except OSError, asyncpg.PostgresError:
            if time.monotonic() > deadline:
                raise
            await asyncio.sleep(0.5)
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def postgres_admin_url() -> Iterator[str]:
    url = os.environ.get("ETT_TEST_POSTGRES_URI")
    if url:
        yield url
        return
    cli = _container_cli()
    if cli is None:
        pytest.fail(
            "PostgreSQL tests need ETT_TEST_POSTGRES_URI, or Docker or Podman "
            "(ETT_CONTAINER_CLI chooses); to leave them out on purpose, run "
            'pytest -m "not postgres"'
        )
    password, port = secrets.token_hex(16), _free_port()
    container = subprocess.run(
        [cli, "run", "-d", "--rm", "-e", f"POSTGRES_PASSWORD={password}",
         "-p", f"127.0.0.1:{port}:5432", POSTGRES_IMAGE],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    try:
        yield f"postgresql://postgres:{password}@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run([cli, "stop", container], capture_output=True)


def _postgres_databases(admin_url: str) -> tuple[str, str]:
    """A fresh catalogue database and a fresh event-table database, as in the
    demo stack (table rows then come back in storage order, not write order)."""
    parts = urlsplit(admin_url)
    names = [f"ett_test_{secrets.token_hex(4)}" for _ in range(2)]
    for name in names:
        asyncio.run(_create_database(admin_url, name))
    return (
        urlunsplit(("postgresql+asyncpg", parts.netloc, f"/{names[0]}", "", "")),
        urlunsplit(("postgresql", parts.netloc, f"/{names[1]}", "", "")),
    )


def _seed(url: str, api_key: str, processed: Path) -> dict[str, Any]:
    from tiled.client import from_uri

    from event_timeline_tiled.testing import FixtureWriter, window_scenario

    return window_scenario(FixtureWriter(from_uri(url, api_key=api_key), processed), T0)


@pytest.fixture(scope="session")
def tiled_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[CatalogServer]:
    """An empty SQLite-backed Tiled server, for tests that write their own runs."""
    directory = tmp_path_factory.mktemp("tiled")
    server = CatalogServer("sqlite", f"sqlite:///{directory / 'catalog.db'}", directory)
    try:
        yield server
    finally:
        server.close()


@pytest.fixture(scope="session")
def tiled_base_url(tiled_server: CatalogServer) -> str:
    parts = urlsplit(tiled_server.url)
    return f"{parts.scheme}://{parts.netloc}"


@pytest.fixture(scope="session")
def window_manifest(tiled_server: CatalogServer, tiled_base_url: str) -> dict[str, Any]:
    return _seed(tiled_base_url, tiled_server.api_key, Path(tiled_server.processed_dir))


@pytest.fixture(
    scope="session",
    params=["sqlite", pytest.param("postgresql", marks=pytest.mark.postgres)],
)
def catalog_server(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[CatalogServer, dict[str, Any]]]:
    """A Tiled server per catalogue backend, seeded with the window scenario."""
    directory = tmp_path_factory.mktemp(f"catalog-{request.param}")
    tables = None
    if request.param == "postgresql":
        uri, tables = _postgres_databases(request.getfixturevalue("postgres_admin_url"))
    else:
        uri = f"sqlite:///{directory / 'catalog.db'}"
    server = CatalogServer(request.param, uri, directory, tables)
    try:
        yield server, _seed(server.url, server.api_key, server.processed_dir)
    finally:
        server.close()
