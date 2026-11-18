"""Presets on the adapter's endpoints (presets.md §10), against seeded Tiled
servers (SQLite and PostgreSQL), and run keys in several roots."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from event_timeline_tiled.adapter import create_app
from event_timeline_tiled.settings import Settings

Api = tuple[TestClient, dict[str, Any]]

BY_PLAN = """\
version: 1
id: by-plan
title: MOUSE by plan
scope:
  - {field: start.instrument, op: in, value: [MOUSE]}
tracks:
  - id: all
    name: All
    group_by: {field: start.plan_name, fallback: no plan}
  - id: failed
    name: Failed
    where: {field: status, op: in, value: [failed, aborted]}
lanes:
  order: [failed]
"""
PER_RUN = """\
version: 1
id: per-run
title: One lane per run
tracks: [{id: t, name: T, lane_prefix: false, group_by: {field: uid}}]
lanes: {max_lanes: 2}
"""


@pytest.fixture(scope="module")
def api(
    catalog_server: tuple[Any, dict[str, Any]], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Api]:
    server, manifest = catalog_server
    presets = tmp_path_factory.mktemp("presets")
    (presets / "by-plan.yaml").write_text(BY_PLAN)
    (presets / "per-run.yaml").write_text(PER_RUN)
    settings = Settings(tiled_url=server.url, tiled_api_key=server.api_key, presets_dir=presets)
    now = manifest["window"][1]
    with TestClient(create_app(settings, clock=lambda: now, data_cache_s=0)) as client:
        yield client, manifest


def _window(manifest: dict[str, Any]) -> dict[str, Any]:
    t_from, t_to = manifest["window"]
    return {"from": t_from, "to": t_to}


def _uid(manifest: dict[str, Any], case: str) -> str:
    return str(manifest["runs"][case]["uid"])


def test_presets_are_listed_default_first(api: Api) -> None:
    client, _ = api
    assert client.get("/presets").json() == [
        {"id": "instruments", "title": "Instruments"},
        {"id": "by-plan", "title": "MOUSE by plan"},
        {"id": "per-run", "title": "One lane per run"},
    ]


def test_an_unknown_preset_is_refused(api: Api) -> None:
    client, manifest = api
    for path in ("/clips", "/clips/frame", "/clips/status"):
        response = client.get(path, params={**_window(manifest), "preset": "nope"})
        assert response.status_code == 422, path
    bad = client.get("/clips", params={**_window(manifest), "preset": "Not An Id"})
    assert bad.status_code == 422


def test_a_preset_places_runs_in_its_tracks(api: Api) -> None:
    client, manifest = api
    body = client.get("/clips", params={**_window(manifest), "preset": "by-plan"}).json()
    assert body["preset"] == "by-plan" and body["evaluated_at"] == manifest["window"][1]
    clips = {c["uid"]: c for c in body["clips"]}
    # scope: MOUSE only (the generic and electrochemistry runs are left out).
    assert _uid(manifest, "generic_inside") not in clips
    assert _uid(manifest, "electrochemistry") not in clips
    failed = clips[_uid(manifest, "failed")]
    assert [(p["track"], p["lane"]) for p in failed["placements"]] == [
        ("failed", "Failed"), ("all", "All · plan"),
    ]  # fmt: skip
    assert failed["lane"] == "Failed"  # the first placement in lanes.order
    ok = clips[_uid(manifest, "inside")]
    assert [p["track"] for p in ok["placements"]] == ["all"]
    only_failed = client.get("/clips", params={**_window(manifest), "preset": "by-plan",
                                               "status": "failed"}).json()  # fmt: skip
    assert {c["status"] for c in only_failed["clips"]} == {"failed"}


def test_the_frame_and_status_follow_the_preset(api: Api) -> None:
    client, manifest = api
    window = {**_window(manifest), "preset": "by-plan"}
    rows = client.get("/clips/frame", params=window).json()
    lanes = list(dict.fromkeys(r["lane"].split(" /")[0] for r in rows))
    assert lanes[0] == "Failed" and "All · plan" in lanes
    assert {r["track"] for r in rows} == {"all", "failed"}
    failed = [r for r in rows if r["uid"] == _uid(manifest, "failed")]
    assert len(failed) == 2  # once per placement
    status = client.get("/clips/status", params=window).json()
    assert status["preset"] == "by-plan" and status["placements"] > status["matched_runs"]
    assert status["lanes"] == len({r["lane_id"] for r in rows})
    assert status["state"] in {"complete", "partial"} and status["lanes_cut"] == 0


def test_a_lane_budget_is_reported_not_hidden(api: Api) -> None:
    client, manifest = api
    window = {**_window(manifest), "preset": "per-run"}
    status = client.get("/clips/status", params=window).json()
    assert status["lanes"] == 2 and status["lanes_cut"] > 0
    assert status["state"] == "incomplete"
    assert any("over the lane budget" in i["message"] for i in status["issues"])
    assert len({r["lane_id"] for r in client.get("/clips/frame", params=window).json()}) == 2


def test_min_lanes_defaults_to_the_presets(api: Api) -> None:
    client, manifest = api
    window = {**_window(manifest), "preset": "per-run"}
    padded = client.get("/clips/frame", params={**window, "min_lanes": 6}).json()
    assert len({r["lane"] for r in padded}) == 6


def test_latest_runs_on_a_lane(api: Api) -> None:
    client, manifest = api
    window = {**_window(manifest), "preset": "by-plan"}
    everything = client.get("/selection/runs", params=window).json()
    assert everything["lane"] is None and everything["rows"]
    starts = [r["start"] for r in everything["rows"]]
    assert starts == sorted(starts, reverse=True)
    assert len({(r["root"], r["uid"]) for r in everything["rows"]}) == len(everything["rows"])
    failed_lane = next(r["lane_id"] for r in client.get("/clips/frame", params=window).json()
                       if r["track"] == "failed")  # fmt: skip
    on_lane = client.get("/selection/runs", params={**window, "lane": failed_lane}).json()
    assert {r["status"] for r in on_lane["rows"]} <= {"failed", "aborted"}
    assert on_lane["lane_name"] == "Failed" and on_lane["notice"].endswith("on Failed")
    # A direct link: the run's first lane in the preset.
    direct = client.get("/selection/runs",
                        params={**window, "uid": _uid(manifest, "failed")}).json()  # fmt: skip
    assert direct["lane"] == failed_lane and "first lane" in direct["notice"]
    gone = client.get("/selection/runs", params={**window, "lane": "by-plan.all.000000000000"})
    assert gone.json()["rows"] == [] and "not in this preset" in gone.json()["notice"]
    small = client.get("/selection/runs", params={**window, "limit": 1}).json()
    assert len(small["rows"]) == 1 and small["truncated"] is True


def test_the_trend_follows_the_selected_lane(api: Api) -> None:
    client, manifest = api
    window = _window(manifest)
    uid = _uid(manifest, "electrochemistry")
    other_lane = {**window, "uid": uid, "lane": "by-plan.all.000000000000"}
    body = client.get("/selection/trend", params=other_lane).json()
    assert body["lane"] is None and "that lane is not in this preset's range" in body["notice"]
    assert body["runs"]["like"] == 0
    # by-plan's scope leaves electrochemistry out: no lane, no peers.
    scoped = client.get("/selection/trend", params={**window, "uid": uid, "preset": "by-plan"})
    assert "in no lane of this preset" in scoped.json()["notice"]


def test_runs_can_be_named_by_root(api: Api) -> None:
    client, manifest = api
    uid = _uid(manifest, "inside")
    assert client.get(f"/runs/{uid}", params={"root": "/"}).json()["run"]["uid"] == uid
    assert client.get(f"/runs/{uid}", params={"root": "elsewhere"}).status_code == 422
    selected = client.get("/selection/run", params={"uid": uid, "root": "/"}).json()
    assert selected["found"] is True


def test_a_run_key_in_two_roots_is_never_guessed(tiled_server: Any, tmp_path: Path) -> None:
    from tiled.client import from_uri

    from event_timeline_tiled.testing.fixtures import FixtureWriter

    client = from_uri(tiled_server.url, api_key=tiled_server.api_key)
    roots = [client.create_container(name) for name in ("copy-a", "copy-b")]
    fw = FixtureWriter(client, tiled_server.processed_dir)
    uid = fw.same_run_in(roots, {"facility": "BAM", "instrument": "twin"}, 50.0, 60.0)
    settings = Settings(tiled_url=tiled_server.url, tiled_api_key=tiled_server.api_key,
                        catalog_roots="copy-a,copy-b")  # fmt: skip
    with TestClient(create_app(settings, clock=lambda: 100.0)) as api:
        assert api.get(f"/runs/{uid}").status_code == 409
        assert api.get(f"/runs/{uid}", params={"root": "copy-b"}).json()["run"]["root"] == "copy-b"
        selected = api.get("/selection/run", params={"uid": uid}).json()
        assert (
            selected["found"] is False
            and "several catalogue roots" in selected["summary"][0]["value"]
        )
        frame = api.get("/clips/frame", params={"from": 0, "to": 100}).json()
        twins = [r for r in frame if r["uid"] == uid]
        # One lane per root (the root is part of the default key), told apart by name.
        assert sorted(r["root"] for r in twins) == ["copy-a", "copy-b"]
        assert len({r["lane"] for r in twins}) == 2
