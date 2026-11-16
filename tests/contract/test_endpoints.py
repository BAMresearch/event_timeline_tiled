"""The adapter's endpoints against seeded Tiled servers (SQLite and PostgreSQL)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from event_timeline_tiled.adapter import create_app
from event_timeline_tiled.settings import Settings

Server = tuple[Any, dict[str, Any]]


@pytest.fixture(scope="module")
def api(catalog_server: Server) -> Iterator[tuple[TestClient, dict[str, Any]]]:
    server, manifest = catalog_server
    settings = Settings(tiled_url=server.url, tiled_api_key=server.api_key)
    now = manifest["window"][1]  # "now" is the end of the window
    with TestClient(create_app(settings, clock=lambda: now)) as client:
        yield client, manifest


def _window(manifest: dict[str, Any]) -> dict[str, float]:
    t_from, t_to = manifest["window"]
    return {"from": t_from, "to": t_to}


def test_clips_match_the_window_and_statuses(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, manifest = api
    body = client.get("/clips", params=_window(manifest)).json()
    assert body["truncated"] is False and body["sources"][0]["ok"] is True
    by_uid = {c["uid"]: c for c in body["clips"]}
    for case, expected in manifest["runs"].items():
        assert (expected["uid"] in by_uid) == expected["expect_in_window"], case
        if expected["uid"] in by_uid:
            assert by_uid[expected["uid"]]["status"] == expected["expect_status"], case


def test_clip_fields(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, manifest = api
    runs = manifest["runs"]
    clips = {c["uid"]: c for c in client.get("/clips", params=_window(manifest)).json()["clips"]}
    inside = clips[runs["inside"]["uid"]]
    # MOUSE labels: sampleid, then plan_name (the fixture plan function is called "plan").
    assert (inside["lane"], inside["role"], inside["label"]) == (
        "MOUSE",
        "acquisition",
        "S-inside · plan",
    )
    assert clips[runs["proc_inside_ok"]["uid"]]["lane"] == "MOUSE · processing"
    assert clips[runs["generic_inside"]["uid"]]["lane"] == "generic A"
    assert clips[runs["no_instrument"]["uid"]]["lane"] == "(unknown)"
    mismatch = clips[runs["uid_mismatch"]["uid"]]
    assert (
        mismatch["uid_mismatch"]
        and mismatch["start_uid"] == runs["uid_mismatch"]["expect_start_uid"]
    )
    # 200 s after its last event: not stalled (MOUSE allows 20 minutes).
    open_inside = clips[runs["open_inside"]["uid"]]
    assert open_inside["last_event"] is not None and open_inside["possibly_stalled"] is False
    # Open for 20 days without events in the window: possibly stalled, still "open".
    old = clips[runs["open_20d"]["uid"]]
    assert (old["status"], old["possibly_stalled"]) == ("open", True)


def test_frame_has_final_lanes_in_milliseconds(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, manifest = api
    rows = client.get("/clips/frame", params=_window(manifest)).json()
    assert list(rows[0]) == [
        "lane", "start", "stop", "state", "status", "uid", "label",
        "clip_from_ms", "clip_to_ms", "possibly_stalled", "stale",
    ]  # fmt: skip
    window_end_ms = manifest["window"][1] * 1000
    by_uid = {r["uid"]: r for r in rows}
    open_row = by_uid[manifest["runs"]["open_inside"]["uid"]]
    assert open_row["stop"] == window_end_ms and open_row["state"] == "open"
    assert by_uid[manifest["runs"]["open_20d"]["uid"]]["state"] == "possibly stalled"
    lanes = [r["lane"] for r in rows]
    assert lanes.index("MOUSE") < lanes.index("MOUSE · processing")
    # Overlapping MOUSE runs are spread over sub-lanes; every run is present.
    assert any(lane.startswith("MOUSE /") for lane in lanes)
    assert len(rows) == sum(1 for c in manifest["runs"].values() if c["expect_in_window"])


@pytest.mark.parametrize(
    "params",
    [
        {"from": 2, "to": 1},
        {"from": 1, "to": 1},
        {"from": "nan", "to": 1},
        {"from": 0, "to": 500 * 86400},
        {"from": 1},
    ],
)
def test_bad_windows_are_rejected(
    api: tuple[TestClient, dict[str, Any]], params: dict[str, Any]
) -> None:
    client, _ = api
    assert client.get("/clips", params=params).status_code == 422
    assert client.get("/clips/frame", params=params).status_code == 422


def test_run_summary(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, manifest = api
    body = client.get(f"/runs/{manifest['runs']['aborted']['uid']}").json()
    run = body["run"]
    assert (run["status"], run["lane"], run["duration_s"]) == ("aborted", "MOUSE", 10.0)
    assert run["reason"] == "synthetic abort"
    keys = {(r["document"], r["key"]) for r in body["metadata"]}
    assert {("start", "sampleid"), ("stop", "exit_status"), ("stop", "num_events.baseline")} <= keys
    assert client.get("/runs/no-such-run").status_code == 404
    assert client.get("/runs/bad%2Fuid").status_code in {404, 422}


def test_streams_and_fields(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, manifest = api
    uid = manifest["runs"]["inside"]["uid"]
    streams = {s["stream"]: s for s in client.get(f"/runs/{uid}/streams").json()}
    assert streams["mouse_eiger_measure"]["events"] == 3
    assert streams["mouse_eiger_measure"]["events_source"] == "stop document"
    fields = {
        f["field"]: f for f in client.get(f"/runs/{uid}/streams/mouse_eiger_measure/fields").json()
    }
    assert {"roi", "stats"} <= set(fields)
    assert client.get(f"/runs/{uid}/streams/nope/fields").status_code == 404


def test_mouse_readings_view(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, manifest = api
    uid = manifest["runs"]["inside"]["uid"]
    views = client.get(f"/runs/{uid}/views").json()
    assert views["default"] == "readings"
    data = client.get(f"/runs/{uid}/data").json()
    assert data["view"] == "readings" and not data["truncated"]
    measure = [r for r in data["rows"] if r["stream"] == "mouse_eiger_measure"]
    assert sorted({r["seq_num"] for r in measure}) == [1, 2, 3]
    assert {r["field"] for r in measure} == {"roi", "stats"}
    summary = client.get(f"/runs/{uid}/data", params={"view": "summary"}).json()
    assert any(r["key"] == "sampleid" for r in summary["rows"])
    assert client.get(f"/runs/{uid}/data", params={"view": "nope"}).status_code == 404
    assert client.get(f"/runs/{uid}/data", params={"view": "Bad!"}).status_code == 422


def test_generic_fallback_time_series(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, manifest = api
    uid = manifest["runs"]["generic_inside"]["uid"]
    views = client.get(f"/runs/{uid}/views").json()
    # The simulated signal is not hinted, so the chain starts at "numeric".
    assert views["default"] == "numeric"
    assert "hinted" not in [v["name"] for v in views["views"]]
    data = client.get(f"/runs/{uid}/data", params={"max_points": 10}).json()
    assert data["fields"] == ["env_temperature"] and len(data["rows"]) == 5
    assert data["decimation"]["every"] == 1 and data["rows"][0]["time"] > 1e12  # milliseconds


def test_processing_run_has_no_plottable_data_yet(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, manifest = api
    views = client.get(f"/runs/{manifest['runs']['proc_inside_ok']['uid']}/views").json()
    assert views["default"] == "summary"
    assert views["views"][0] == {**views["views"][0], "name": "no_data", "available": False}


def test_health_reports_fetch_state(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, manifest = api
    client.get("/clips", params=_window(manifest))
    [row] = client.get("/health").json()
    assert row["status"] == "ok" and row["last_success"] is not None and row["last_error"] is None
