"""Unfolding one lane into its streams (presets.md §6.5), against seeded
Tiled servers (SQLite and PostgreSQL)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from event_timeline_tiled.adapter import create_app
from event_timeline_tiled.settings import Settings

Api = tuple[TestClient, dict[str, Any]]


def _app(server: Any, manifest: dict[str, Any], **settings: Any) -> TestClient:
    config = Settings(tiled_url=server.url, tiled_api_key=server.api_key, **settings)
    now = manifest["window"][1]
    return TestClient(create_app(config, clock=lambda: now, data_cache_s=0))


@pytest.fixture(scope="module")
def api(catalog_server: tuple[Any, dict[str, Any]]) -> Iterator[Api]:
    server, manifest = catalog_server
    with _app(server, manifest) as client:
        yield client, manifest


def _window(manifest: dict[str, Any]) -> dict[str, Any]:
    t_from, t_to = manifest["window"]
    return {"from": t_from, "to": t_to}


def _lane(client: TestClient, manifest: dict[str, Any], name: str) -> str:
    rows = client.get("/clips/frame", params=_window(manifest)).json()
    return str(next(r["lane_id"] for r in rows if r["lane"] == name))


def test_a_measurement_lane_unfolds_into_its_streams(api: Api) -> None:
    client, manifest = api
    lane = _lane(client, manifest, "MOUSE · acquisition")
    rows = client.get("/clips/frame", params={**_window(manifest), "unfold": lane}).json()
    inside = manifest["runs"]["inside"]["uid"]
    bars = {r["stream"]: r for r in rows if r["uid"] == inside}
    assert {"baseline", "mouse_eiger_measure"} <= set(bars)
    for stream, bar in bars.items():
        assert bar["lane"].startswith(f"MOUSE · acquisition · {stream}"), stream
        assert bar["clip_from_ms"] <= bar["stream_from_ms"] <= bar["stream_to_ms"]
        assert bar["stream_to_ms"] <= bar["clip_to_ms"] and bar["lane_id"] == lane
        assert bar["label"].endswith(f" · {stream}") and bar["state"] == "success"
    # The baseline spans the run (readings at its start and end); the detector less.
    assert bars["baseline"]["stream_to_ms"] - bars["baseline"]["stream_from_ms"] >= (
        bars["mouse_eiger_measure"]["stream_to_ms"] - bars["mouse_eiger_measure"]["stream_from_ms"]
    )
    # Other lanes stay folded.
    generic = [r for r in rows if r["lane"].startswith("generic")]
    assert generic and all(r["stream"] is None for r in generic)
    status = client.get("/clips/status", params={**_window(manifest), "unfold": lane}).json()
    unfold = status["unfold"]
    assert unfold["found"] and unfold["lane"] == lane and unfold["runs_unfolded"] > 0
    assert unfold["streams"].get("drawn", 0) >= 2


def test_open_runs_unfold_to_their_last_event_not_to_now(api: Api) -> None:
    client, manifest = api
    lane = _lane(client, manifest, "MOUSE · acquisition")
    rows = client.get("/clips/frame", params={**_window(manifest), "unfold": lane}).json()
    open_bars = [r for r in rows if r["uid"] == manifest["runs"]["open_inside"]["uid"]
                 and r["stream"]]  # fmt: skip
    for bar in open_bars:
        assert bar["stream_to_ms"] < bar["clip_to_ms"]  # its last event, not the window's end


def test_processing_streams_with_only_external_arrays_unfold(api: Api) -> None:
    client, manifest = api
    lane = _lane(client, manifest, "MOUSE · processing")
    rows = client.get("/clips/frame", params={**_window(manifest), "unfold": lane}).json()
    ok = manifest["runs"]["proc_inside_ok"]["uid"]
    streams = {r["stream"] for r in rows if r["uid"] == ok}
    assert streams == {"corrected_1d", "corrected_2d"}
    one = next(r for r in rows if r["uid"] == ok and r["stream"] == "corrected_1d")
    assert one["stream_from_ms"] == one["stream_to_ms"]  # a single event
    assert one["stop"] > one["start"]  # widened for visibility only
    # A failed attempt with no outputs has nothing to draw: it stays folded.
    failed = manifest["runs"]["proc_inside_failed"]["uid"]
    [folded] = [r for r in rows if r["uid"] == failed]
    assert folded["stream"] is None and folded["lane"].endswith(" · earlier runs")


def test_the_run_budget_leaves_earlier_runs_folded(catalog_server: tuple[Any, Any]) -> None:
    server, manifest = catalog_server
    with _app(server, manifest, unfold_max_runs=1) as client:
        lane = _lane(client, manifest, "MOUSE · acquisition")
        params = {**_window(manifest), "unfold": lane}
        rows = client.get("/clips/frame", params=params).json()
        unfolded = {r["uid"] for r in rows if r["lane_id"] == lane and r["stream"]}
        earlier = {r["uid"] for r in rows if r["lane"].startswith("MOUSE · acquisition · earlier")}
        assert len(unfolded) == 1 and earlier and not unfolded & earlier
        status = client.get("/clips/status", params=params).json()
        assert status["unfold"]["runs_unfolded"] == 1 and status["unfold"]["runs_folded"] > 0
        assert any("only the latest 1 runs are unfolded" in i["message"] for i in status["issues"])


def test_an_unknown_lane_is_a_notice_not_an_error(api: Api) -> None:
    client, manifest = api
    params = {**_window(manifest), "unfold": "instruments.runs.000000000000"}
    rows = client.get("/clips/frame", params=params)
    assert rows.status_code == 200 and all(r["stream"] is None for r in rows.json())
    status = client.get("/clips/status", params=params).json()
    assert status["unfold"]["found"] is False
    assert any("not in this range" in i["message"] for i in status["issues"])
    folded = client.get("/clips/frame", params={**_window(manifest), "unfold": ""})
    assert folded.status_code == 200
