"""Static checks of the provisioned Grafana files (plan §10, AGENTS.md)."""

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GRAFANA = ROOT / "grafana"
DASHBOARDS = sorted((GRAFANA / "dashboards").glob("*.json"))
ADAPTER = "${ETT_ADAPTER_URL}"
"""The datasource's URL; panel queries are paths below it (Infinity prefixes it)."""
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
    "/selection/related",
    "/selection/curve",
    "/presets",
    "/selection/runs",
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
    assert ds["url"] == ADAPTER and ds["jsonData"]["allowedHosts"] == [ADAPTER + "/"]
    # Compose always sets it: Grafana's provisioning has no default values.
    compose = yaml.safe_load((GRAFANA.parent / "deploy" / "compose.yaml").read_text())
    assert compose["services"]["grafana"]["environment"]["ETT_ADAPTER_URL"]
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
            assert node["url"].startswith("/"), "a path below the datasource's URL"
            path = node["url"].split("?")[0]
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
    assert target["url"].startswith("/clips/status?")
    assert [c["selector"] for c in target["columns"]] == ["state", "message"]
    health = json.loads((GRAFANA / "dashboards" / "health.json").read_text())
    selected = {c["selector"] for p in _panels(health) for t in p["targets"] for c in t["columns"]}
    assert {"status", "last_error", "data_age_s"} <= selected


def test_time_series_puts_other_units_on_a_second_axis() -> None:
    """Axis 1: fields in the first field's units; axis 2, on the right: the rest.
    Generic: no field names or units in the dashboard."""
    for name in ("timeline.json", "overview.json"):
        dashboard = json.loads((GRAFANA / "dashboards" / name).read_text())
        panels = [p for p in _panels(dashboard) if p["type"] in ("timeseries", "xychart")
                  and any("axis=" in t.get("url", "") for t in p["targets"])]  # fmt: skip
        assert {p["title"].split(":")[0] for p in panels} == {"Run time series", "Trend"}
        for panel in panels:
            axes = {
                t["refId"]: parse_qs(urlsplit(t["url"]).query)["axis"] for t in panel["targets"]
            }
            assert axes == {"A": ["1"], "B": ["2"]}
            right = [o for o in panel["fieldConfig"]["overrides"]
                     if o["matcher"] == {"id": "byFrameRefID", "options": "B"}]  # fmt: skip
            assert len(right) == 1
            assert {"id": "custom.axisPlacement", "value": "right"} in right[0]["properties"]
            if panel["type"] == "timeseries":
                assert panel["fieldConfig"]["defaults"]["displayName"] == "${__field.labels.series}"
                continue
            # The run's time series: against the time since its start, wide rows.
            assert all("x=elapsed" in t["url"] and not t["columns"] for t in panel["targets"])
            assert panel["options"]["series"] == [
                {"x": {"matcher": {"id": "byName", "options": "elapsed_s"}}}
            ]


# Columns the adapter may return as null (docs/technical/endpoints.md), per table.
NULLABLE = {
    "Failed and aborted": {"reason"},
    "Streams": {"last_event"},
    "Run data (default view)": {"units", "value", "time", "y_lower", "y_upper"},
    "Related runs": {
        "family",
        "attempt",
        "mark",
        "problems",
        "status",
        "usable",
        "start",
        "reason",
    },
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
    assert len(notices) == 3  # selected run's data, corrected curve, trend
    assert all(n["fieldConfig"]["defaults"]["noValue"] == "\u2013" for n in notices)


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_dashboard_has_no_secrets_or_instrument_tests(path: Path) -> None:
    text = path.read_text()
    assert not re.search(r"(?i)apikey|api_key|password|secret", text)
    assert not re.search(r"\bMOUSE\b|electrochemistry", text)


def test_timeline_dashboard_is_in_collapsible_rows() -> None:
    """Every panel below the top strip is in a row; only Stream fields starts
    collapsed (its panel is queried only when opened)."""
    timeline = json.loads((GRAFANA / "dashboards" / "timeline.json").read_text())
    panels = timeline["panels"]
    rows = [p for p in panels if p["type"] == "row"]
    assert [r["collapsed"] for r in rows] == [False] * (len(rows) - 1) + [True]
    first_row = min(r["gridPos"]["y"] for r in rows)
    above = [p["title"] for p in panels if p["gridPos"]["y"] < first_row]
    assert set(above) == {"", "Queue", "Data status"}  # the help text has no title
    assert all(not r["panels"] for r in rows[:-1]) and rows[-1]["panels"]


def test_corrected_data_panels() -> None:
    """The curve on log–log axes with x chosen by name (Infinity orders columns
    alphabetically); the image from the browser through the proxy's signed-in
    route, relative to Grafana's origin, never from the adapter's address."""
    timeline = json.loads((GRAFANA / "dashboards" / "timeline.json").read_text())
    panels = {p["title"]: p for p in _panels(timeline)}
    curve = panels["Corrected curve"]
    assert curve["type"] == "xychart" and curve["options"]["mapping"] == "auto"
    assert curve["options"]["series"] == [{"x": {"matcher": {"id": "byName", "options": "x"}}}]
    scale = curve["fieldConfig"]["defaults"]["custom"]["scaleDistribution"]
    assert scale == {"type": "log", "log": 10}
    image = panels["Corrected image"]
    assert (image["type"], image["options"]["mode"]) == ("text", "html")
    [src] = re.findall(r'src="([^"]*)"', image["options"]["content"])
    assert src.startswith("/run-images/${run:percentencode}/image?")
    assert "t=${__to}" in src  # a new URL on each refresh: newer results appear
    assert "adapter" not in image["options"]["content"]


def test_timeline_dashboard_is_what_the_generator_writes() -> None:
    """``grafana/generator`` builds timeline.json; edit it, not the JSON, and
    run ``uv run --locked python grafana/generator``."""
    import importlib.util
    import sys

    generator = GRAFANA / "generator"
    sys.path.insert(0, str(generator))
    try:
        spec = importlib.util.spec_from_file_location("ett_generator", generator / "__main__.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        written = {name: module.render(build()) for name, build in module.GENERATED.items()}
    finally:
        sys.path.remove(str(generator))
    for name, text in written.items():
        assert text == (GRAFANA / "dashboards" / name).read_text(), (
            f"{name} differs from the generator's output: run `uv run --locked python "
            "grafana/generator`"
        )


def test_the_preset_picker_and_the_selection_state() -> None:
    """presets.md §11: an Infinity query variable on /presets (backend parser,
    title shown, id sent), and the hidden, URL-synced selection variables."""
    timeline = json.loads((GRAFANA / "dashboards" / "timeline.json").read_text())
    variables = {v["name"]: v for v in timeline["templating"]["list"]}
    assert list(variables) == ["preset", "run", "root", "lane", "unfold", "stream"]
    picker = variables["preset"]
    assert picker["type"] == "query" and picker["query"]["queryType"] == "infinity"
    assert picker["query"]["infinityQuery"]["parser"] == "backend"
    assert picker["query"]["meta"] == {"textField": "title", "valueField": "id"}
    assert not picker["multi"] and not picker["includeAll"] and not picker["skipUrlSync"]
    assert all(variables[n]["hide"] == 2 for n in ("run", "root", "lane", "unfold", "stream"))


def test_every_navigation_sets_each_variable_once() -> None:
    timeline = json.loads((GRAFANA / "dashboards" / "timeline.json").read_text())
    urls = [n["url"] for n in _walk(timeline)
            if "url" in n and str(n["url"]).startswith("/d/") and "?" in str(n["url"])]  # fmt: skip
    assert len(urls) >= 6
    # The top bar's links to the other timeline dashboards carry everything.
    for other in timeline["links"]:
        assert other["includeVars"] and other["keepTime"] and other["url"] == "/d/ett-overview"
    for url in urls:
        for name in ("run", "root", "lane", "preset", "unfold", "stream"):
            count = url.count(f"var-{name}=") + url.count(f"${{{name}:queryparam}}")
            assert count == 1, (name, url)
        assert url.count("${__url_time_range}") + url.count("from=") == 1, url
    [frame] = [p for p in _panels(timeline) if p["type"] == "state-timeline"]
    queries = frame["targets"][0]["url"]
    assert (
        "preset=${preset:percentencode}" in queries and "unfold=${unfold:percentencode}" in queries
    )
    [status] = [p for p in _panels(timeline) if p["title"] == "Data status"]
    assert "unfold=${unfold:percentencode}" in status["targets"][0]["url"]


def test_stacks_without_the_demo_need_no_demo_settings() -> None:
    """Compose interpolates every service, in an active profile or not: a
    required (":?") or unset ("${X}") variable in a demo service would break
    compose.yaml + compose.host.yaml on a host without deploy/.env."""
    compose = yaml.safe_load((GRAFANA.parent / "deploy" / "compose.yaml").read_text())
    for name, service in compose["services"].items():
        if "demo" not in service.get("profiles", []):
            continue
        for text in _strings(service):
            for match in re.finditer(r"\$\{([A-Z0-9_]+)([^}]*)\}", text):
                assert match.group(2).startswith(":-"), f"{name}: {match.group(0)}"


def _strings(node: Any) -> Any:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
