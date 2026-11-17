"""A stream table Tiled does not have (review 06, finding 4)."""

from typing import Any

import pytest

from event_timeline_tiled.layout import Layout, RunRecord
from event_timeline_tiled.tiled_http import HealthStatus, SourceError


class _NoTables:
    def table_columns(self, path: str, columns: Any) -> dict[str, list[Any]]:
        raise SourceError(HealthStatus.NOT_FOUND, "HTTP 404")


def _run(stop: dict[str, Any] | None) -> RunRecord:
    return RunRecord("", "r", {"uid": "r", "time": 0.0}, stop)


def test_open_runs_and_streams_without_events_have_no_rows_yet() -> None:
    layout = Layout(_NoTables())  # type: ignore[arg-type]
    empty = {"seq_num": [], "time": []}
    assert layout.event_columns(_run(None), "primary") == empty  # before its first batch
    no_events = {"time": 1.0, "exit_status": "success", "num_events": {"primary": 0}}
    assert layout.event_columns(_run(no_events), "primary") == empty
    unreported = {"time": 1.0, "exit_status": "success"}
    assert layout.event_columns(_run(unreported), "primary") == empty


def test_a_finished_stream_with_events_but_no_table_is_an_error() -> None:
    layout = Layout(_NoTables())  # type: ignore[arg-type]
    stop = {"time": 1.0, "exit_status": "success", "num_events": {"primary": 4}}
    with pytest.raises(SourceError, match="reports events but has no table"):
        layout.event_columns(_run(stop), "primary")
