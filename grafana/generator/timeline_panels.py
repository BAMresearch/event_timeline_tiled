"""Panels shared by the timeline dashboards: each builder takes its position
(relative to its row) and, where it navigates, the dashboard's :class:`Links`.
Dashboards test view kinds, never instrument names (AGENTS.md)."""

from __future__ import annotations

from typing import Any

from dash_parts import (
    ADAPTER as A,
)
from dash_parts import (
    PRESET,
    RANGE,
    RUN,
    Panel,
    blank,
    by_name,
    col,
    field,
    hidden_column,
    hide,
    link,
    nav,
    notice,
    panel,
    preset_variable,
    prop,
    target,
    text,
    two_axes,
    value_colours,
    width,
)

Pos = tuple[int, int, int, int]
UNFOLD = "unfold=${unfold:percentencode}"
LANE = "lane=${lane:percentencode}"
# The image comes from the browser through the proxy's signed-in route (plan §6);
# sanitising stays on. No run selected, or no image: the alt text shows.
# ${__to} changes on every refresh (relative ranges), so the browser asks again
# and a newer result appears without a reload (review 06, finding 2); the
# adapter ignores the parameter.
IMAGE = ('<img src="/run-images/${run:percentencode}/image?size=768&root=${root:percentencode}'
         '&t=${__to}" '
         'alt="No corrected image for the selected run (see the line above the curve)" '
         'style="display:block;margin:auto;max-width:100%;max-height:calc(100% - 1.6em)">'
         '<p style="text-align:center;margin:0.2em 0 0;opacity:0.7">The same result as the '
         "curve: named in the line above it.</p>")  # fmt: skip
NOTHING = "nothing to plot (see the line above)"
LANE_NAME = prop("displayName", "${__field.labels.lane}")
"""A timeline row's name: the partition's lane label."""

STATE_COLOURS = {
    "success": "green", "failed": "red", "aborted": "orange", "unknown": "purple",
    "open": "blue", "possibly stalled": "yellow", "activity unknown": "light-blue", "stale": "gray",
}  # fmt: skip
QUEUE_COLOURS = {"idle": "green", "executing_queue": "blue", "paused": "orange",
                 "unreachable": "red", "error": "red"}  # fmt: skip
COMPLETENESS = {"complete": "green", "partial": "orange", "stale": "red", "incomplete": "red"}
MAPPINGS = value_colours(STATE_COLOURS)
DASHBOARDS = {"ett-timeline": "Timeline", "ett-overview": "Overview"}
"""The dashboards these panels make; each links to the others."""


class Links:
    """Every navigation of one dashboard, from one transition table
    (presets.md §11): each variable once, kept or set from the clicked row
    (by id, never a display name)."""

    def __init__(self, uid: str) -> None:
        clicked = {"run": field("uid"), "root": field("root"), "lane": field("lane_id"),
                   "stream": field("stream")}  # fmt: skip
        zoom = 'from=${__data.fields["zoom_from_ms"]}&to=${__data.fields["zoom_to_ms"]}'
        self.select_bar = nav(uid, clicked)
        self.zoom = nav(uid, clicked, time=zoom)
        self.unfold = nav(uid, {"unfold": field("lane_id")})
        self.fold = nav(uid, {"unfold": ""})
        self.select_row = nav(uid, {"run": field("uid"), "root": field("root"),
                                    "lane": field("lane_key"), "stream": ""})  # fmt: skip
        self.select_lane_run = nav(uid, {"run": field("uid"), "root": field("root"),
                                         "lane": field("lane_id"), "stream": ""})  # fmt: skip
        self.select_related = nav(uid, {"run": field("uid"), "root": "", "lane": "",
                                        "stream": ""})  # fmt: skip
        self.stream = nav(uid, {"stream": field("stream")})


def status_cells(name: str) -> dict[str, Any]:
    return by_name(name, prop("custom.cellOptions", {"type": "color-text"}),
                   prop("mappings", MAPPINGS))  # fmt: skip


def help_text(pos: Pos, links: Links) -> Panel:
    return text("", pos, "markdown",
                "**Recorded runs from Tiled.** Click a run to select or unfold its lane. "
                "Green success · red failed · orange aborted · blue open · yellow possibly "
                "stalled · light blue activity unknown · grey stale. "
                f"[Fold]({links.fold}) · [Health](/d/ett-health/timeline-health)",
                transparent=True)  # fmt: skip


def queue(pos: Pos) -> Panel:
    return panel("table", "Queue", pos,
              [target(f"{A}/queues", "", [
                  col("instrument"), col("state"), col("running_plan"),
                  col("running_since", "timestamp_epoch_s"), col("items_in_queue", "number"),
                  col("run_progress", "number"), col("run_text")])],
              description="Each RE Manager's state and running plan. Progress counts the plans "
                          "finished since the queue started (or since the dashboard's adapter "
                          "first saw it running), against those plus the plans still queued: "
                          "plans, not time; the history before the run does not count.",
              options={"showHeader": True, "cellHeight": "sm"},
              fieldConfig={"defaults": {"noValue": "no RE Manager configured"}, "overrides": [
                  width("items_in_queue", 64), width("instrument", 80),
                  width("running_since", 150), width("state", 115), width("run_progress", 100),
                  width("run_text", 70),
                  by_name("items_in_queue", prop("displayName", "queued")),
                  by_name("run_text", prop("displayName", "run")),
                  by_name("run_progress", prop("displayName", "progress"),
                          prop("unit", "percentunit"), prop("min", 0), prop("max", 1),
                          prop("custom.cellOptions", {"type": "gauge", "mode": "basic",
                                                      "valueDisplayMode": "text"})),
                  *blank("items_in_queue", "running_plan", "running_since", "run_progress",
                         "run_text"),
                  by_name("state", prop("custom.cellOptions", {"type": "color-text"}),
                          prop("mappings", value_colours(QUEUE_COLOURS)))]})  # fmt: skip


def data_status(pos: Pos) -> Panel:
    return panel("table", "Data status", pos,
              [target(f"{A}/clips/status?{RANGE}&{PRESET}&{UNFOLD}", "issues",
                      [col("state"), col("message")])],
              options={"showHeader": False, "cellHeight": "sm"},
              fieldConfig={"defaults": {"noValue": "no status: adapter not answering"},
                           "overrides": [
                  width("state", 90), by_name("message", prop("custom.wrapText", True)),
                  by_name("state", prop("custom.cellOptions", {"type": "color-background"}),
                          prop("mappings", value_colours(COMPLETENESS)))]})  # fmt: skip


def runs_table(title: str, pos: Pos, links: Links, failures: bool = False) -> Panel:
    """The preset's runs in range (``failures``: only failed and aborted ones)."""
    query = f"{A}/clips?{RANGE}&{PRESET}" + ("&status=failed,aborted" if failures else "")
    shown = [col("label"), col("reason") if failures else col("start", "timestamp_epoch_s")]
    shown += [col("start", "timestamp_epoch_s")] if failures else [col("status")]
    return panel("table", title, pos,
              [target(query, "clips", [*shown, col("uid"), col("root"), col("lane_key")])],
              options={"showHeader": True, "sortBy": [{"displayName": "start", "desc": True}]},
              fieldConfig={"defaults": {"noValue": "none in range"} if failures else {},
                           "overrides": [
                  hidden_column("uid"), hidden_column("root"), hidden_column("lane_key"),
                  link("label", "Select run", links.select_row),
                  *(blank("reason") if failures else [status_cells("status")])]})  # fmt: skip


def lane_runs(pos: Pos, links: Links) -> Panel:
    """The latest runs on the selected lane; before a selection, the preset's."""
    return panel("table", "Latest runs on the lane", pos,
              [target(f"{A}/selection/runs?{RUN}&{RANGE}&{PRESET}&{LANE}&limit=50", "rows", [
                  col("label"), col("plan_name"), col("start", "timestamp_epoch_s"),
                  col("status"), col("uid"), col("root"), col("lane_id")])],
              description="The selected lane's latest runs, newest first, each once (the "
                          "line above names the lane); before a selection, the preset's runs.",
              options={"showHeader": True, "cellHeight": "sm"},
              fieldConfig={"defaults": {"noValue": "no runs"}, "overrides": [
                  hidden_column("uid"), hidden_column("root"), hidden_column("lane_id"),
                  link("label", "Select run", links.select_lane_run), status_cells("status"),
                  width("status", 80), width("start", 160), *blank("plan_name")]})  # fmt: skip


def lane_runs_notice(pos: Pos) -> Panel:
    return notice(pos, f"{A}/selection/runs?{RUN}&{RANGE}&{PRESET}&{LANE}&limit=50")


def selected_run(pos: Pos) -> Panel:
    return panel("table", "Selected run", pos,
              [target(f"{A}/selection/run?{RUN}", "summary", [col("key"), col("value")])],
              description="The selected run's state: status, duration, reason, activity.",
              options={"showHeader": False, "cellHeight": "sm"},
              fieldConfig={"defaults": {}, "overrides": [width("key", 140)]})  # fmt: skip


def streams(pos: Pos, links: Links, fields_below: bool = True) -> Panel:
    title = "Show fields (below the timeline)" if fields_below else "Select stream"
    return panel("table", "Streams", pos,
              [target(f"{A}/selection/streams?{RUN}", "", [
                  col("stream"), col("events", "number"),
                  col("last_event", "timestamp_epoch_s")])],
              options={"showHeader": True},
              fieldConfig={"defaults": {"noValue": "no run selected, or no streams"},
                           "overrides": [
                  width("events", 70), width("last_event", 160), *blank("last_event"),
                  link("stream", title, links.stream)]})  # fmt: skip


def run_data(pos: Pos) -> list[Panel]:
    """The selected run's default view: a notice line above a table."""
    x, y, w, h = pos
    return [
        notice((x, y, w, 2), f"{A}/selection/data?{RUN}"),
        panel("table", "Run data (default view)", (x, y + 2, w, h - 2),
              [target(f"{A}/selection/data?{RUN}", "rows")],
              options={"showHeader": True},
              fieldConfig={"defaults": {"noValue": "no rows"}, "overrides": [
                  by_name("time", prop("unit", "dateTimeAsIso")),
                  width("seq_num", 75), width("units", 60), width("time", 170),
                  width("stream", 150),
                  *blank("units", "value", "time", "y_lower", "y_upper")]}),
    ]  # fmt: skip


def run_time_series(pos: Pos) -> Panel:
    # Two queries: fields in the first field's units (left axis), the others (right).
    return two_axes(
        "Run time series", pos, f"{A}/selection/data?{RUN}&kind=timeseries&max_points=2000", NOTHING
    )


def run_time_series_notice(pos: Pos) -> Panel:
    return notice(pos, f"{A}/selection/data?{RUN}&kind=timeseries&max_points=2000&axis=1")


def timeline(pos: Pos, links: Links) -> Panel:
    """Lanes from the preset; partitioned by the adapter's final ``lane``
    column, the only dashboard transformation."""
    helpers = ("status", "uid", "clip_from_ms", "clip_to_ms", "zoom_from_ms", "zoom_to_ms",
               "possibly_stalled", "stale", "activity_error", "lane_id", "track", "root",
               "stream", "stream_from_ms", "stream_to_ms")  # fmt: skip
    return panel("state-timeline", "Timeline", pos,
              [target(f"{A}/clips/frame?{RANGE}&{PRESET}&{UNFOLD}", "", [
                  col("lane"), col("start", "timestamp_epoch"), col("stop", "timestamp_epoch"),
                  col("state"), col("status"), col("uid"), col("label"),
                  col("clip_from_ms", "number"), col("clip_to_ms", "number"),
                  col("zoom_from_ms", "number"), col("zoom_to_ms", "number"),
                  col("possibly_stalled", "boolean"), col("stale", "boolean"),
                  col("activity_error"), col("lane_id"), col("track"), col("root"),
                  col("stream"), col("stream_from_ms", "number"),
                  col("stream_to_ms", "number")])],
              transformations=[{"id": "partitionByValues", "options": {
                  "fields": ["lane"], "keepFields": False, "naming": {"asLabels": True}}}],
              options={"mergeValues": False, "showValue": "never", "alignValue": "left",
                       "rowHeight": 0.85, "legend": {"showLegend": False},
                       "tooltip": {"mode": "single"}},
              fieldConfig={
                  "defaults": {"custom": {"fillOpacity": 85, "lineWidth": 1, "spanNulls": False},
                               "color": {"mode": "fixed", "fixedColor": "gray"},
                               "mappings": MAPPINGS,
                               "links": [{"title": "Select run", "url": links.select_bar},
                                         {"title": "Zoom to run", "url": links.zoom},
                                         {"title": "Unfold lane into streams",
                                          "url": links.unfold},
                                         {"title": "Fold", "url": links.fold}]},
                  "overrides": [*[hide(n) for n in helpers], hide("label", tooltip=False),
                                by_name("state", LANE_NAME)]})  # fmt: skip


CURVE = f"{A}/selection/curve?{RUN}&max_points=2000"


def curve_notice(pos: Pos) -> Panel:
    return notice(pos, CURVE)


def corrected_curve(pos: Pos) -> Panel:
    bounds = [by_name(name, prop("displayName", label),
                      prop("custom.lineStyle", {"fill": "dash", "dash": [4, 4]}),
                      prop("color", {"mode": "fixed", "fixedColor": "gray"}))
              for name, label in (("y_lower", "lower bound (−σ)"),
                                  ("y_upper", "upper bound (+σ)"))]  # fmt: skip
    return panel("xychart", "Corrected curve", pos,
              [target(CURVE, "rows", [col("x", "number"), col("y", "number"),
                                      col("y_lower", "number"), col("y_upper", "number")])],
              description="The curve view of the selected processing run, or of the selected "
                          "measurement's latest usable processing result, on log–log axes. The "
                          "bounds are y ∓ its uncertainty. Names and units are in the line above.",
              options={"mapping": "auto",
                       "series": [{"x": {"matcher": {"id": "byName", "options": "x"}}}],
                       "legend": {"showLegend": True, "displayMode": "list",
                                  "placement": "bottom"},
                       "tooltip": {"mode": "multi"}},
              fieldConfig={"defaults": {"noValue": NOTHING, "unit": "sci",
                                        "custom": {"show": "lines", "lineWidth": 1,
                                                   "scaleDistribution": {"type": "log",
                                                                         "log": 10}}},
                           "overrides": [
                  by_name("y", prop("displayName", "corrected"), prop("custom.lineWidth", 2),
                          prop("color", {"mode": "fixed", "fixedColor": "blue"})),
                  *bounds]})  # fmt: skip


def corrected_image(pos: Pos) -> Panel:
    return text("Corrected image", pos, "html", IMAGE,
                description="The image view of the selected processing run, or of the selected "
                            "measurement's latest usable processing result: log scale, colours "
                            "from the 1st to the 99.5th percentile, zero and negative values "
                            "transparent.")  # fmt: skip


def related_runs(pos: Pos, links: Links) -> Panel:
    return panel("table", "Related runs", pos,
              [target(f"{A}/selection/related?{RUN}", "rows", [
                  col("relation"), col("label"), col("family"), col("attempt", "number"),
                  col("mark"), col("problems"), col("status"), col("usable", "boolean"),
                  col("start", "timestamp_epoch_s"), col("reason"), col("uid")])],
              description="A measurement's processing runs (the latest attempt and the latest "
                          "usable result marked per family), or a processing run's measurements.",
              options={"showHeader": True, "cellHeight": "sm",
                       "sortBy": [{"displayName": "start", "desc": True}]},
              fieldConfig={"defaults": {"noValue": "no related runs"}, "overrides": [
                  hidden_column("uid"), link("label", "Select run", links.select_related),
                  status_cells("status"), width("relation", 105), width("attempt", 75),
                  width("status", 80), width("usable", 75), width("mark", 220),
                  width("start", 160),
                  *blank("family", "attempt", "mark", "problems", "status", "usable", "start",
                         "reason")]})  # fmt: skip


TREND = f"{A}/selection/trend?{RUN}&{RANGE}&{PRESET}&{LANE}&max_points=2000"


def trend_notice(pos: Pos) -> Panel:
    return notice(pos, f"{TREND}&axis=1")


def trend(pos: Pos, title: str) -> Panel:
    return two_axes(title, pos, TREND, NOTHING,
                    description="The selected run's time series view over every run in range on "
                                "the selected lane (the line above names it) with the same plan "
                                "name and the same view: one line per field, broken between "
                                "runs.")  # fmt: skip


def metadata(pos: Pos) -> Panel:
    return panel("table", "Run metadata", pos,
              [target(f"{A}/selection/run?{RUN}", "metadata", [
                  col("document"), col("key"), col("value")])],
              description="The selected run's start and stop documents, one row per key "
                          "(nested keys dotted). Long values end in … where they were cut.",
              options={"showHeader": True, "cellHeight": "sm"},
              fieldConfig={"defaults": {"noValue": "no run selected"}, "overrides": [
                  width("document", 90), width("key", 280), *blank("value"),
                  by_name("value", prop("custom.wrapText", True))]})  # fmt: skip


def stream_fields(pos: Pos) -> Panel:
    return panel("table", "Fields of ${stream}", pos,
              [target(f"{A}/selection/fields?{RUN}&stream=${{stream:percentencode}}", "", [
                  col("field"), col("dtype"), col("shape"), col("units"),
                  col("hinted", "boolean"), col("external", "boolean")])],
              options={"showHeader": True},
              fieldConfig={"defaults": {"noValue": "choose a stream in Streams"},
                           "overrides": [*blank("units", "shape", "dtype")]})  # fmt: skip


def dashboard(uid: str, panels: list[Panel], time_from: str = "now-12h") -> dict[str, Any]:
    """A timeline dashboard: the shared variables, refresh and links to the
    other timeline dashboards (keeping the time range and the selection)."""
    hidden = {"type": "textbox", "hide": 2, "query": "", "current": {"text": "", "value": ""}}
    state = ("run", "root", "lane", "unfold", "stream")
    others = [{"title": title, "type": "link", "url": f"/d/{other}", "icon": "dashboard",
               "keepTime": True, "includeVars": True, "targetBlank": False, "tags": [],
               "asDropdown": False, "tooltip": ""}
              for other, title in DASHBOARDS.items() if other != uid]  # fmt: skip
    return {
        "uid": uid,
        "title": DASHBOARDS[uid],
        "tags": ["event-timeline-tiled"],
        "schemaVersion": 41,
        "editable": False,
        "graphTooltip": 1,
        "refresh": "30s",
        "time": {"from": time_from, "to": "now"},
        "timepicker": {"refresh_intervals": ["10s", "30s", "1m", "5m"]},
        "links": others,
        "templating": {"list": [preset_variable(), *({"name": n, **hidden} for n in state)]},
        "panels": panels,
    }
