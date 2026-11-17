"""Processing results: related runs, corrected curves and images (plan step 6),
against seeded Tiled servers (SQLite and PostgreSQL)."""

from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from event_timeline_tiled.adapter import create_app
from event_timeline_tiled.layout import Layout
from event_timeline_tiled.settings import Settings
from event_timeline_tiled.tiled_http import TiledReader

Server = tuple[Any, dict[str, Any]]
Api = tuple[TestClient, dict[str, Any]]


@pytest.fixture(scope="module")
def api(catalog_server: Server) -> Iterator[Api]:
    server, manifest = catalog_server
    settings = Settings(tiled_url=server.url, tiled_api_key=server.api_key)
    now = manifest["window"][1]
    with TestClient(create_app(settings, clock=lambda: now, data_cache_s=0)) as client:
        yield client, manifest


def _uid(manifest: dict[str, Any], case: str) -> str:
    return str(manifest["runs"][case]["uid"])


def test_related_marks_the_latest_attempt_and_usable_result(api: Api) -> None:
    client, manifest = api
    for case, expected in manifest["selection"].items():
        body = client.get(f"/runs/{_uid(manifest, case)}/related").json()
        assert body["role"] == "acquisition", case
        rows = body["rows"]
        assert all(r["relation"] == "processing" for r in rows), case
        attempt = [r["uid"] for r in rows if r["latest_attempt"]]
        best = [r["uid"] for r in rows if r["latest_usable"]]
        assert attempt == [expected["latest_attempt"]], case
        assert best == ([expected["latest_usable"]] if expected["latest_usable"] else []), case
        assert body["default"] == expected["latest_usable"], case
        for row in rows:
            marks = [m for m, on in (("latest usable", row["latest_usable"]),
                                     ("latest attempt", row["latest_attempt"])) if on]  # fmt: skip
            assert row["mark"] == (" · ".join(marks) or None), case
    inside = client.get(f"/runs/{_uid(manifest, 'inside')}/related").json()
    cases = ("proc_inside_failed", "proc_inside_ok", "proc_inside_retry_failed")
    assert [r["uid"] for r in inside["rows"]] == [_uid(manifest, c) for c in cases]
    assert inside["notice"].startswith("Latest usable result: ")
    assert inside["notice"].endswith("; a later attempt failed")


def test_related_without_results(api: Api) -> None:
    client, manifest = api
    only_open = client.get(f"/runs/{_uid(manifest, 'stop_eq_from')}/related").json()
    assert [r["status"] for r in only_open["rows"]] == ["open"]
    assert only_open["notice"] == "No usable processing result of this measurement; 1 still open"
    none = client.get(f"/runs/{_uid(manifest, 'generic_inside')}/related").json()
    assert (none["rows"], none["notice"]) == ([], "No processing run of this measurement")


def test_related_of_a_processing_run_lists_its_measurements(api: Api) -> None:
    client, manifest = api
    body = client.get(f"/runs/{_uid(manifest, 'proc_two_parents')}/related").json()
    assert body["role"] == "processing"
    assert [(r["relation"], r["uid"]) for r in body["rows"]] == [
        ("measurement", _uid(manifest, "spanning")),
        ("measurement", _uid(manifest, "stop_eq_to")),
    ]


def test_derived_from_lookup_is_exact(catalog_server: Server) -> None:
    """``Like`` finds candidates; only exact list members are kept."""
    server, manifest = catalog_server
    layout = Layout(TiledReader(Settings(tiled_url=server.url, tiled_api_key=server.api_key)))
    inside = _uid(manifest, "inside")
    found, truncated = layout.derived_runs("", inside, 50)
    assert len(found) == 3 and not truncated
    assert layout.derived_runs("", inside[:-1], 50) == ([], False)
    assert layout.derived_runs("", inside[1:], 50) == ([], False)
    # Two parents: found from either.
    both = {r.key for r in layout.derived_runs("", _uid(manifest, "spanning"), 50)[0]}
    assert _uid(manifest, "proc_two_parents") in both


def test_processing_run_views_and_default_data(api: Api) -> None:
    client, manifest = api
    proc = _uid(manifest, "proc_inside_ok")
    views = client.get(f"/runs/{proc}/views").json()
    assert views["default"] == "corrected_curve"
    assert [v["name"] for v in views["views"]] == ["corrected_curve", "corrected_image", "summary"]
    data = client.get(f"/runs/{proc}/data").json()
    assert data["kind"] == "curve" and len(data["rows"]) == 2000
    image = client.get(f"/runs/{proc}/data", params={"view": "corrected_image"}).json()
    assert (image["rows"], image["notice"]) == ([], "Corrected image: shown in Corrected data")


def test_curve_of_a_processing_run(api: Api) -> None:
    client, manifest = api
    body = client.get(f"/runs/{_uid(manifest, 'proc_inside_ok')}/curve").json()
    assert (body["view"], body["x"], body["y"], body["uncertainty"]) == (
        "corrected_curve", "Q", "I", "I_uncertainty",
    )  # fmt: skip
    assert body["source"] == {**body["source"], "usable": True, "followed": False}
    assert (body["points"], body["plotted"], body["decimation"]) == (2000, 2000, 1)
    assert body["units"] == {"Q": "1/nm", "I": "1/(m sr)", "I_uncertainty": "1/(m sr)"}
    for row in body["rows"]:
        assert 0 < row["y_lower"] < row["y"] < row["y_upper"] and row["x"] > 0
    xs = [r["x"] for r in body["rows"]]
    assert xs == sorted(xs)
    assert body["notice"].startswith("Corrected curve of ")
    assert "I [1/(m sr)] against Q [1/nm], bounds ± I_uncertainty" in body["notice"]
    small = client.get(f"/runs/{_uid(manifest, 'proc_inside_ok')}/curve",
                       params={"max_points": 100}).json()  # fmt: skip
    assert (small["decimation"], len(small["rows"]), small["truncated"]) == (20, 100, True)


def test_curve_of_a_measurement_follows_its_latest_usable_result(api: Api) -> None:
    client, manifest = api
    body = client.get(f"/runs/{_uid(manifest, 'inside')}/curve").json()
    assert body["source"]["uid"] == _uid(manifest, "proc_inside_ok")
    assert body["source"]["followed"] is True and len(body["rows"]) == 2000
    assert "the latest usable result of the selected measurement" in body["notice"]
    unusable = client.get(f"/runs/{_uid(manifest, 'stop_eq_to')}/curve").json()
    assert (unusable["rows"], unusable["source"]) == ([], None)
    assert unusable["notice"] == "No usable processing result of this measurement; " \
        "latest attempt failed"  # fmt: skip


def test_curves_of_runs_that_are_not_usable(api: Api) -> None:
    client, manifest = api
    # Partial: corrected_1d was published, the run failed. Shown, and said so.
    partial = client.get(f"/runs/{_uid(manifest, 'proc_partial')}/curve").json()
    assert len(partial["rows"]) == 2000 and partial["source"]["usable"] is False
    assert ": not a usable result (failed): " in partial["notice"]
    failed = client.get(f"/runs/{_uid(manifest, 'proc_inside_failed')}/curve").json()
    assert (failed["rows"], failed["notice"]) == ([], "This processing run has no curve (failed)")
    still_open = client.get(f"/runs/{_uid(manifest, 'proc_open')}/curve").json()
    assert still_open["notice"] == "This processing run has no curve yet"


def test_image_of_a_processing_run(api: Api) -> None:
    client, manifest = api
    proc = _uid(manifest, "proc_inside_ok")
    response = client.get(f"/runs/{proc}/image", params={"size": 64})
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, max-age=86400"
    assert Image.open(io.BytesIO(response.content)).size == (64, 64)
    etag = response.headers["etag"]
    again = client.get(f"/runs/{proc}/image", params={"size": 64},
                       headers={"If-None-Match": etag})  # fmt: skip
    assert again.status_code == 304 and again.content == b""
    linear = client.get(f"/run-images/{proc}/image", params={"size": 64, "scale": "linear"})
    assert linear.status_code == 200 and linear.headers["etag"] != etag
    assert client.get(f"/runs/{proc}/image", params={"scale": "sqrt"}).status_code == 422
    assert client.get(f"/runs/{proc}/image", params={"size": 4096}).status_code == 422


def test_image_of_a_measurement_and_without_one(api: Api) -> None:
    client, manifest = api
    response = client.get(f"/run-images/{_uid(manifest, 'inside')}/image", params={"size": 128})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-cache"  # a newer result may come
    assert Image.open(io.BytesIO(response.content)).size == (128, 128)
    # The partial failure published no image; the measurement has no usable result.
    for case in ("proc_partial", "stop_eq_to", "generic_inside"):
        missing = client.get(f"/run-images/{_uid(manifest, case)}/image")
        assert missing.status_code == 404, case
    assert client.get("/run-images/no-such-run/image").status_code == 404


def test_selection_of_results(api: Api) -> None:
    client, manifest = api
    for path in ("/selection/related", "/selection/curve"):
        empty = client.get(path).json()
        assert empty["rows"] == [] and empty["notice"].startswith("Select a run"), path
        gone = client.get(path, params={"uid": "no-such-run"}).json()
        assert gone["notice"].startswith("The selected run is not in Tiled"), path
    inside = {"uid": _uid(manifest, "inside")}
    assert len(client.get("/selection/related", params=inside).json()["rows"]) == 3
    assert len(client.get("/selection/curve", params=inside).json()["rows"]) == 2000
