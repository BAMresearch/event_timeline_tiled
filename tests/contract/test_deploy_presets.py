"""The presets in ``deploy/presets/`` load, and do what their comments say,
against seeded Tiled servers (SQLite and PostgreSQL)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from event_timeline_tiled.adapter import create_app
from event_timeline_tiled.settings import Settings

Api = tuple[TestClient, dict[str, Any]]
DEPLOY_PRESETS = Path(__file__).parents[2] / "deploy" / "presets"


@pytest.fixture(scope="module")
def api(catalog_server: tuple[Any, dict[str, Any]]) -> Iterator[Api]:
    server, manifest = catalog_server
    settings = Settings(
        tiled_url=server.url, tiled_api_key=server.api_key, presets_dir=DEPLOY_PRESETS
    )
    now = manifest["window"][1]
    with TestClient(create_app(settings, clock=lambda: now, data_cache_s=0)) as client:
        yield client, manifest


def _window(manifest: dict[str, Any], **params: Any) -> dict[str, Any]:
    t_from, t_to = manifest["window"]
    return {"from": t_from, "to": t_to, **params}


def test_every_deployment_preset_loads(api: Api) -> None:
    client, _ = api
    listed = {p["id"] for p in client.get("/presets").json()}
    assert {p.stem for p in DEPLOY_PRESETS.glob("*.yaml")} <= listed


def test_electrochemistry_shows_all_activity_on_a_station(api: Api) -> None:
    client, manifest = api
    runs = manifest["runs"]
    cases = ("electrochemistry", "electrochemistry_next", "electrochemistry_other_plan")
    echem = {runs[c]["uid"] for c in cases}
    params = _window(manifest, preset="electrochemistry")
    rows = client.get("/clips/frame", params=params).json()
    drawn = [r for r in rows if r["uid"]]
    # Every electrochemistry run, and nothing else, on the station's lane.
    assert {r["uid"] for r in drawn} == echem
    assert {r["lane"] for r in drawn} == {"simulator"}
    # The trend follows the run sheets on that station, not the calibration.
    first = runs["electrochemistry"]["uid"]
    trend = client.get(
        "/selection/trend", params={**params, "uid": first, "lane": drawn[0]["lane_id"]}
    ).json()
    assert trend["runs"]["like"] == 2 and "run_sequence" in trend["notice"]
