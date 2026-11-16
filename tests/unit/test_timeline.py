"""Stale data, source state and activity reads when Tiled misbehaves (plan §5.2, §5.3)."""

from __future__ import annotations

import threading
from typing import Any

import httpx

from event_timeline_tiled.clips import frame
from event_timeline_tiled.layout import Layout, RunRecord
from event_timeline_tiled.settings import Settings
from event_timeline_tiled.tiled_http import TiledReader
from event_timeline_tiled.timeline import Timeline


def item(key: str, start: float, stop: float | None = None, **md: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {"start": {"uid": key, "time": start, "instrument": "MOUSE", **md}}
    if stop is not None:
        metadata["stop"] = {"time": stop, "exit_status": "success"}
    return {"id": key, "attributes": {"metadata": metadata}}


class Upstream:
    """A fake Tiled: ``runs[root]`` is what a window search of that root
    returns (split into open and closed); roots in ``down`` refuse
    connections; ``tables`` is the status of event-table reads."""

    def __init__(self, runs: dict[str, list[dict[str, Any]]]) -> None:
        self.runs = runs
        self.down: set[str] = set()
        self.tables = 200
        self.table_reads = 0
        self._lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        if path.startswith("/table/"):
            with self._lock:
                self.table_reads += 1
            if self.tables != 200:
                return httpx.Response(self.tables)
            return httpx.Response(200, json={"time": [350.0, 340.0]})
        parts = path.removeprefix("/search/").strip("/").split("/") if path != "/search/" else []
        keys = {i["id"]: root for root, items in self.runs.items() for i in items}
        if parts and parts[-1] in keys:  # a run: its streams
            stream = {"id": "primary", "attributes": {"metadata": {"data_keys": {"x": {}}}}}
            return httpx.Response(200, json={"data": [stream], "links": {"next": None}})
        root = "/".join(parts)
        if root in self.down:
            raise httpx.ConnectError("refused", request=request)
        is_open = "filter[keypresent][condition][key]" in request.url.params
        data = [
            r
            for r in self.runs.get(root, [])
            if ("stop" not in r["attributes"]["metadata"]) == is_open
        ]
        return httpx.Response(200, json={"data": data, "links": {"next": None}})


def _timeline(upstream: Upstream, clock: list[float], roots: tuple[str, ...] = ()) -> Timeline:
    settings = Settings(tiled_url="http://tiled.test", tiled_api_key="k", catalog_roots=roots)
    reader = TiledReader(settings, transport=httpx.MockTransport(upstream))
    return Timeline(settings, Layout(reader), clock=lambda: clock[0])


def _rows(result: Any, t_from: float = 0, t_to: float = 10_000) -> dict[str, dict[str, Any]]:
    return {r["uid"]: r for r in frame(result.clips, t_from, t_to)}


def test_failure_shows_last_good_runs_as_stale() -> None:
    upstream, clock = Upstream({"": [item("closed", 100, 200), item("open", 300)]}), [400.0]
    timeline = _timeline(upstream, clock)
    first = timeline.window(0, 1000)
    assert {c.uid: c.stale for c in first.clips} == {"closed": False, "open": False}

    upstream.down, clock[0] = {""}, 500.0
    second = timeline.window(0, 1000)
    assert {c.uid: c.stale for c in second.clips} == {"closed": True, "open": True}
    [fetch] = second.fetches
    assert (fetch.ok, fetch.error, fetch.last_success, fetch.truncated) == (
        False,
        "unreachable",
        400.0,
        None,
    )
    rows = _rows(second, 0, 1000)
    # The open clip stops growing where it was last seen, and nothing is inferred.
    assert rows["open"]["stop"] == 400_000 and rows["open"]["state"] == "stale"
    assert rows["open"]["possibly_stalled"] is False


def test_one_failing_root_does_not_freeze_the_others() -> None:
    upstream = Upstream({"ra": [item("a", 100)], "rb": [item("b", 100)]})
    clock = [1000.0]
    timeline = _timeline(upstream, clock, roots=("ra", "rb"))
    timeline.window(0, 5000)
    upstream.down, clock[0] = {"ra"}, 1100.0
    result = timeline.window(0, 5000)
    rows = _rows(result, 0, 5000)
    assert (rows["a"]["stop"], rows["a"]["stale"]) == (1_000_000, True)
    assert (rows["b"]["stop"], rows["b"]["stale"]) == (1_100_000, False)
    assert [(f.root, f.ok) for f in result.fetches] == [("ra", False), ("rb", True)]


def test_a_root_that_never_answered_is_reported() -> None:
    upstream = Upstream({"ra": [item("a", 100)], "rb": [item("b", 100)]})
    upstream.down = {"rb"}
    result = _timeline(upstream, [1000.0], roots=("ra", "rb")).window(0, 5000)
    assert [c.uid for c in result.clips] == ["a"]
    [_, missing] = result.fetches
    assert (missing.ok, missing.last_success, missing.error) == (False, None, "unreachable")


def test_stale_records_keep_their_own_observation_time() -> None:
    # Seen open at 1000 in one window; a disjoint window succeeds at 2000;
    # the first window then fails: the run stops at 1000, not 2000.
    upstream, clock = Upstream({"": [item("r", 100)]}), [1000.0]
    timeline = _timeline(upstream, clock)
    timeline.window(0, 1500)
    upstream.runs[""], clock[0] = [], 2000.0
    timeline.window(1_000_000, 1_000_500)
    upstream.down, clock[0] = {""}, 2100.0
    rows = _rows(timeline.window(0, 1500), 0, 1500)
    assert (rows["r"]["stop"], rows["r"]["state"]) == (1_000_000, "stale")


def test_stale_fallback_applies_the_open_run_horizon() -> None:
    day = 86400.0
    upstream, clock = Upstream({"": [item("r", 100)]}), [1000.0]
    timeline = _timeline(upstream, clock)
    assert [c.uid for c in timeline.window(0, 2000).clips] == ["r"]
    upstream.down = {""}
    # 22 days later the run is beyond the 21-day horizon of that window.
    assert timeline.window(22 * day, 22 * day + 2000).clips == []
    assert [c.uid for c in timeline.window(0, 2000).clips] == ["r"]


def test_truncation_is_unknown_after_a_failure() -> None:
    runs = [item(f"r{i}", 100 + i, 200 + i) for i in range(3)]
    upstream, clock = Upstream({"": runs}), [1000.0]
    settings = Settings(tiled_url="http://tiled.test", tiled_api_key="k", max_runs=2)
    reader = TiledReader(settings, transport=httpx.MockTransport(upstream))
    timeline = Timeline(settings, Layout(reader), clock=lambda: clock[0])
    first = timeline.window(0, 1000)
    assert first.truncated and first.fetches[0].truncated is True
    upstream.down = {""}
    second = timeline.window(0, 1000)
    assert second.fetches[0].truncated is None and not second.fetches[0].ok


def test_last_event_is_read_at_most_once_per_refresh_interval() -> None:
    upstream, clock = Upstream({"": [item("open", 300)]}), [400.0]
    timeline = _timeline(upstream, clock)
    timeline.window(0, 1000)
    timeline.window(0, 1000)
    assert upstream.table_reads == 1
    clock[0] = 461.0
    [open_clip] = timeline.window(0, 1000).clips
    # The latest event, not the last row: the column is not sorted.
    assert upstream.table_reads == 2 and open_clip.activity.last_event == 350.0


def test_concurrent_requests_share_one_activity_read() -> None:
    upstream, clock = Upstream({"": [item("open", 300)]}), [400.0]
    timeline = _timeline(upstream, clock)
    threads = [threading.Thread(target=timeline.window, args=(0, 1000)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert upstream.table_reads == 1


def test_failed_activity_reads_are_reported_and_throttled() -> None:
    upstream, clock = Upstream({"": [item("open", 300)]}), [400.0]
    timeline = _timeline(upstream, clock)
    timeline.window(0, 1000)  # a good read: last event 350
    upstream.tables, clock[0] = 403, 470.0
    result = timeline.window(0, 1000)
    [clip] = result.clips
    assert (clip.activity.error, clip.activity.last_event) == ("forbidden", 350.0)
    assert clip.activity.possibly_stalled is False and clip.activity.age_s is None
    assert not clip.stale and _rows(result)["open"]["state"] == "activity unknown"
    [fetch] = result.fetches
    assert (fetch.ok, fetch.activity_errors, fetch.activity_error) == (True, 1, "forbidden")
    reads = upstream.table_reads
    clock[0] = 480.0
    timeline.window(0, 1000)
    timeline.window(0, 1000)
    assert upstream.table_reads == reads  # failures are throttled too
    upstream.tables, clock[0] = 200, 531.0
    [clip] = timeline.window(0, 1000).clips
    assert clip.activity.error is None and upstream.table_reads == reads + 1


def test_event_columns_come_back_in_seq_num_order() -> None:
    def shuffled(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"seq_num": [3, 1, 2], "time": [30.0, 10.0, 20.0], "x": [3, 1, 2]}
        )

    settings = Settings(tiled_url="http://tiled.test", tiled_api_key="k")
    layout = Layout(TiledReader(settings, transport=httpx.MockTransport(shuffled)))
    run = RunRecord("", "r", {"uid": "r", "time": 0.0}, None)
    assert layout.event_columns(run, "primary", ["x"]) == {
        "seq_num": [1, 2, 3],
        "time": [10.0, 20.0, 30.0],
        "x": [1, 2, 3],
    }
