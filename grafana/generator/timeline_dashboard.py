"""The Timeline dashboard (``grafana/dashboards/timeline.json``): the full
view, one function per row. Positions are relative to the row;
:func:`dash_parts.stack` places the rows."""

from __future__ import annotations

from typing import Any

import timeline_panels as tp
from dash_parts import Panel, stack

UID = "ett-timeline"
LINKS = tp.Links(UID)


def header() -> list[Panel]:
    """Always shown: what this is, the queues and whether the data is complete."""
    return [tp.help_text((0, 0, 5, 4), LINKS), tp.queue((5, 0, 11, 4)),
            tp.data_status((16, 0, 8, 4))]  # fmt: skip


def runs_and_selection() -> list[Panel]:
    """Left: the window's runs and the selected run; right: its data."""
    return [
        tp.runs_table("Runs in range", (0, 0, 7, 7), LINKS),
        tp.runs_table("Failed and aborted", (7, 0, 5, 7), LINKS, failures=True),
        tp.selected_run((0, 7, 7, 7)),
        tp.streams((7, 7, 5, 7), LINKS),
        *tp.run_data((12, 0, 12, 8)),
        tp.run_time_series((12, 8, 12, 6)),
    ]


def corrected_data() -> list[Panel]:
    """The selected run's corrected result; for a measurement, the adapter
    follows to its latest usable processing result."""
    return [
        tp.curve_notice((0, 0, 24, 2)),
        tp.corrected_curve((0, 2, 12, 9)),
        tp.corrected_image((12, 2, 12, 9)),
        tp.related_runs((0, 11, 24, 5), LINKS),
    ]


def dashboard() -> dict[str, Any]:
    panels = stack(header(), [
        ("Runs and the selected run", runs_and_selection(), False),
        ("Timeline", [tp.timeline((0, 0, 24, 10), LINKS)], False),
        ("Corrected data", corrected_data(), False),
        ("Trend: runs like the selected one",
         [tp.trend_notice((0, 0, 24, 2)),
          tp.trend((0, 2, 24, 8), "Trend: the selected run and the runs like it in range")],
         False),
        ("Run metadata", [tp.metadata((0, 0, 24, 10))], False),
        ("Stream fields (choose a stream in Streams)", [tp.stream_fields((0, 0, 24, 8))], True),
    ])  # fmt: skip
    return tp.dashboard(UID, panels)
