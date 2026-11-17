"""Point budgets and metadata flattening of the data endpoints, against a fake Tiled."""

from __future__ import annotations

import json
import math
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from event_timeline_tiled.adapter import create_app
from event_timeline_tiled.adapter import metadata as adapter_metadata
from event_timeline_tiled.layout import RunRecord
from event_timeline_tiled.settings import Settings


def fake_tiled(
    n_events: int, fields: list[str], start: dict[str, Any], units: dict[str, str] | None = None
) -> httpx.MockTransport:
    data_keys = {f: {"dtype": "number", "shape": [], "source": "sim"} for f in fields}
    for f, u in (units or {}).items():
        data_keys[f]["units"] = u
    metadata = {
        "start": {"uid": "r", "time": 0.0, **start},
        "stop": {"time": 100.0, "exit_status": "success"},
    }
    run = {"id": "r", "attributes": {"metadata": metadata}}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        if path == "/metadata/r":
            return httpx.Response(200, json={"data": run})
        if path == "/search/r":
            stream = {"id": "primary", "attributes": {"metadata": {"data_keys": data_keys}}}
            return httpx.Response(200, json={"data": [stream], "links": {"next": None}})
        if path.startswith("/table/full/r/primary"):
            columns = request.url.params.get_list("column")
            table = {
                c: list(range(1, n_events + 1)) if c == "seq_num" else
                [float(i) for i in range(n_events)]
                for c in columns
            }  # fmt: skip
            return httpx.Response(200, json=table)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _client(transport: httpx.MockTransport) -> TestClient:
    from event_timeline_tiled.tiled_http import TiledReader

    settings = Settings(tiled_url="http://tiled.test", tiled_api_key="k")
    reader = TiledReader(settings, transport=transport)
    return TestClient(create_app(settings, reader, clock=lambda: 200.0, data_cache_s=0))


@pytest.mark.parametrize("n_events", [9, 10, 11, 20, 1000])
def test_time_series_never_exceeds_max_points(n_events: int) -> None:
    with _client(fake_tiled(n_events, ["x"], {"instrument": "generic"})) as client:
        body = client.get("/runs/r/data", params={"max_points": 10}).json()
    times = [r["time"] for r in body["rows"]]
    # A bound, not a target: every k-th event may leave some of the budget unused.
    assert len(times) <= 10 and len(times) >= min(n_events, 10) // 2
    assert len(times) == n_events or n_events > 10
    assert times[0] == 0 and times[-1] == (n_events - 1) * 1000  # first and last kept
    assert body["decimation"]["returned"] == len(times) <= body["decimation"]["max_points"]


def test_the_point_budget_covers_all_fields() -> None:
    fields = [f"f{i}" for i in range(4)]
    with _client(fake_tiled(100, fields, {"instrument": "generic"})) as client:
        body = client.get("/runs/r/data", params={"max_points": 10}).json()
    assert len(body["rows"]) <= 10 and body["decimation"]["events"] == 2
    assert {r["field"] for r in body["rows"]} == set(fields)
    many = [f"f{i:02d}" for i in range(12)]
    with _client(fake_tiled(5, many, {"instrument": "generic"})) as client:
        cut = client.get("/runs/r/data", params={"max_points": 10}).json()
    assert len(cut["rows"]) <= 10 and cut["truncated"] is True
    assert cut["fields"] == many[:10] and "first 10 of 12 fields" in cut["notice"]


def test_fields_in_other_units_go_to_the_second_axis() -> None:
    units = {"a": "V", "b": "A", "d": "V"}  # c has none
    with _client(fake_tiled(3, ["a", "b", "c", "d"], {"instrument": "generic"}, units)) as client:
        both = client.get("/runs/r/data").json()
        left = client.get("/runs/r/data", params={"axis": 1}).json()
        right = client.get("/runs/r/data", params={"axis": 2}).json()
        assert client.get("/runs/r/data", params={"axis": 3}).status_code == 422
    assert (both["axis"], both["fields"]) == (None, ["a", "b", "c", "d"])
    assert (left["fields"], right["fields"]) == (["a", "d"], ["b", "c"])
    assert {(r["field"], r["series"]) for r in right["rows"]} == {("b", "b [A]"), ("c", "c")}


def test_metadata_is_plain_json_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    deep: dict[str, Any] = {"v": 1}
    for _ in range(12):
        deep = {"d": deep}
    start = {
        "uid": "r",
        "time": 0.0,
        "nan": math.nan,
        "list_of_dicts": [{"v": math.nan}, {"v": 1}],
        "nested_list": [[1, 2], [3, math.inf]],
        "scalars": ["a", 1, True, None, math.nan],
        "long": "x" * 5000,
        "deep": deep,
    }
    record = RunRecord("", "r", start, None)
    rows, truncated = adapter_metadata.metadata_rows(record)
    values = {r["key"]: r["value"] for r in rows}
    assert values["nan"] is None
    assert json.loads(values["list_of_dicts"]) == [{"v": None}, {"v": 1}]
    assert json.loads(values["nested_list"]) == [[1, 2], [3, None]]
    assert values["scalars"] == "a, 1, true, null, null"
    assert len(values["long"]) == adapter_metadata.METADATA_MAX_CHARS and values["long"].endswith(
        "…"
    )
    deep_key = next(k for k in values if k.startswith("deep."))
    assert len(deep_key.split(".")) == adapter_metadata.METADATA_MAX_DEPTH
    assert isinstance(values[deep_key], str) and json.loads(values[deep_key])
    assert truncated  # the long value was cut
    json.dumps(rows, allow_nan=False)  # strict JSON

    assert rows[-1] == {"document": "", "key": "(cut)", "value": adapter_metadata.METADATA_CUT}

    monkeypatch.setattr(adapter_metadata, "METADATA_MAX_ROWS", 3)
    rows, truncated = adapter_metadata.metadata_rows(record)
    assert len(rows) == 4 and truncated and rows[-1]["key"] == "(cut)"


def test_metadata_cut_by_depth_alone_is_reported() -> None:
    """Review 06, finding 11: nesting cut to "…" sets the flag too."""
    deep: dict[str, Any] = {"v": 1}
    for _ in range(2 * adapter_metadata.METADATA_MAX_DEPTH + 2):
        deep = {"d": deep}
    rows, truncated = adapter_metadata.metadata_rows(
        RunRecord("", "r", {"uid": "r", "deep": deep}, None)
    )
    assert truncated and any("…" in str(r["value"]) for r in rows[:-1])
    assert rows[-1]["key"] == "(cut)"
    shallow, cut = adapter_metadata.metadata_rows(
        RunRecord("", "r", {"uid": "r", "a": {"b": 1}}, None)
    )
    assert not cut and shallow[-1]["key"] != "(cut)"


def test_run_summary_reports_metadata_truncation() -> None:
    start = {"instrument": "generic", "blob": "y" * 5000}
    with _client(fake_tiled(3, ["x"], start)) as client:
        body = client.get("/runs/r").json()
    assert body["metadata_truncated"] is True
