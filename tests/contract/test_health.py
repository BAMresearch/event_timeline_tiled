"""The adapter's /health against a real in-process Tiled."""

from typing import Any

import tiled
from fastapi.testclient import TestClient

from event_timeline_tiled.adapter import create_app
from event_timeline_tiled.settings import Settings


def _health(url: str, key: str, **kw: Any) -> list[dict[str, Any]]:
    with TestClient(create_app(Settings(tiled_url=url, tiled_api_key=key, **kw))) as client:
        response = client.get("/health")
    assert response.status_code == 200
    rows: list[dict[str, Any]] = response.json()
    return rows


def test_health_ok(tiled_server: Any, tiled_base_url: str) -> None:
    [row] = _health(tiled_base_url, tiled_server.api_key)
    assert row["status"] == "ok" and row["ok"] is True
    assert row["tiled_version"] == tiled.__version__
    assert row["root"] == "/"


def test_health_wrong_key_is_unauthorised(tiled_base_url: str) -> None:
    [row] = _health(tiled_base_url, "not-the-key")
    assert row["status"] == "unauthorised" and row["ok"] is False
    assert "not-the-key" not in str(row)


def test_health_unreachable() -> None:
    [row] = _health("http://127.0.0.1:9", "k", request_timeout_s=2)
    assert row["status"] == "unreachable"


def test_health_one_row_per_root(tiled_server: Any, tiled_base_url: str) -> None:
    rows = _health(tiled_base_url, tiled_server.api_key, catalog_roots=("", "missing-root"))
    assert [(r["root"], r["status"]) for r in rows] == [("/", "ok"), ("missing-root", "not_found")]
