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
ENDPOINTS = {"/health"}  # grows with the adapter


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
            assert node["url"][len(ADAPTER) - 1 :].split("?")[0] in ENDPOINTS


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_dashboard_has_no_secrets_or_instrument_tests(path: Path) -> None:
    text = path.read_text()
    assert not re.search(r"(?i)apikey|api_key|password|secret", text)
    assert not re.search(r"\bMOUSE\b|electrochemistry", text)
