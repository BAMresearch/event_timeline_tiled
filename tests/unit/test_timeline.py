"""Stale data and source state when Tiled stops answering (plan §5.2, §5.3)."""

from __future__ import annotations

from typing import Any

import httpx

from event_timeline_tiled.clips import frame
from event_timeline_tiled.layout import Layout, RunRecord
from event_timeline_tiled.settings import Settings
from event_timeline_tiled.tiled_http import TiledReader
from event_timeline_tiled.timeline import Timeline

RUNS = [
    {"id": "closed", "attributes": {"metadata": {
        "start": {"uid": "closed", "time": 100.0, "instrument": "MOUSE"},
        "stop": {"time": 200.0, "exit_status": "success"}}}},
    {"id": "open", "attributes": {"metadata": {
        "start": {"uid": "open", "time": 300.0, "instrument": "MOUSE"}}}},
]  # fmt: skip


class Upstream:
    """Answers searches until ``down`` is set; event tables for the open run."""

    def __init__(self) -> None:
        self.down = False
        self.table_reads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        path = request.url.path
        if path.startswith("/api/v1/table/"):
            self.table_reads += 1
            return httpx.Response(200, json={"seq_num": [1], "time": [350.0]})
        if path == "/api/v1/search/open":
            stream = {"id": "primary", "attributes": {"metadata": {"data_keys": {"x": {}}}}}
            return httpx.Response(200, json={"data": [stream], "links": {"next": None}})
        is_open = "filter[keypresent][condition][key]" in request.url.params
        data = [r for r in RUNS if ("stop" not in r["attributes"]["metadata"]) == is_open]
        return httpx.Response(200, json={"data": data, "links": {"next": None}})


RUN_RECORD = RunRecord("", "r", {"uid": "r", "time": 0.0}, None)


def _timeline(upstream: Upstream, clock: list[float]) -> Timeline:
    settings = Settings(tiled_url="http://tiled.test", tiled_api_key="k")
    reader = TiledReader(settings, transport=httpx.MockTransport(upstream))
    return Timeline(settings, Layout(reader), clock=lambda: clock[0])


def test_failure_shows_last_good_runs_as_stale() -> None:
    upstream, clock = Upstream(), [400.0]
    timeline = _timeline(upstream, clock)
    first = timeline.window(0, 1000)
    assert {c.uid: c.stale for c in first.clips} == {"closed": False, "open": False}
    assert first.open_until == 400.0

    upstream.down, clock[0] = True, 500.0
    second = timeline.window(0, 1000)
    assert {c.uid: c.stale for c in second.clips} == {"closed": True, "open": True}
    [source] = second.sources
    assert (source.error.value if source.error else None, source.last_success) == (
        "unreachable",
        400.0,
    )
    rows: dict[str, Any] = {r["uid"]: r for r in frame(second.clips, 0, 1000, second.open_until)}
    # The open clip stops growing at the last successful fetch.
    assert rows["open"]["stop"] == 400_000 and rows["open"]["state"] == "stale"


def test_last_event_is_read_at_most_once_per_refresh_interval() -> None:
    upstream, clock = Upstream(), [400.0]
    timeline = _timeline(upstream, clock)
    timeline.window(0, 1000)
    timeline.window(0, 1000)
    assert upstream.table_reads == 1
    clock[0] = 461.0
    [open_clip] = [c for c in timeline.window(0, 1000).clips if c.uid == "open"]
    assert upstream.table_reads == 2 and open_clip.activity.last_event == 350.0


def test_event_columns_come_back_in_seq_num_order() -> None:
    def shuffled(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"seq_num": [3, 1, 2], "time": [30.0, 10.0, 20.0], "x": [3, 1, 2]}
        )

    settings = Settings(tiled_url="http://tiled.test", tiled_api_key="k")
    layout = Layout(TiledReader(settings, transport=httpx.MockTransport(shuffled)))
    run = RUN_RECORD
    assert layout.event_columns(run, "primary", ["x"]) == {
        "seq_num": [1, 2, 3],
        "time": [10.0, 20.0, 30.0],
        "x": [1, 2, 3],
    }
