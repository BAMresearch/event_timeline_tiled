"""Cases reproduced in review 06, on runs these tests write themselves (a
RunEngine and event_model through TiledWriter, into the empty SQLite server)."""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from event_timeline_tiled.adapter import app as adapter_app
from event_timeline_tiled.adapter import create_app
from event_timeline_tiled.layout import Layout
from event_timeline_tiled.settings import Settings
from event_timeline_tiled.testing.fixtures import FixtureWriter, ProcessingOutputs
from event_timeline_tiled.tiled_http import TiledReader

T0 = 1_800_000_000.0
WINDOW = {"from": T0 - 100, "to": T0 + 200}


@pytest.fixture(scope="module")
def own(tiled_server: Any) -> dict[str, Any]:
    from tiled.client import from_uri

    fw = FixtureWriter(
        from_uri(tiled_server.url, api_key=tiled_server.api_key),
        tiled_server.processed_dir,
        ProcessingOutputs(curve_points=4, image_shape=(8, 8)),
    )
    md = {"facility": "BAM", "instrument": "unit-test", "plan_name": "u_plan"}
    runs = {
        "volts": fw.unit_run(md, T0, T0 + 10, units="V", value=2.0),
        "millivolts": fw.unit_run(md, T0 + 20, T0 + 30, units="mV", value=2000.0),
    }
    for parent in ("parent_a", "parentXa", "párent"):
        runs[parent] = fw.processing_run([parent], T0 + 40, T0 + 50)
    runs["sigma"] = fw.processing_run(
        ["sigma-parent"], T0 + 60, T0 + 70, uncertainty=[-1.0, math.nan, math.inf, 1.0]
    )
    runs["gone"] = fw.processing_run(["gone-parent"], T0 + 80, T0 + 90)
    (tiled_server.processed_dir / f"{runs['gone']}.h5").unlink()
    return runs


def _client(server: Any) -> TestClient:
    settings = Settings(tiled_url=server.url, tiled_api_key=server.api_key)
    return TestClient(create_app(settings, clock=lambda: T0 + 200, data_cache_s=0))


@pytest.fixture
def client(tiled_server: Any, own: dict[str, Any]) -> Iterator[TestClient]:
    with _client(tiled_server) as c:
        yield c


def _values(body: dict[str, Any]) -> list[Any]:
    return [r["value"] for r in body["rows"]]


def test_trend_in_other_units_does_not_reuse_a_cached_selection(
    tiled_server: Any, own: dict[str, Any], client: TestClient
) -> None:
    """Finding 1: the V run, then the mV run, gives what a cold app gives."""
    volts = client.get(f"/runs/{own['volts']}/trend", params=WINDOW).json()
    assert volts["units"] == {"reading": "V"} and 2.0 in _values(volts)
    warm = client.get(f"/runs/{own['millivolts']}/trend", params=WINDOW).json()
    with _client(tiled_server) as cold_client:
        cold = cold_client.get(f"/runs/{own['millivolts']}/trend", params=WINDOW).json()
    assert _values(warm) == _values(cold)
    assert 2000.0 in _values(warm) and 2.0 not in _values(warm)
    assert warm["runs"]["lacking_fields"] == 1  # the V run: its values left out


def test_derived_from_search_is_exact_for_any_uid(tiled_server: Any, own: dict[str, Any]) -> None:
    """Finding 5: `_` is no wildcard, non-ASCII UIDs are found, and false
    candidates do not use up the cap."""
    reader = TiledReader(Settings(tiled_url=tiled_server.url, tiled_api_key=tiled_server.api_key))
    layout = Layout(reader)
    for parent in ("parent_a", "parentXa", "párent"):
        found, truncated = layout.derived_runs("", parent, 1)
        assert [r.key for r in found] == [own[parent]] and not truncated, parent
    assert layout.derived_runs("", "parent", 5) == ([], False)


def test_invalid_uncertainties_give_no_bounds(own: dict[str, Any], client: TestClient) -> None:
    """Finding 12: negative and non-finite uncertainties are reported, never
    plotted, and the response is strict JSON."""
    body = client.get(f"/runs/{own['sigma']}/curve").json()
    assert body["uncertainty_invalid"] == 3
    rows = body["rows"]
    assert all(r["y_lower"] is None and r["y_upper"] is None for r in rows[:3])
    assert rows[3]["y_lower"] < rows[3]["y"] < rows[3]["y_upper"]
    assert "no bounds at 3 points" in body["notice"]


def test_a_missing_result_file_is_reported(own: dict[str, Any], client: TestClient) -> None:
    """Finding 15: Tiled 0.2.21 answers a registered HDF5 file that is gone with
    500 (not 410); the curve says it could not be read, the image fails."""
    body = client.get(f"/runs/{own['gone']}/curve").json()
    assert body["rows"] == [] and "could not be read (error: HTTP 500)" in body["notice"]
    assert client.get(f"/runs/{own['gone']}/image").status_code == 502


def test_notices_name_the_result_by_family_and_uid(own: dict[str, Any], client: TestClient) -> None:
    """Finding 13."""
    body = client.get(f"/runs/{own['sigma']}/curve").json()
    assert f"synthetic-correction, {own['sigma'][:8]})" in body["notice"]
    assert body["source"]["family"] == "saxs_1d_2d · synthetic-correction"


def test_a_capped_related_search_names_no_latest(
    catalog_server: tuple[Any, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 3: with more processing runs than the cap, nothing is called
    the latest, and a measurement's curve is not followed."""
    server, manifest = catalog_server
    monkeypatch.setattr(adapter_app, "RELATED_MAX_RUNS", 2)
    inside = manifest["runs"]["inside"]["uid"]
    with _client(server) as c:
        related = c.get(f"/runs/{inside}/related").json()
        assert related["truncated"] is True and related["default"] is None
        assert not any(r["latest_usable"] or r["latest_attempt"] for r in related["rows"])
        assert related["notice"].startswith("More than 2 processing runs")
        curve = c.get(f"/runs/{inside}/curve").json()
        assert curve["rows"] == [] and curve["notice"].startswith("More than 2")
        assert c.get(f"/run-images/{inside}/image").status_code == 404
