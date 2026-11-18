"""The adapter's endpoints against seeded Tiled servers (SQLite and PostgreSQL)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from event_timeline_tiled.adapter import create_app
from event_timeline_tiled.adapter import runs as adapter_runs
from event_timeline_tiled.settings import Settings

Server = tuple[Any, dict[str, Any]]


@pytest.fixture(scope="module")
def api(catalog_server: Server) -> Iterator[tuple[TestClient, dict[str, Any]]]:
    server, manifest = catalog_server
    settings = Settings(tiled_url=server.url, tiled_api_key=server.api_key)
    now = manifest["window"][1]  # "now" is the end of the window
    with TestClient(create_app(settings, clock=lambda: now, data_cache_s=0)) as client:
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
        "MOUSE · acquisition",
        "acquisition",
        "S-inside · plan",
    )
    assert inside["lane_key"].startswith("instruments.runs.")
    assert [(p["preset"], p["track"], p["lane"]) for p in inside["placements"]] == [
        ("instruments", "runs", "MOUSE · acquisition")
    ]
    assert clips[runs["proc_inside_ok"]["uid"]]["lane"] == "MOUSE · processing"
    assert clips[runs["generic_inside"]["uid"]]["lane"] == "generic · A · acquisition"
    assert clips[runs["no_instrument"]["uid"]]["lane"] == "(unknown) · acquisition"
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
        "zoom_from_ms", "zoom_to_ms", "activity_error", "lane_id", "track", "root",
    ]  # fmt: skip
    window_end_ms = manifest["window"][1] * 1000
    by_uid = {r["uid"]: r for r in rows}
    open_row = by_uid[manifest["runs"]["open_inside"]["uid"]]
    assert open_row["stop"] == window_end_ms and open_row["state"] == "open"
    assert by_uid[manifest["runs"]["open_20d"]["uid"]]["state"] == "possibly stalled"
    lanes = [r["lane"] for r in rows]
    assert lanes.index("MOUSE · acquisition") < lanes.index("MOUSE · processing")
    # Overlapping MOUSE runs are spread over sub-lanes; every run is present.
    assert any(lane.startswith("MOUSE · acquisition /") for lane in lanes)
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
    assert (run["status"], run["lane"], run["duration_s"]) == (
        "aborted",
        "MOUSE · acquisition",
        10.0,
    )
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
    assert all(r["time"] > 1e12 for r in measure)  # milliseconds
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
    assert set(data["rows"][0]) == {"time", "field", "series", "value"}


def test_processing_run_without_outputs_has_no_plottable_data(
    api: tuple[TestClient, dict[str, Any]],
) -> None:
    client, manifest = api
    views = client.get(f"/runs/{manifest['runs']['proc_inside_failed']['uid']}/views").json()
    assert views["default"] == "summary"
    assert views["views"][0] == {**views["views"][0], "name": "no_data", "available": False}


def test_health_reports_fetch_state(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, manifest = api
    client.get("/clips", params=_window(manifest))
    [row] = client.get("/health").json()
    assert row["status"] == "ok" and row["last_success"] is not None and row["last_error"] is None


def test_status_filter_and_reasons(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, manifest = api
    params = {**_window(manifest), "status": "failed,aborted"}
    clips = client.get("/clips", params=params).json()["clips"]
    assert {c["status"] for c in clips} == {"failed", "aborted"}
    assert all(c["reason"] for c in clips)
    assert client.get("/clips", params={**params, "status": "running"}).status_code == 422


def test_data_by_kind(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, manifest = api
    runs = manifest["runs"]
    mouse, generic = runs["inside"]["uid"], runs["generic_inside"]["uid"]
    # A processing run's outputs are arrays: no time series.
    none = client.get(f"/runs/{runs['proc_inside_ok']['uid']}/data", params={"kind": "timeseries"})
    assert (none.json()["view"], none.json()["rows"]) == (None, [])
    # MOUSE measurements: the view table's time series of the detector stream.
    detector = client.get(f"/runs/{mouse}/data", params={"kind": "timeseries"}).json()
    assert (detector["view"], detector["stream"]) == ("numeric", "mouse_eiger_measure")
    assert {r["field"] for r in detector["rows"]} == {"roi", "stats"}
    series = client.get(f"/runs/{generic}/data", params={"kind": "timeseries"}).json()
    assert series["view"] == "numeric" and len(series["rows"]) == 5
    both = {"kind": "table", "view": "summary"}
    assert client.get(f"/runs/{generic}/data", params=both).status_code == 422


def test_window_status(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, manifest = api
    status = client.get("/clips/status", params=_window(manifest)).json()
    expected = sum(1 for c in manifest["runs"].values() if c["expect_in_window"])
    assert (status["state"], status["runs"], status["truncated"]) == ("complete", expected, False)


def test_shared_field_keeps_each_streams_units(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, manifest = api
    uid = manifest["runs"]["shared_field"]["uid"]
    [readings] = [v for v in client.get(f"/runs/{uid}/views").json()["views"] if v["default"]]
    assert readings["fields"].count("shared") == 1
    assert {(c["stream"], c["units"]) for c in readings["columns"] if c["field"] == "shared"} == {
        ("mouse_eiger_measure", "A"),
        ("snapshot", "V"),
    }
    rows = client.get(f"/runs/{uid}/data").json()["rows"]
    shared = [(r["stream"], r["seq_num"], r["units"]) for r in rows if r["field"] == "shared"]
    assert sorted(shared) == [
        ("mouse_eiger_measure", 1, "A"),
        ("mouse_eiger_measure", 2, "A"),
        ("snapshot", 1, "V"),
    ]
    keys = [(r["stream"], r["seq_num"], r["field"]) for r in rows]
    assert len(keys) == len(set(keys))  # no row twice


def test_table_row_limit_is_exact(
    api: tuple[TestClient, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, manifest = api
    uid = manifest["runs"]["shared_field"]["uid"]
    n = len(client.get(f"/runs/{uid}/data").json()["rows"])
    monkeypatch.setattr(adapter_runs, "TABLE_ROW_LIMIT", n)
    exact = client.get(f"/runs/{uid}/data").json()
    assert (len(exact["rows"]), exact["truncated"]) == (n, False)
    monkeypatch.setattr(adapter_runs, "TABLE_ROW_LIMIT", n - 1)
    cut = client.get(f"/runs/{uid}/data").json()
    assert (len(cut["rows"]), cut["truncated"]) == (n - 1, True)
    assert "only the first" in cut["notice"]


def test_markup_in_metadata_is_returned_as_text(
    api: tuple[TestClient, dict[str, Any]],
) -> None:
    client, manifest = api
    uid = manifest["runs"]["shared_field"]["uid"]
    clips = {c["uid"]: c for c in client.get("/clips", params=_window(manifest)).json()["clips"]}
    assert clips[uid]["label"].startswith("<b>S-shared</b>")
    metadata = client.get(f"/runs/{uid}").json()["metadata"]
    assert {"document": "start", "key": "sampleid", "value": "<b>S-shared</b>"} in metadata


def test_selection_endpoints(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, manifest = api
    # Nothing selected: a normal state, not an error.
    none = client.get("/selection/run", params={"uid": ""}).json()
    assert none["selected"] is False and none["summary"][0]["value"].startswith("Select a run")
    assert (none["metadata"], none["metadata_truncated"]) == ([], False)
    assert client.get("/selection/streams", params={"uid": ""}).json() == []
    assert client.get("/selection/fields", params={"uid": "", "stream": ""}).json() == []
    empty = client.get("/selection/data", params={"uid": ""}).json()
    assert (empty["rows"], empty["notice"]) == ([], none["summary"][0]["value"])
    # A selected run: the same as /runs/{uid}/...
    uid = manifest["runs"]["inside"]["uid"]
    selected = client.get("/selection/run", params={"uid": uid}).json()
    whole = client.get(f"/runs/{uid}").json()
    for key in ("summary", "metadata", "metadata_truncated"):
        assert selected[key] == whole[key]
    assert {"document": "stop", "key": "exit_status", "value": "success"} in selected["metadata"]
    assert (
        client.get("/selection/streams", params={"uid": uid}).json()
        == client.get(f"/runs/{uid}/streams").json()
    )
    data = client.get("/selection/data", params={"uid": uid}).json()
    assert data["rows"] == client.get(f"/runs/{uid}/data").json()["rows"]
    assert data["notice"].startswith("Readings: ")
    fields = client.get("/selection/fields", params={"uid": uid, "stream": "baseline"}).json()
    assert fields == client.get(f"/runs/{uid}/streams/baseline/fields").json()
    # A run this Tiled does not have (a bookmark, a shared link): a message, no
    # error. /runs/{uid} still answers 404; a malformed uid is refused.
    gone = client.get("/selection/run", params={"uid": "no-such-run"}).json()
    assert (gone["selected"], gone["found"]) == (True, False)
    assert gone["summary"][0]["value"].startswith("The selected run is not in Tiled")
    assert gone["metadata"] == []
    assert client.get("/selection/streams", params={"uid": "no-such-run"}).json() == []
    assert (
        client.get("/selection/fields", params={"uid": "no-such-run", "stream": "x"}).json() == []
    )
    gone_data = client.get("/selection/data", params={"uid": "no-such-run"}).json()
    assert (gone_data["rows"], gone_data["notice"]) == ([], gone["summary"][0]["value"])
    assert client.get("/runs/no-such-run").status_code == 404
    assert client.get("/selection/run", params={"uid": "a/b"}).status_code == 422


def test_electrochemistry_run(api: tuple[TestClient, dict[str, Any]]) -> None:
    """electrochemistry-bluesky's set-up names (no facility) and hints."""
    client, manifest = api
    uid = manifest["runs"]["electrochemistry"]["uid"]
    clips = {c["uid"]: c for c in client.get("/clips", params=_window(manifest)).json()["clips"]}
    clip = clips[uid]
    assert (clip["lane"], clip["label"]) == (
        "electrochemistry-k2460 · simulator · acquisition",
        "example-rest · S-echem",
    )
    views = client.get(f"/runs/{uid}/views").json()
    assert views["default"] == "hinted"
    data = client.get("/selection/data", params={"uid": uid, "kind": "timeseries"}).json()
    assert (data["stream"], data["fields"]) == ("readout", ["voltage_v", "current_a"])
    assert data["units"] == {"voltage_v": "V", "current_a": "A"}
    assert len(data["rows"]) == 10
    # A second axis for fields in other units than the first field's.
    for axis, field, series in (
        (1, "voltage_v", "voltage_v [V]"),
        (2, "current_a", "current_a [A]"),
    ):
        part = client.get(
            "/selection/data", params={"uid": uid, "kind": "timeseries", "axis": axis}
        ).json()
        assert (part["axis"], part["fields"]) == (axis, [field])
        assert {(r["field"], r["series"]) for r in part["rows"]} == {(field, series)}
        assert len(part["rows"]) == 5


def test_trend_over_runs_like_the_selected_one(api: tuple[TestClient, dict[str, Any]]) -> None:
    """Same lane, role and plan_name, one series per field, a gap between runs."""
    client, manifest = api
    runs = manifest["runs"]
    uid = runs["electrochemistry"]["uid"]
    window = _window(manifest)
    body = client.get("/selection/trend", params={"uid": uid, **window, "axis": 1}).json()
    assert body["runs"]["like"] == body["runs"]["plotted"] == 2  # not the "calibrate" run
    assert (body["fields"], body["plan_name"]) == (["voltage_v"], "run_sequence")
    data = [r for r in body["rows"] if r["value"] is not None]
    gaps = [r for r in body["rows"] if r["value"] is None]
    assert len(data) == 5 + 4 and len(gaps) == 1
    assert {r["series"] for r in body["rows"]} == {"voltage_v [V]"}
    times = [r["time"] for r in body["rows"]]
    assert times == sorted(times)
    first = [r["time"] for r in data][:5]
    second = [r["time"] for r in data][5:]
    assert max(first) < gaps[0]["time"] < min(second)
    assert body["notice"].startswith(
        "Hinted fields of readout over 2 run_sequence runs on "
        "electrochemistry-k2460 · simulator · acquisition in range"
    )
    assert body["lane"] == clip_lane(client, manifest, uid)
    assert body == client.get(f"/runs/{uid}/trend", params={**window, "axis": 1}).json()
    right = client.get("/selection/trend", params={"uid": uid, **window, "axis": 2}).json()
    assert right["fields"] == ["current_a"]
    # A run of another plan is a trend of its own; a small budget still keeps the last event.
    other = runs["electrochemistry_other_plan"]["uid"]
    alone = client.get("/selection/trend", params={"uid": other, **window}).json()
    assert alone["runs"]["like"] == 1 and alone["fields"] == ["voltage_v", "current_a"]
    small = client.get("/selection/trend", params={"uid": uid, **window, "max_points": 10}).json()
    assert len(small["rows"]) <= 10 and small["rows"][-1]["time"] == data[-1]["time"]
    # The window decides which runs: only the first run's time.
    start = client.get(f"/runs/{uid}").json()["run"]
    narrow = {"from": start["start"], "to": start["stop"] + 1}
    assert client.get(f"/runs/{uid}/trend", params=narrow).json()["runs"]["like"] == 1
    # MOUSE measurements: the view table's time series of the detector stream
    # (the fixture hints nothing there, so all its numeric scalars).
    mouse = client.get("/selection/trend", params={"uid": runs["inside"]["uid"], **window})
    body = mouse.json()
    assert (body["view"], body["stream"]) == ("numeric", "mouse_eiger_measure")
    assert body["fields"] == ["roi", "stats"] and body["runs"]["unreadable"] == 0
    assert body["runs"]["plotted"] >= 2
    # No time series view (a processing run), nothing selected, a run Tiled lacks.
    processing = {"uid": runs["proc_inside_ok"]["uid"], **window}
    assert client.get("/selection/trend", params=processing).json()["notice"] == (
        "this run has no timeseries view"
    )
    none = client.get("/selection/trend", params={"uid": "", **window}).json()
    assert none["rows"] == [] and none["notice"].startswith("Select a run")
    gone = client.get("/selection/trend", params={"uid": "no-such-run", **window}).json()
    assert gone["notice"].startswith("The selected run is not in Tiled")
    assert client.get("/selection/trend", params={"uid": uid}).status_code == 422


def test_open_run_before_its_first_batch(api: tuple[TestClient, dict[str, Any]]) -> None:
    """TiledWriter writes event rows in batches: a running run's streams exist
    before their tables do. That is "no events yet", not an error."""
    client, manifest = api
    uid = manifest["runs"]["open_unflushed"]["uid"]
    clips = {c["uid"]: c for c in client.get("/clips", params=_window(manifest)).json()["clips"]}
    assert clips[uid]["status"] == "open" and clips[uid]["activity_error"] is None
    assert clips[uid]["last_event"] is None and clips[uid]["possibly_stalled"] is False
    status = client.get("/clips/status", params=_window(manifest)).json()
    assert status["state"] == "complete", status["issues"]
    streams = client.get("/selection/streams", params={"uid": uid}).json()
    assert {(s["stream"], s["events"], s["last_event"]) for s in streams} == {
        ("baseline", 0, None),
        ("primary", 0, None),
    }
    for kind in ("table", "timeseries"):
        data = client.get("/selection/data", params={"uid": uid, "kind": kind}).json()
        assert data["rows"] == [] and data["notice"].endswith("(none in Tiled yet)"), kind


def clip_lane(client: TestClient, manifest: dict[str, Any], uid: str) -> str:
    clips = client.get("/clips", params=_window(manifest)).json()["clips"]
    return str(next(c["lane_key"] for c in clips if c["uid"] == uid))
