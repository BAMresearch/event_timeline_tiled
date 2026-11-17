"""Static checks of the provisioned Grafana files (plan §10, AGENTS.md)."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GRAFANA = ROOT / "grafana"
DASHBOARDS = sorted((GRAFANA / "dashboards").glob("*.json"))
ADAPTER = "http://adapter:8000/"
# Adapter endpoints dashboards may query (docs/technical/endpoints.md). The
# selected run is read only through /selection/..., which accept an empty
# selection; /runs/{uid}/... would fail on the dashboard's initial state.
ENDPOINTS = {
    "/health",
    "/queues",
    "/clips",
    "/clips/frame",
    "/clips/status",
    "/selection/run",
    "/selection/streams",
    "/selection/fields",
    "/selection/data",
    "/selection/trend",
}


def _datasources() -> dict[str, dict[str, Any]]:
    out = {}
    for f in (GRAFANA / "provisioning" / "datasources").glob("*.yaml"):
        for ds in yaml.safe_load(f.read_text())["datasources"]:
            out[ds["uid"]] = ds
    return out


def _walk(node: Any) -> Any:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_datasource_is_locked_to_the_adapter() -> None:
    ds = _datasources()["timeline-adapter"]
    assert ds["type"] == "yesoreyeram-infinity-datasource"
    assert ds["editable"] is False
    assert ds["jsonData"]["allowedHosts"] == [ADAPTER]
    assert "secureJsonData" not in ds  # no Tiled credential in Grafana


def test_dashboard_providers_are_read_only() -> None:
    for f in (GRAFANA / "provisioning" / "dashboards").glob("*.yaml"):
        for provider in yaml.safe_load(f.read_text())["providers"]:
            assert provider["allowUiUpdates"] is False


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_dashboard_queries(path: Path) -> None:
    dashboard = json.loads(path.read_text())
    uids = set(_datasources())
    assert dashboard["uid"] and dashboard["editable"] is False
    for node in _walk(dashboard):
        ds = node.get("datasource")
        if isinstance(ds, dict) and "uid" in ds:
            assert ds["uid"] in uids, f"unknown data source {ds['uid']}"
        if "url" in node and node.get("source") == "url":
            assert node["parser"] == "backend"
            assert node["url"].startswith(ADAPTER)
            path = node["url"][len(ADAPTER) - 1 :].split("?")[0]
            assert path in ENDPOINTS, path
            assert "${run" not in path, "the selected run goes in a query parameter"
            # Times go through Infinity's own macros, in seconds (plan §6.1).
            assert "${__from" not in node["url"] and "${__to" not in node["url"]


def _panels(dashboard: dict[str, Any]) -> Any:
    for panel in dashboard.get("panels", []):
        yield panel
        yield from panel.get("panels", [])  # inside a collapsed row


def test_only_the_lane_partition_transformation() -> None:
    for path in DASHBOARDS:
        for panel in _panels(json.loads(path.read_text())):
            for transformation in panel.get("transformations", []):
                assert transformation["id"] == "partitionByValues", path.name
                assert transformation["options"]["fields"] == ["lane"], path.name


def test_timeline_frame_fields_sort_start_before_stop() -> None:
    """Infinity returns columns alphabetically and State timeline takes the first
    time field as the start (spike gate 1.2)."""
    timeline = json.loads((GRAFANA / "dashboards" / "timeline.json").read_text())
    [panel] = [p for p in timeline["panels"] if p["type"] == "state-timeline"]
    times = [
        c["selector"] for c in panel["targets"][0]["columns"] if c["type"].startswith("timestamp")
    ]
    assert times == sorted(times) == ["start", "stop"]
    assert panel["options"]["mergeValues"] is False
    assert panel["fieldConfig"]["defaults"]["custom"]["lineWidth"] == 1
    # "Zoom to run" uses the padded bounds, never the (possibly equal) true extent.
    [zoom] = [link for link in panel["fieldConfig"]["defaults"]["links"] if "Zoom" in link["title"]]
    assert "zoom_from_ms" in zoom["url"] and "zoom_to_ms" in zoom["url"]


def test_timeline_shows_whether_its_data_is_complete() -> None:
    """Missing or stale data must be visible on the Timeline page itself (plan §5.3)."""
    timeline = json.loads((GRAFANA / "dashboards" / "timeline.json").read_text())
    [status] = [p for p in _panels(timeline) if p["title"] == "Data status"]
    [target] = status["targets"]
    assert target["url"].startswith(ADAPTER + "clips/status?")
    assert [c["selector"] for c in target["columns"]] == ["state", "message"]
    health = json.loads((GRAFANA / "dashboards" / "health.json").read_text())
    selected = {c["selector"] for p in _panels(health) for t in p["targets"] for c in t["columns"]}
    assert {"status", "last_error", "data_age_s"} <= selected


def test_time_series_puts_other_units_on_a_second_axis() -> None:
    """Axis 1: fields in the first field's units; axis 2, on the right: the rest.
    Generic: no field names or units in the dashboard."""
    timeline = json.loads((GRAFANA / "dashboards" / "timeline.json").read_text())
    panels = [p for p in _panels(timeline) if p["type"] == "timeseries"]
    assert {p["title"].split(":")[0] for p in panels} == {"Run time series", "Trend"}
    for panel in panels:
        axes = {t["refId"]: t["url"].rsplit("axis=", 1)[1] for t in panel["targets"]}
        assert axes == {"A": "1", "B": "2"}
        [right] = panel["fieldConfig"]["overrides"]
        assert right["matcher"] == {"id": "byFrameRefID", "options": "B"}
        assert right["properties"] == [{"id": "custom.axisPlacement", "value": "right"}]
        assert panel["fieldConfig"]["defaults"]["displayName"] == "${__field.labels.series}"


# Columns the adapter may return as null (docs/technical/endpoints.md), per table.
NULLABLE = {
    "Failed and aborted": {"reason"},
    "Streams": {"last_event"},
    "Run data (default view)": {"units", "value", "time"},
    "Fields of ${stream}": {"units", "shape", "dtype"},
    "Run metadata": {"value"},
}


def test_null_cells_never_show_the_empty_panel_text() -> None:
    """A table's default ``noValue`` also fills null cells: the text meant for an
    empty panel ("none in range") would appear as a value. Nullable columns
    override it with a dash; the notice line never claims a selection state."""
    timeline = json.loads((GRAFANA / "dashboards" / "timeline.json").read_text())
    panels = {p["title"]: p for p in _panels(timeline)}
    for title, nullable in NULLABLE.items():
        overrides = {
            o["matcher"]["options"]
            for o in panels[title]["fieldConfig"]["overrides"]
            if {"id": "noValue", "value": "\u2013"} in o["properties"]
        }
        assert nullable <= overrides, title
    notices = [p for p in _panels(timeline) if p["type"] == "stat" and not p["title"]]
    assert len(notices) == 2  # selected run's data, trend
    assert all(n["fieldConfig"]["defaults"]["noValue"] == "\u2013" for n in notices)


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_dashboard_has_no_secrets_or_instrument_tests(path: Path) -> None:
    text = path.read_text()
    assert not re.search(r"(?i)apikey|api_key|password|secret", text)
    assert not re.search(r"\bMOUSE\b|electrochemistry", text)
