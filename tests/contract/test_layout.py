"""The layout module's searches and reads, on SQLite and PostgreSQL (plan §5.4, §6)."""

from __future__ import annotations

from typing import Any

import pytest

from event_timeline_tiled import tiled_http
from event_timeline_tiled.layout import Layout
from event_timeline_tiled.settings import Settings
from event_timeline_tiled.testing.fixtures import DAY
from event_timeline_tiled.tiled_http import TiledReader

Server = tuple[Any, dict[str, Any]]


def _layout(server: Any) -> Layout:
    return Layout(TiledReader(Settings(tiled_url=server.url, tiled_api_key=server.api_key)))


def _cases(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return manifest["runs"]  # type: ignore[no-any-return]


def test_window_union_is_exact(catalog_server: Server) -> None:
    server, manifest = catalog_server
    t_from, t_to = manifest["window"]
    runs, truncated = _layout(server).window_runs("", t_from, t_to, 21 * DAY, 10_000)
    found = {r.key for r in runs}
    cases = _cases(manifest)
    unexpected = sorted(
        k for k, c in cases.items() if c["uid"] in found and not c["expect_in_window"]
    )
    missing = sorted(k for k, c in cases.items() if c["expect_in_window"] and c["uid"] not in found)
    assert (missing, unexpected, truncated) == ([], [], False)
    assert len(found) == len(runs)  # de-duplicated


def test_paging_reaches_every_run(catalog_server: Server, monkeypatch: pytest.MonkeyPatch) -> None:
    server, manifest = catalog_server
    t_from, t_to = manifest["window"]
    layout = _layout(server)
    full, _ = layout.window_runs("", t_from, t_to, 21 * DAY, 10_000)
    monkeypatch.setattr(tiled_http, "PAGE_LIMIT", 2)
    paged, truncated = layout.window_runs("", t_from, t_to, 21 * DAY, 10_000)
    assert [r.key for r in paged] == [r.key for r in full] and not truncated


def test_truncation_is_reported(catalog_server: Server) -> None:
    server, manifest = catalog_server
    t_from, t_to = manifest["window"]
    runs, truncated = _layout(server).window_runs("", t_from, t_to, 21 * DAY, 3)
    assert len(runs) == 3 and truncated


def test_horizon_is_anchored_to_the_window_end(catalog_server: Server) -> None:
    server, manifest = catalog_server
    t_from, t_to = manifest["window"]
    cases = _cases(manifest)
    runs, _ = _layout(server).window_runs("", t_from, t_to, 21 * DAY, 10_000)
    keys = {r.key for r in runs}
    assert cases["open_20d"]["uid"] in keys and cases["open_22d"]["uid"] not in keys
    longer, _ = _layout(server).window_runs("", t_from, t_to, 23 * DAY, 10_000)
    assert cases["open_22d"]["uid"] in {r.key for r in longer}


def test_find_run_and_uid_mismatch(catalog_server: Server) -> None:
    server, manifest = catalog_server
    cases = _cases(manifest)
    layout = _layout(server)
    run = layout.find_run(["missing-root", ""], cases["uid_mismatch"]["uid"])
    assert run is not None and run.uid_mismatch
    assert run.start_uid == cases["uid_mismatch"]["expect_start_uid"]
    assert layout.find_run([""], "no-such-run") is None
    normal = layout.find_run([""], cases["inside"]["uid"])
    assert normal is not None and not normal.uid_mismatch and normal.exit_status == "success"


def test_streams_and_event_columns(catalog_server: Server) -> None:
    server, manifest = catalog_server
    layout = _layout(server)
    run = layout.find_run([""], _cases(manifest)["inside"]["uid"])
    assert run is not None
    streams = {s.name: s for s in layout.streams(run)}
    assert sorted(streams) == ["baseline", "mouse_eiger_measure", "snapshot"]
    measure = streams["mouse_eiger_measure"]
    assert {"roi", "stats"} <= set(measure.data_keys) and measure.has_table
    columns = layout.event_columns(run, "mouse_eiger_measure", ["roi"])
    assert columns["seq_num"] == [1, 2, 3] and len(columns["roi"]) == 3
    assert run.num_events("mouse_eiger_measure") == 3


def test_last_event_time_of_an_open_run(catalog_server: Server) -> None:
    server, manifest = catalog_server
    layout = _layout(server)
    run = layout.find_run([""], _cases(manifest)["open_inside"]["uid"])
    assert run is not None and run.stop is None
    last = layout.last_event_time(run)
    assert last is not None and run.start_time is not None and last >= run.start_time


def test_processing_streams_have_no_table(catalog_server: Server) -> None:
    server, manifest = catalog_server
    layout = _layout(server)
    run = layout.find_run([""], _cases(manifest)["proc_inside_ok"]["uid"])
    assert run is not None
    assert {s.name: s.has_table for s in layout.streams(run)} == {
        "corrected_1d": False,
        "corrected_2d": False,
    }
