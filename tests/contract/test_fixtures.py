"""The synthetic runs have the layout the plan relies on (plan §3.2, §6;
processing-runs.md §1.1). Window-search behaviour is tested with the search
code in step 3."""

import time
from pathlib import Path
from typing import Any

import numpy as np
from tiled.client import from_uri


def _client(tiled_server: Any, url: str) -> Any:
    return from_uri(url, api_key=tiled_server.api_key)


def test_all_cases_written(
    window_manifest: dict[str, Any], tiled_server: Any, tiled_base_url: str
) -> None:
    keys = set(_client(tiled_server, tiled_base_url).keys())
    assert {case["uid"] for case in window_manifest["runs"].values()} <= keys


def test_mouse_measurement_layout(
    window_manifest: dict[str, Any], tiled_server: Any, tiled_base_url: str
) -> None:
    run = _client(tiled_server, tiled_base_url)[window_manifest["runs"]["inside"]["uid"]]
    assert sorted(run.keys()) == ["baseline", "mouse_eiger_measure", "snapshot"]
    start, stop = run.metadata["start"], run.metadata["stop"]
    assert (start["facility"], start["instrument"]) == ("BAM", "MOUSE")
    assert stop["num_events"]["mouse_eiger_measure"] == 3
    t0 = window_manifest["t0"]
    assert (start["time"], stop["time"]) == (t0 + 1500, t0 + 1600)
    stream = run["mouse_eiger_measure"]
    assert {"data_keys", "hints"} <= set(stream.metadata)
    columns = list(stream.base["internal"].read().columns)
    assert columns[:2] == ["seq_num", "time"] and {"roi", "stats"} <= set(columns)


def test_open_and_aborted_runs(
    window_manifest: dict[str, Any], tiled_server: Any, tiled_base_url: str
) -> None:
    c = _client(tiled_server, tiled_base_url)
    assert "stop" not in c[window_manifest["runs"]["open_inside"]["uid"]].metadata
    unflushed = c[window_manifest["runs"]["open_unflushed"]["uid"]]
    assert "stop" not in unflushed.metadata and sorted(unflushed) == ["baseline", "primary"]
    for stream in ("baseline", "primary"):  # no table before the first batch
        assert "internal" not in unflushed[stream].base
    aborted = c[window_manifest["runs"]["aborted"]["uid"]].metadata["stop"]
    assert aborted["exit_status"] == "abort" and aborted["reason"]


def test_processing_run_contract(
    window_manifest: dict[str, Any], tiled_server: Any, tiled_base_url: str
) -> None:
    c = _client(tiled_server, tiled_base_url)
    case = window_manifest["runs"]["proc_inside_ok"]
    run = c[case["uid"]]
    start = run.metadata["start"]
    required = {
        "processing_schema_version", "kind", "product", "facility", "instrument",
        "derived_from", "inputs", "pipeline", "software", "request_id", "attempt", "modacor",
    }  # fmt: skip
    assert required <= set(start)
    assert start["kind"] == "modacor_correction"
    assert start["derived_from"] == case["derived_from"]
    assert start["processing_schema_version"] == 1
    assert {"name", "version", "hash"} <= set(start["pipeline"])
    assert {"session_id", "run_id"} <= set(start["modacor"])
    assert run.metadata["stop"]["outputs"] == ["saxs_1d_2d"]
    q = run["corrected_1d"]["Q"].read()
    assert q.shape == (1, 2000) and q.dtype == np.float32
    assert run["corrected_2d"]["image"].shape == (1, 1024, 1024)


def test_processing_fields_state_units_uncertainty_and_axes(
    window_manifest: dict[str, Any], tiled_server: Any, tiled_base_url: str
) -> None:
    run = _client(tiled_server, tiled_base_url)[window_manifest["runs"]["proc_inside_ok"]["uid"]]
    keys_1d = run["corrected_1d"].metadata["data_keys"]
    assert all(k["units"] and k["dtype_numpy"] == "<f4" for k in keys_1d.values())
    sigma = keys_1d["I_uncertainty"]
    assert sigma["uncertainty_of"] == "I" and sigma["meaning"]
    image = run["corrected_2d"].metadata["data_keys"]["image"]
    assert image["dims"] == ["y", "x"]
    assert all({"name", "units"} <= set(image["axes"][d]) for d in image["dims"])


def test_successful_processing_without_outputs_says_why(
    window_manifest: dict[str, Any], tiled_server: Any, tiled_base_url: str
) -> None:
    stop = _client(tiled_server, tiled_base_url)[
        window_manifest["runs"]["proc_two_parents"]["uid"]
    ].metadata["stop"]
    assert (stop["exit_status"], stop["outputs"]) == ("success", [])
    assert stop["reason"]


def test_failed_processing_has_no_outputs(
    window_manifest: dict[str, Any], tiled_server: Any, tiled_base_url: str
) -> None:
    run = _client(tiled_server, tiled_base_url)[
        window_manifest["runs"]["proc_inside_failed"]["uid"]
    ]
    assert run.metadata["stop"]["exit_status"] == "fail"
    assert run.metadata["stop"]["outputs"] == []
    assert list(run.keys()) == []


def test_live_measurement_has_the_mouse_streams(tiled_server: Any, tiled_base_url: str) -> None:
    from event_timeline_tiled.testing import FixtureWriter

    c = _client(tiled_server, tiled_base_url)
    fw = FixtureWriter(c, Path(tiled_server.processed_dir))
    uid = fw.mouse_measurement_live({"facility": "BAM", "instrument": "MOUSE"}, interval=0)
    run = c[uid]
    assert sorted(run.keys()) == ["baseline", "mouse_eiger_measure", "snapshot"]
    assert run.metadata["stop"]["num_events"]["mouse_eiger_measure"] == 3


def test_operando_backfill_is_retimed(tiled_server: Any, tiled_base_url: str) -> None:
    from event_timeline_tiled.testing import FixtureWriter

    c = _client(tiled_server, tiled_base_url)
    fw = FixtureWriter(c, Path(tiled_server.processed_dir))
    now = time.time()
    uid = fw.operando_forever(
        {"facility": "BAM", "instrument": "generic"},
        now,
        backfill=4,
        step=600,
        interval=0,
        live_readings=1,
    )
    run = c[uid]
    assert run.metadata["start"]["time"] == now - 2400
    times = run["primary"].base["internal"].read()["time"].tolist()
    assert times[:4] == [now - 1800, now - 1200, now - 600, now]
    assert len(times) == 5 and times[4] >= now - 5


def test_window_cases_have_expected_states(
    window_manifest: dict[str, Any], tiled_server: Any, tiled_base_url: str
) -> None:
    c = _client(tiled_server, tiled_base_url)
    runs = window_manifest["runs"]
    for case in ("stopped_success", "aborted", "failed"):
        md = c[runs[case]["uid"]].metadata
        assert md["stop"]["num_events"].get("mouse_eiger_measure", 0) < 3, case
        assert runs[case]["expect_complete"] is False
    assert runs["inside"]["expect_complete"] is True
    assert c[runs["failed"]["uid"]].metadata["stop"]["exit_status"] == "fail"
    mismatch = c[runs["uid_mismatch"]["uid"]].metadata["start"]["uid"]
    assert mismatch == runs["uid_mismatch"]["expect_start_uid"] != runs["uid_mismatch"]["uid"]


def test_processing_attempts_are_coherent(
    window_manifest: dict[str, Any], tiled_server: Any, tiled_base_url: str
) -> None:
    c = _client(tiled_server, tiled_base_url)
    runs = window_manifest["runs"]
    attempts = [
        c[runs[k]["uid"]].metadata["start"]
        for k in ("proc_inside_failed", "proc_inside_ok", "proc_inside_retry_failed")
    ]
    assert len({s["request_id"] for s in attempts}) == 1
    assert [s["attempt"] for s in attempts] == [1, 2, 3]
    a, b = (c[runs[k]["uid"]].metadata["stop"]["time"] for k in ("proc_tie_a", "proc_tie_b"))
    assert a == b
    partial = c[runs["proc_partial"]["uid"]]
    assert partial.metadata["stop"]["exit_status"] == "fail"
    assert partial.metadata["stop"]["outputs"] == []
    assert list(partial.keys()) == ["corrected_1d"]
