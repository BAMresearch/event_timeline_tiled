"""The Timeline dashboard (``grafana/dashboards/timeline.json``), one function
per row. Positions are relative to the row; :func:`dash_parts.stack` places
the rows. Dashboards test view kinds, never instrument names (AGENTS.md)."""

from __future__ import annotations

from typing import Any

from dash_parts import (
    ADAPTER as A,
)
from dash_parts import (
    RANGE,
    RUN,
    Panel,
    blank,
    by_name,
    col,
    hidden_column,
    hide,
    link,
    notice,
    panel,
    prop,
    stack,
    target,
    text,
    two_axes,
    value_colours,
    width,
)

MIN_LANES = 4
"""The timeline always has room for this many lanes: fewer are padded with
empty ones (by the adapter), so that one lane is not one huge bar."""
SELECT = '/d/ett-timeline/timeline?var-run=${__data.fields["uid"]}&${__url_time_range}'
ZOOM = ('/d/ett-timeline/timeline?var-run=${__data.fields["uid"]}'
        '&from=${__data.fields["zoom_from_ms"]}&to=${__data.fields["zoom_to_ms"]}')  # fmt: skip
STREAM = ('/d/ett-timeline/timeline?${run:queryparam}&var-stream=${__data.fields["stream"]}'
          "&${__url_time_range}")  # fmt: skip
# The image comes from the browser through the proxy's signed-in route (plan §6);
# sanitising stays on. No run selected, or no image: the alt text shows.
# ${__to} changes on every refresh (relative ranges), so the browser asks again
# and a newer result appears without a reload (review 06, finding 2); the
# adapter ignores the parameter.
IMAGE = ('<img src="/run-images/${run:percentencode}/image?size=768&t=${__to}" '
         'alt="No corrected image for the selected run (see the line above the curve)" '
         'style="display:block;margin:auto;max-width:100%;max-height:calc(100% - 1.6em)">'
         '<p style="text-align:center;margin:0.2em 0 0;opacity:0.7">The same result as the '
         "curve: named in the line above it.</p>")  # fmt: skip
NOTHING = "nothing to plot (see the line above)"

STATE_COLOURS = {
    "success": "green", "failed": "red", "aborted": "orange", "unknown": "purple",
    "open": "blue", "possibly stalled": "yellow", "activity unknown": "light-blue", "stale": "gray",
}  # fmt: skip
QUEUE_COLOURS = {"idle": "green", "executing_queue": "blue", "paused": "orange",
                 "unreachable": "red", "error": "red"}  # fmt: skip
COMPLETENESS = {"complete": "green", "partial": "orange", "stale": "red", "incomplete": "red"}
MAPPINGS = value_colours(STATE_COLOURS)


def status_cells(name: str) -> dict[str, Any]:
    return by_name(name, prop("custom.cellOptions", {"type": "color-text"}),
                   prop("mappings", MAPPINGS))  # fmt: skip


def header() -> list[Panel]:
    """Always shown: what this is, the queues and whether the data is complete."""
    return [
        text("", (0, 0, 5, 4), "markdown",
             "**Recorded runs from Tiled.** Click a run to select it. Green "
             "success · red failed · orange aborted · blue open · yellow possibly "
             "stalled · light blue activity unknown · grey stale. "
             "[Health](/d/ett-health/timeline-health)", transparent=True),
        panel("table", "Queue", (5, 0, 11, 4),
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
                          prop("mappings", value_colours(QUEUE_COLOURS)))]}),
        panel("table", "Data status", (16, 0, 8, 4),
              [target(f"{A}/clips/status?{RANGE}", "issues", [col("state"), col("message")])],
              options={"showHeader": False, "cellHeight": "sm"},
              fieldConfig={"defaults": {"noValue": "no status: adapter not answering"},
                           "overrides": [
                  width("state", 90), by_name("message", prop("custom.wrapText", True)),
                  by_name("state", prop("custom.cellOptions", {"type": "color-background"}),
                          prop("mappings", value_colours(COMPLETENESS)))]}),
    ]  # fmt: skip


def runs_and_selection() -> list[Panel]:
    """Left: the window's runs and the selected run; right: its data."""
    return [
        panel("table", "Runs in range", (0, 0, 7, 7),
              [target(f"{A}/clips?{RANGE}", "clips", [
                  col("label"), col("start", "timestamp_epoch_s"), col("status"), col("uid")])],
              options={"showHeader": True, "sortBy": [{"displayName": "start", "desc": True}]},
              fieldConfig={"defaults": {}, "overrides": [
                  hidden_column("uid"), link("label", "Select run", SELECT),
                  status_cells("status")]}),
        panel("table", "Failed and aborted", (7, 0, 5, 7),
              [target(f"{A}/clips?{RANGE}&status=failed,aborted", "clips", [
                  col("label"), col("reason"), col("start", "timestamp_epoch_s"), col("uid")])],
              options={"showHeader": True, "sortBy": [{"displayName": "start", "desc": True}]},
              fieldConfig={"defaults": {"noValue": "none in range"}, "overrides": [
                  hidden_column("uid"), link("label", "Select run", SELECT), *blank("reason")]}),
        panel("table", "Selected run", (0, 7, 7, 7),
              [target(f"{A}/selection/run?{RUN}", "summary", [col("key"), col("value")])],
              options={"showHeader": False},
              fieldConfig={"defaults": {}, "overrides": [width("key", 140)]}),
        panel("table", "Streams", (7, 7, 5, 7),
              [target(f"{A}/selection/streams?{RUN}", "", [
                  col("stream"), col("events", "number"),
                  col("last_event", "timestamp_epoch_s")])],
              options={"showHeader": True},
              fieldConfig={"defaults": {"noValue": "no run selected, or no streams"},
                           "overrides": [
                  width("events", 70), width("last_event", 160), *blank("last_event"),
                  link("stream", "Show fields (below the timeline)", STREAM)]}),
        notice((12, 0, 12, 2), f"{A}/selection/data?{RUN}"),
        panel("table", "Run data (default view)", (12, 2, 12, 6),
              [target(f"{A}/selection/data?{RUN}", "rows")],
              options={"showHeader": True},
              fieldConfig={"defaults": {"noValue": "no rows"}, "overrides": [
                  by_name("time", prop("unit", "dateTimeAsIso")),
                  width("seq_num", 75), width("units", 60), width("time", 170),
                  width("stream", 150),
                  *blank("units", "value", "time", "y_lower", "y_upper")]}),
        # Two queries: fields in the first field's units (left axis), the others (right).
        two_axes("Run time series", (12, 8, 12, 6),
                 f"{A}/selection/data?{RUN}&kind=timeseries&max_points=2000", NOTHING),
    ]  # fmt: skip


def timeline() -> list[Panel]:
    """One lane per instrument (and station), processing below; partitioned by
    the adapter's final ``lane`` column, the only dashboard transformation."""
    helpers = ("status", "uid", "clip_from_ms", "clip_to_ms", "zoom_from_ms", "zoom_to_ms",
               "possibly_stalled", "stale", "activity_error")  # fmt: skip
    return [
        panel("state-timeline", "Timeline", (0, 0, 24, 10),
              [target(f"{A}/clips/frame?{RANGE}&min_lanes={MIN_LANES}", "", [
                  col("lane"), col("start", "timestamp_epoch"), col("stop", "timestamp_epoch"),
                  col("state"), col("status"), col("uid"), col("label"),
                  col("clip_from_ms", "number"), col("clip_to_ms", "number"),
                  col("zoom_from_ms", "number"), col("zoom_to_ms", "number"),
                  col("possibly_stalled", "boolean"), col("stale", "boolean"),
                  col("activity_error")])],
              transformations=[{"id": "partitionByValues", "options": {
                  "fields": ["lane"], "keepFields": False, "naming": {"asLabels": True}}}],
              options={"mergeValues": False, "showValue": "never", "alignValue": "left",
                       "rowHeight": 0.85, "legend": {"showLegend": False},
                       "tooltip": {"mode": "single"}},
              fieldConfig={
                  "defaults": {"custom": {"fillOpacity": 85, "lineWidth": 1, "spanNulls": False},
                               "color": {"mode": "fixed", "fixedColor": "gray"},
                               "mappings": MAPPINGS,
                               "links": [{"title": "Select run", "url": SELECT},
                                         {"title": "Zoom to run", "url": ZOOM}]},
                  "overrides": [*[hide(n) for n in helpers], hide("label", tooltip=False),
                                by_name("state", prop("displayName", "${__field.labels.lane}"))]}),
    ]  # fmt: skip


def corrected_data() -> list[Panel]:
    """The selected run's corrected result; for a measurement, the adapter
    follows to its latest usable processing result."""
    curve = f"{A}/selection/curve?{RUN}&max_points=2000"
    bounds = [by_name(name, prop("displayName", label),
                      prop("custom.lineStyle", {"fill": "dash", "dash": [4, 4]}),
                      prop("color", {"mode": "fixed", "fixedColor": "gray"}))
              for name, label in (("y_lower", "lower bound (−σ)"),
                                  ("y_upper", "upper bound (+σ)"))]  # fmt: skip
    return [
        notice((0, 0, 24, 2), curve),
        panel("xychart", "Corrected curve", (0, 2, 12, 9),
              [target(curve, "rows", [col("x", "number"), col("y", "number"),
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
                  *bounds]}),
        text("Corrected image", (12, 2, 12, 9), "html", IMAGE,
             description="The image view of the selected processing run, or of the selected "
                         "measurement's latest usable processing result: log scale, colours "
                         "from the 1st to the 99.5th percentile, zero and negative values "
                         "transparent."),
        panel("table", "Related runs", (0, 11, 24, 5),
              [target(f"{A}/selection/related?{RUN}", "rows", [
                  col("relation"), col("label"), col("family"), col("attempt", "number"),
                  col("mark"), col("problems"), col("status"), col("usable", "boolean"),
                  col("start", "timestamp_epoch_s"), col("reason"), col("uid")])],
              description="A measurement's processing runs (the latest attempt and the latest "
                          "usable result marked per family), or a processing run's measurements.",
              options={"showHeader": True, "cellHeight": "sm",
                       "sortBy": [{"displayName": "start", "desc": True}]},
              fieldConfig={"defaults": {"noValue": "no related runs"}, "overrides": [
                  hidden_column("uid"), link("label", "Select run", SELECT),
                  status_cells("status"), width("relation", 105), width("attempt", 75),
                  width("status", 80), width("usable", 75), width("mark", 220),
                  width("start", 160),
                  *blank("family", "attempt", "mark", "problems", "status", "usable", "start",
                         "reason")]}),
    ]  # fmt: skip


def trend() -> list[Panel]:
    """The selected run's time series over the runs like it in range."""
    return [
        notice((0, 0, 24, 2), f"{A}/selection/trend?{RUN}&{RANGE}&max_points=2000&axis=1"),
        two_axes("Trend: the selected run and the runs like it in range", (0, 2, 24, 8),
                 f"{A}/selection/trend?{RUN}&{RANGE}&max_points=2000", NOTHING,
                 description="The selected run's time series view over every run in range on "
                             "the same lane with the same plan name and the same view: one "
                             "line per field, broken between runs."),
    ]  # fmt: skip


def run_metadata() -> list[Panel]:
    return [
        panel("table", "Run metadata", (0, 0, 24, 10),
              [target(f"{A}/selection/run?{RUN}", "metadata", [
                  col("document"), col("key"), col("value")])],
              description="The selected run's start and stop documents, one row per key "
                          "(nested keys dotted). Long values end in … where they were cut.",
              options={"showHeader": True, "cellHeight": "sm"},
              fieldConfig={"defaults": {"noValue": "no run selected"}, "overrides": [
                  width("document", 90), width("key", 280), *blank("value"),
                  by_name("value", prop("custom.wrapText", True))]}),
    ]  # fmt: skip


def stream_fields() -> list[Panel]:
    """Collapsed: queried only when opened."""
    return [
        panel("table", "Fields of ${stream}", (0, 0, 24, 8),
              [target(f"{A}/selection/fields?{RUN}&stream=${{stream:percentencode}}", "", [
                  col("field"), col("dtype"), col("shape"), col("units"),
                  col("hinted", "boolean"), col("external", "boolean")])],
              options={"showHeader": True},
              fieldConfig={"defaults": {"noValue": "choose a stream in Streams"},
                           "overrides": [*blank("units", "shape", "dtype")]}),
    ]  # fmt: skip


def dashboard() -> dict[str, Any]:
    panels = stack(header(), [
        ("Runs and the selected run", runs_and_selection(), False),
        ("Timeline", timeline(), False),
        ("Corrected data", corrected_data(), False),
        ("Trend: runs like the selected one", trend(), False),
        ("Run metadata", run_metadata(), False),
        ("Stream fields (choose a stream in Streams)", stream_fields(), True),
    ])  # fmt: skip
    hidden = {"type": "textbox", "hide": 2, "query": "", "current": {"text": "", "value": ""}}
    return {
        "uid": "ett-timeline",
        "title": "Timeline",
        "tags": ["event-timeline-tiled"],
        "schemaVersion": 41,
        "editable": False,
        "graphTooltip": 1,
        "refresh": "30s",
        "time": {"from": "now-12h", "to": "now"},
        "timepicker": {"refresh_intervals": ["10s", "30s", "1m", "5m"]},
        "templating": {"list": [{"name": "run", **hidden}, {"name": "stream", **hidden}]},
        "panels": panels,
    }
