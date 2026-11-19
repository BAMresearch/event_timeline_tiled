"""Building blocks for the provisioned dashboards: Infinity queries to the
adapter, field overrides, and stacking rows of panels into a grid.

Every query goes to the adapter with Infinity's backend parser (AGENTS.md).
Infinity returns columns in alphabetical order whatever ``columns`` says, so
column names, not their order here, decide a table's column order.
"""

from __future__ import annotations

from typing import Any

Panel = dict[str, Any]

DS = {"type": "yesoreyeram-infinity-datasource", "uid": "timeline-adapter"}
ADAPTER = ""
"""Panel URLs are paths: Infinity 4.1.6 prefixes the datasource's URL
(``ETT_ADAPTER_URL``, grafana/provisioning/datasources/adapter.yaml)."""
RANGE = "from=${__timeFrom:date:seconds}&to=${__timeTo:date:seconds}"
"""The dashboard's time range in epoch seconds, as the adapter takes it."""
RUN = "uid=${run:percentencode}&root=${root:percentencode}"
"""The selected run and its catalogue root, as query parameters (may be empty)."""
PRESET = "preset=${preset:percentencode}"
"""The chosen preset (presets.md §11)."""
SELECTION = ("run", "root", "lane", "preset", "unfold", "stream")
"""The dashboard state every navigation carries (presets.md §11)."""


def field(name: str) -> str:
    """A clicked row's field, percent-encoded for a URL."""
    return f'${{__data.fields["{name}"]:percentencode}}'


def nav(dashboard: str, changes: dict[str, str], time: str = "${__url_time_range}") -> str:
    """A link to ``dashboard`` that sets the variables in ``changes`` (``""``
    clears one) and keeps the others: each variable exactly once (presets.md
    §11's transition table)."""
    params = [f"var-{n}={changes[n]}" if n in changes else f"${{{n}:queryparam}}"
              for n in SELECTION]  # fmt: skip
    return f"/d/{dashboard}?" + "&".join([*params, time])


def preset_variable() -> dict[str, Any]:
    """The preset picker: an Infinity query variable on ``/presets``, which
    lists the deployment's default first, so the picker starts there."""
    query = {key: value for key, value in target(f"{ADAPTER}/presets", "",
                                                 [col("id"), col("title")]).items()
             if key not in ("refId", "datasource")}  # fmt: skip
    return {
        "name": "preset", "label": "Preset", "type": "query", "datasource": DS,
        "query": {"queryType": "infinity", "refId": "variable", "infinityQuery": query,
                  "meta": {"textField": "title", "valueField": "id"}},
        "refresh": 1, "multi": False, "includeAll": False, "options": [],
        "current": {}, "hide": 0, "skipUrlSync": False,
    }  # fmt: skip


WIDTH = 24
"""Grafana's grid width."""


def col(name: str, kind: str = "string") -> dict[str, str]:
    return {"selector": name, "text": name, "type": kind}


def target(
    url: str, root: str = "", columns: list[dict[str, str]] | tuple[()] = (), fmt: str = "table"
) -> dict[str, Any]:
    return {"refId": "A", "datasource": DS, "type": "json", "source": "url", "parser": "backend",
            "format": fmt, "url": url, "url_options": {"method": "GET", "data": ""},
            "root_selector": root, "columns": list(columns)}  # fmt: skip


def panel(
    kind: str, title: str, pos: tuple[int, int, int, int], targets: list[Any], **extra: Any
) -> Panel:
    """A panel at ``pos`` = (x, y, w, h), y relative to its row."""
    p = {"type": kind, "title": title, "gridPos": dict(zip("xywh", pos, strict=True)),
         "datasource": DS, "targets": targets}  # fmt: skip
    p.update(extra)
    return p


def text(
    title: str, pos: tuple[int, int, int, int], mode: str, content: str, **extra: Any
) -> Panel:
    """A built-in Text panel (no query; HTML sanitising stays on)."""
    p: Panel = {"type": "text", "title": title, "gridPos": dict(zip("xywh", pos, strict=True))}
    p.update(extra)
    p["options"] = {"mode": mode, "content": content}
    return p


def by_name(name: str, *properties: dict[str, Any]) -> dict[str, Any]:
    """A field override for the field called ``name``."""
    return {"matcher": {"id": "byName", "options": name}, "properties": list(properties)}


def prop(key: str, value: Any) -> dict[str, Any]:
    return {"id": key, "value": value}


def hide(name: str, tooltip: bool = True) -> dict[str, Any]:
    """Hidden from the plot and legend (and the tooltip, by default), but kept
    for data links."""
    return by_name(name, prop("custom.hideFrom", {"viz": True, "legend": True, "tooltip": tooltip}))


def hidden_column(name: str) -> dict[str, Any]:
    return by_name(name, prop("custom.hidden", True))


def width(name: str, px: int) -> dict[str, Any]:
    return by_name(name, prop("custom.width", px))


def blank(*names: str) -> list[dict[str, Any]]:
    """Null cells show a dash, not the panel's "no data" text."""
    return [by_name(n, prop("noValue", "–")) for n in names]


def link(name: str, title: str, url: str) -> dict[str, Any]:
    return by_name(name, prop("links", [{"title": title, "url": url}]))


def value_colours(colours: dict[str, str]) -> list[dict[str, Any]]:
    """Value mappings that colour each value."""
    return [{"type": "value", "options": {
        k: {"color": c, "index": i} for i, (k, c) in enumerate(colours.items())}}]  # fmt: skip


def notice(pos: tuple[int, int, int, int], url: str) -> Panel:
    """One line of text: the ``notice`` of an adapter response."""
    return panel("stat", "", pos, [target(url, "", [col("notice")])], transparent=True,
                 options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^notice$/",
                                            "values": False},
                          "colorMode": "none", "graphMode": "none", "textMode": "value",
                          "justifyMode": "auto", "text": {"valueSize": 14}},
                 fieldConfig={"defaults": {"noValue": "–"}, "overrides": []})  # fmt: skip


def two_axes(
    title: str,
    pos: tuple[int, int, int, int],
    url: str,
    no_value: str,
    description: str | None = None,
) -> Panel:
    """A time series of long rows: fields in the first field's units on the
    left axis (query A), the others on the right (query B)."""
    extra = {"description": description} if description else {}
    columns = [col("time", "timestamp_epoch"), col("field"), col("series"), col("value", "number")]
    queries = [dict(target(f"{url}&axis={axis}", "rows", columns, fmt="timeseries"), refId=ref)
               for axis, ref in ((1, "A"), (2, "B"))]  # fmt: skip
    return panel(
        "timeseries", title, pos, queries,
        options={"legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                 "tooltip": {"mode": "multi"}},
        fieldConfig={"defaults": {"displayName": "${__field.labels.series}", "noValue": no_value,
                                  "custom": {"drawStyle": "line", "showPoints": "auto"}},
                     "overrides": [{"matcher": {"id": "byFrameRefID", "options": "B"},
                                    "properties": [prop("custom.axisPlacement", "right")]}]},
        **extra,
    )  # fmt: skip


def _height(panels: list[Panel]) -> int:
    return max((p["gridPos"]["y"] + p["gridPos"]["h"] for p in panels), default=0)


def _moved(p: Panel, dy: int) -> Panel:
    return dict(p, gridPos=dict(p["gridPos"], y=p["gridPos"]["y"] + dy))


def stack(top: list[Panel], rows: list[tuple[str, list[Panel], bool]]) -> list[Panel]:
    """The ``top`` panels, then one row per ``(title, panels, collapsed)``,
    each row's panels below its title. Any viewer may collapse a row (not
    remembered on reload); a collapsed row holds its panels, which Grafana
    queries only when it is opened."""
    out = list(top)
    y = _height(top)
    for title, panels, collapsed in rows:
        inside = [_moved(p, y + 1) for p in panels]
        out.append({"type": "row", "title": title, "collapsed": collapsed,
                    "panels": inside if collapsed else [],
                    "gridPos": {"x": 0, "y": y, "w": WIDTH, "h": 1}})  # fmt: skip
        if not collapsed:
            out += inside
        y += 1 + (0 if collapsed else _height(panels))
    return sorted(out, key=lambda p: (p["gridPos"]["y"], p["gridPos"]["x"]))
