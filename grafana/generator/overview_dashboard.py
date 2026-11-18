"""The Overview dashboard (``grafana/dashboards/overview.json``): from the
generic at the top to the specific further down (presets.md §11; the
maintainer's sketch of 2026-11-18). Positions are relative to the row."""

from __future__ import annotations

from typing import Any

import timeline_panels as tp
from dash_parts import Panel, stack

UID = "ett-overview"
LINKS = tp.Links(UID)


def header() -> list[Panel]:
    """A narrow row: what this is, the queues, whether the data is complete."""
    return [tp.help_text((0, 0, 5, 3), LINKS), tp.queue((5, 0, 11, 3)),
            tp.data_status((16, 0, 8, 3))]  # fmt: skip


def selected_run() -> list[Panel]:
    """Three columns: the latest runs on the selected lane (the preset's runs
    before a selection); the selected run's streams; its state above its
    metadata."""
    return [
        tp.lane_runs_notice((0, 0, 8, 2)),
        tp.lane_runs((0, 2, 8, 10), LINKS),
        tp.streams((8, 0, 8, 12), LINKS, fields_below=False),
        tp.selected_run((16, 0, 8, 4)),
        tp.metadata((16, 4, 8, 8)),
    ]


def run_data() -> list[Panel]:
    """The selected run's curves: its event time series beside its corrected
    curve (for a measurement, its latest usable processing result's)."""
    return [
        tp.run_time_series_notice((0, 0, 12, 2)),
        tp.run_time_series((0, 2, 12, 8)),
        tp.curve_notice((12, 0, 12, 2)),
        tp.corrected_curve((12, 2, 12, 8)),
    ]


def dashboard() -> dict[str, Any]:
    panels = stack(header(), [
        ("Timeline", [tp.timeline((0, 0, 24, 10), LINKS)], False),
        ("Trend: the selected lane",
         [tp.trend_notice((0, 0, 24, 2)),
          tp.trend((0, 2, 24, 8), "Trend: the selected run and the runs like it on its lane")],
         False),
        ("The selected run", selected_run(), False),
        ("Its curves", run_data(), False),
    ])  # fmt: skip
    return tp.dashboard(UID, panels)
