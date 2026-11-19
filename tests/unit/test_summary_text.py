"""Readable summary values: times in a zone, durations in units."""

from __future__ import annotations

from datetime import UTC
from zoneinfo import ZoneInfo

import pytest

from event_timeline_tiled.adapter.runs import duration_text, readable, summary_rows


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0.25, "0.25 s"), (8.5, "8.5 s"), (723, "12 min 3 s"), (18120, "5 h 2 min"),
     (100800, "28 h 0 min"), (273600, "3 d 4 h"), (-90, "-1 min 30 s")],
)  # fmt: skip
def test_durations(seconds: float, text: str) -> None:
    assert duration_text(seconds) == text


def test_readable_values() -> None:
    berlin = ZoneInfo("Europe/Berlin")
    assert readable("start", 1_700_000_000.0, UTC) == "2023-11-14 22:13:20 UTC"
    assert readable("start", 1_700_000_000.0, berlin) == "2023-11-14 23:13:20 CET"
    assert readable("last_event_age_s", 61.2, UTC) == "1 min 1 s"
    assert readable("possibly_stalled", False, UTC) == "no"
    assert readable("reason", None, UTC) == ""
    assert readable("uid_mismatch", 3, UTC) == "3"  # numbers that are neither stay numbers
    assert readable("label", "S-1 · scan", UTC) == "S-1 · scan"


def test_rows_keep_the_value_and_add_text() -> None:
    rows = summary_rows({"uid": "u", "start": 1_700_000_000.0, "status": "success"})
    assert [r["key"] for r in rows] == ["status", "start", "uid"]
    assert rows[1] == {"key": "start", "value": 1_700_000_000.0,
                       "text": "2023-11-14 22:13:20 UTC"}  # fmt: skip
