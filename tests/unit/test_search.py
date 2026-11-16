"""Search paging, truncation and the window union against a fake paged Tiled,
and how the adapter reports incomplete data (plan §5.3, §5.4)."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi.testclient import TestClient

from event_timeline_tiled.adapter import create_app
from event_timeline_tiled.layout import Layout
from event_timeline_tiled.settings import Settings
from event_timeline_tiled.tiled_http import TiledReader


def item(key: str, start: float, stop: float | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"start": {"uid": key, "time": start, "instrument": "MOUSE"}}
    if stop is not None:
        metadata["stop"] = {"time": stop, "exit_status": "success"}
    return {"id": key, "attributes": {"metadata": metadata}}


class Paged:
    """Cursor paging like Tiled 0.2.21: ``page[limit]`` items after
    ``page[cursor]``, and a ``next`` link with only the paging parameters
    while more items follow. ``on_search`` runs before each search page."""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.limits: list[int] = []
        self.searches: list[str] = []
        self.on_search: Any = None
        self.fail_filtered = False

    def matching(self, request: httpx.Request) -> list[dict[str, Any]]:
        is_open = "filter[keypresent][condition][key]" in request.url.params
        return [i for i in self.items if ("stop" not in i["attributes"]["metadata"]) == is_open]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if not request.url.path.startswith("/api/v1/search"):
            return httpx.Response(200, json={"library_version": "0.2.21"})
        filtered = any(k.startswith("filter[") for k in params)
        if filtered and self.fail_filtered:
            return httpx.Response(500)
        if self.on_search and filtered:
            self.on_search(request)
        self.searches.append("open" if "filter[keypresent][condition][key]" in params else "x")
        items = self.matching(request) if filtered else self.items
        limit = int(params["page[limit]"])
        self.limits.append(limit)
        start = int(params.get("page[cursor]", 0))
        page = items[start : start + limit]
        more = start + limit < len(items)
        query = f"page[cursor]={start + limit}&page[limit]={limit}"
        next_link = f"http://tiled.test/api/v1/search/?{query}"
        return httpx.Response(
            200, json={"data": page, "links": {"next": next_link if more else None}}
        )


def _reader(upstream: Paged, **settings: Any) -> tuple[Settings, TiledReader]:
    s = Settings(tiled_url="http://tiled.test", tiled_api_key="k", **settings)
    return s, TiledReader(s, transport=httpx.MockTransport(upstream))


def test_exactly_max_items_is_not_truncated() -> None:
    upstream = Paged([item("a", 1, 2), item("b", 1, 2)])
    _, reader = _reader(upstream)
    items, truncated = reader.search("", max_items=2)
    assert (len(items), truncated) == (2, False)
    assert upstream.limits == [3]  # asked for one more than it may return


def test_one_more_than_max_items_is_truncated() -> None:
    upstream = Paged([item(k, 1, 2) for k in "abc"])
    _, reader = _reader(upstream)
    items, truncated = reader.search("", max_items=2)
    assert ([i["id"] for i in items], truncated) == (["a", "b"], True)


def test_paging_stops_at_max_items_plus_one(monkeypatch: Any) -> None:
    from event_timeline_tiled import tiled_http

    monkeypatch.setattr(tiled_http, "PAGE_LIMIT", 2)
    upstream = Paged([item(str(k), 1, 2) for k in range(10)])
    _, reader = _reader(upstream)
    items, truncated = reader.search("", max_items=5)
    assert (len(items), truncated, upstream.limits) == (5, True, [2, 2, 2])


def test_open_runs_survive_the_cap_and_the_open_search_goes_first() -> None:
    closed = [item(f"c{i}", 10 + i, 20 + i) for i in range(5)]
    upstream = Paged([*closed, item("o", 50)])
    _, reader = _reader(upstream)
    runs, truncated = Layout(reader).window_runs("", 0, 100, 86400, 3)
    assert truncated and len(runs) == 3
    assert "o" in {r.key for r in runs}
    assert upstream.searches[0] == "open"


def test_a_run_closing_between_the_searches_is_not_lost() -> None:
    upstream = Paged([item("r", 10)])

    def close_after_first_search(request: httpx.Request) -> None:
        if upstream.searches:  # the second search sees the run closed
            upstream.items = [item("r", 10, 20)]

    upstream.on_search = close_after_first_search
    _, reader = _reader(upstream)
    runs, _ = Layout(reader).window_runs("", 0, 100, 86400, 100)
    assert [(r.key, r.stop is not None) for r in runs] == [("r", True)]


def test_status_reports_a_failing_window_search_behind_a_good_probe() -> None:
    upstream = Paged([item("a", 10, 20)])
    upstream.fail_filtered = True
    settings, reader = _reader(upstream)
    with TestClient(create_app(settings, reader, clock=lambda: 1000.0)) as client:
        status = client.get("/clips/status", params={"from": 0, "to": 100}).json()
        [health] = client.get("/health").json()
    assert (status["state"], status["complete"]) == ("incomplete", False)
    assert status["issues"][0]["message"] == "/: error, no data from this root yet"
    # The live probe (unfiltered) is fine; the window fetch failed: both are shown.
    assert (health["status"], health["last_error"]) == ("ok", "error")


def test_status_reports_truncation_and_a_missing_root() -> None:
    upstream = Paged([item(f"c{i}", 10 + i, 20 + i) for i in range(3)])
    settings, reader = _reader(upstream, max_runs=2, catalog_roots=("", "other"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/search/other"):
            raise httpx.ConnectError("refused", request=request)
        return upstream(request)

    reader = TiledReader(settings, transport=httpx.MockTransport(handler))
    with TestClient(create_app(settings, reader, clock=lambda: 1000.0)) as client:
        status = client.get("/clips/status", params={"from": 0, "to": 100}).json()
        clips = client.get("/clips", params={"from": 0, "to": 100}).json()
    assert status["state"] == "incomplete" and status["truncated"] is True
    messages = [i["message"] for i in status["issues"]]
    assert any("more than 2 runs" in m for m in messages)
    assert "other: unreachable, no data from this root yet" in messages
    assert [(s["root"], s["ok"], s["truncated"]) for s in clips["sources"]] == [
        ("/", True, True),
        ("other", False, None),
    ]


def test_complete_status() -> None:
    settings, reader = _reader(Paged([item("a", 10, 20)]))
    with TestClient(create_app(settings, reader, clock=lambda: 1000.0)) as client:
        status = client.get("/clips/status", params={"from": 0, "to": 100}).json()
    assert (status["state"], status["runs"]) == ("complete", 1)
    [issue] = status["issues"]
    assert issue["state"] == "complete" and "21 days" in issue["message"]
