"""Queue status: read-only by construction, unreachable is never idle (plan §5.5)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from event_timeline_tiled.adapter import create_app
from event_timeline_tiled.queues import (
    READ_ONLY_INTERFACE,
    READ_REQUESTS,
    QueueMonitor,
    QueueRun,
    ReadOnlyQueue,
)
from event_timeline_tiled.settings import QueueConfig, Settings, parse_queues

ZMQ = QueueConfig("MOUSE", "zmq", "tcp://re-manager:60615")


class FakeAPI:
    """Stands in for REManagerAPI, with control methods that must stay unreachable."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.controls: list[str] = []
        self.closed = False

    def status(self, *, reload: bool = False) -> dict[str, Any]:
        if self.fail:
            raise self.fail
        return {"manager_state": "executing_queue", "re_state": "running", "items_in_queue": 2,
                "worker_environment_exists": True, "queue_stop_pending": False}  # fmt: skip

    def queue_get(self, *, reload: bool = False) -> dict[str, Any]:
        return {
            "running_item": {"name": "demo_measurement", "properties": {"time_start": 100.0}},
            "items": [{"name": "demo_measurement"}, {"name": "count"}],
        }

    def close(self) -> None:
        self.closed = True

    def queue_start(self) -> None:
        self.controls.append("queue_start")

    def environment_close(self) -> None:
        self.controls.append("environment_close")


def test_wrapper_offers_reads_only() -> None:
    """The interface the adapter's code uses. Not a sandbox: the bound methods
    still reference the API object (see the transport tests for what is sent)."""
    api = FakeAPI()
    wrapper = ReadOnlyQueue(ZMQ, lambda _c: api)
    public = {name for name in dir(wrapper) if not name.startswith("_")}
    assert public == READ_ONLY_INTERFACE
    held = [getattr(wrapper, slot) for slot in ReadOnlyQueue.__slots__ if slot != "_lock"]
    assert all(value is not api for value in held)  # no attribute holds the API object
    for control in ("queue_start", "environment_close", "re_pause", "item_add", "manager_stop"):
        assert not hasattr(wrapper, control)
    with pytest.raises(AttributeError):
        wrapper.api = api  # type: ignore[attr-defined]  # no way to attach it later
    assert wrapper.status()["manager_state"] == "executing_queue" and api.controls == []


def test_rows_from_a_running_manager() -> None:
    monitor = QueueMonitor((ZMQ,), lambda _c: FakeAPI(), clock=lambda: 130.0)
    [row] = monitor.rows()
    assert row["reachable"] and row["manager_state"] == "executing_queue"
    assert (row["running_plan"], row["running_since"]) == ("demo_measurement", 100.0)
    assert (row["items_in_queue"], row["next_items"]) == (2, "demo_measurement, count")
    assert (row["error"], row["data_age_s"]) == (None, 0.0)


def test_unreachable_is_never_idle() -> None:
    class RequestTimeoutError(Exception):
        pass

    apis = [FakeAPI(), FakeAPI(fail=RequestTimeoutError("no reply"))]
    clock = [10.0]
    monitor = QueueMonitor((ZMQ,), lambda _c: apis.pop(0), clock=lambda: clock[0])
    monitor.rows()
    # The connection breaks: the next API object times out.
    monitor._states["MOUSE"].queue = ReadOnlyQueue(
        ZMQ, lambda _c: FakeAPI(fail=RequestTimeoutError())
    )
    clock[0] = 25.0
    [row] = monitor.rows()
    assert (row["reachable"], row["manager_state"], row["error"]) == (False, None, "unreachable")
    assert row["state"] == "unreachable"
    assert (row["last_success"], row["data_age_s"]) == (10.0, 15.0)


def test_queue_settings() -> None:
    env = {"ETT_QUEUE_MOUSE_2_API_KEY_FILE": ""}
    configs = parse_queues("MOUSE=zmq:tcp://h:60615, mouse-2=http:http://h:60610", env)
    assert [(c.instrument, c.transport) for c in configs] == [("MOUSE", "zmq"), ("mouse-2", "http")]
    assert QueueConfig.env_name("mouse-2") == "ETT_QUEUE_MOUSE_2_API_KEY_FILE"
    for bad in (
        "MOUSE=zmq:http://h:1",
        "MOUSE=ftp:x",
        "MOUSE=http:http://u:p@h:1",
        "A=zmq:tcp://h:1,A=zmq:tcp://h:2",
    ):
        with pytest.raises(ValueError):
            parse_queues(bad, {})


def test_http_key_comes_from_a_file(tmp_path: Any) -> None:
    key = tmp_path / "queue.key"
    key.write_text("secret-key\n")
    [config] = parse_queues("M=http:http://h:60610", {"ETT_QUEUE_M_API_KEY_FILE": str(key)})
    assert config.api_key == "secret-key" and "secret-key" not in repr(config)


def test_queues_endpoint() -> None:
    settings = Settings(tiled_url="http://tiled.test", tiled_api_key="k", queues=(ZMQ,))
    with TestClient(create_app(settings, queue_api=lambda _c: FakeAPI())) as client:
        [row] = client.get("/queues").json()
    assert row["instrument"] == "MOUSE" and row["reachable"] is True
    empty = Settings(tiled_url="http://tiled.test", tiled_api_key="k")
    with TestClient(create_app(empty)) as client:
        assert client.get("/queues").json() == []


def test_http_transport_sends_only_reads_with_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every request bluesky-queueserver-api 0.0.14 would send over HTTP."""
    from bluesky_queueserver_api.comm_threads import ReManagerComm_HTTP_Threads

    sent: list[tuple[str, str | None]] = []

    def record(self: Any, *, method: str, **_kw: Any) -> dict[str, Any]:
        sent.append((method, self.auth_key))
        if method == "status":
            return {"success": True, "msg": "", "manager_state": "idle", "re_state": "idle",
                    "items_in_queue": 0, "plan_queue_uid": "q", "running_item_uid": None,
                    "worker_environment_exists": False, "queue_stop_pending": False}  # fmt: skip
        return {"success": True, "msg": "", "items": [], "running_item": {}, "plan_queue_uid": "q"}

    monkeypatch.setattr(ReManagerComm_HTTP_Threads, "_simple_request", record)
    config = QueueConfig("M", "http", "http://queue.test:60610", api_key="read-only-test-key")
    monitor = QueueMonitor((config,))
    [row] = monitor.rows()
    monitor.rows()
    monitor.close()
    assert row["state"] == "idle"
    assert {method for method, _ in sent} <= READ_REQUESTS
    assert {key for _, key in sent} == {"read-only-test-key"}


def _run_after(*steps: tuple[str, int, int, bool]) -> QueueRun:
    run = QueueRun()
    for t, (state, history, queued, running) in enumerate(steps):
        run.observe(state, history, queued, running, float(t))
    return run


def test_progress_counts_from_the_queue_start_not_the_history() -> None:
    """Ventilating: two plans added to an idle queue with a long history."""
    idle = ("idle", 30_000, 0, False)
    queued = ("idle", 30_000, 2, False)
    first = ("executing_queue", 30_000, 1, True)
    second = ("executing_queue", 30_001, 0, True)
    assert _run_after(idle).fields()["run_total"] is None  # no run seen yet
    started = _run_after(idle, queued, first).fields()
    assert started == {**started, "run_active": True, "run_done": 0, "run_total": 2,
                       "run_progress": 0.0, "run_text": "0 of 2", "run_from_start": True,
                       "run_since": 2.0}  # fmt: skip
    assert _run_after(idle, queued, first, second).fields()["run_text"] == "1 of 2"
    finished = _run_after(idle, queued, first, second, ("idle", 30_002, 0, False)).fields()
    assert (finished["run_active"], finished["run_text"], finished["run_progress"]) == (
        False, "2 of 2", 1.0,
    )  # fmt: skip


def test_progress_seen_only_mid_run_counts_from_first_sight() -> None:
    fields = _run_after(("executing_queue", 500, 3, True)).fields()
    assert (fields["run_text"], fields["run_from_start"]) == ("0 of 4", False)


def test_progress_of_a_stopped_paused_or_growing_queue() -> None:
    start = [("idle", 10, 3, False), ("executing_queue", 10, 2, True)]
    paused = _run_after(*start, ("paused", 11, 1, True)).fields()
    assert (paused["run_active"], paused["run_text"]) == (True, "1 of 3")
    grown = _run_after(*start, ("executing_queue", 11, 4, True)).fields()
    assert grown["run_text"] == "1 of 6"  # items added during the run
    stopped = _run_after(*start, ("executing_queue", 11, 1, True), ("idle", 12, 1, False))
    assert stopped.fields()["run_text"] == "2 of 3, stopped"
    cleared = _run_after(*start, ("executing_queue", 0, 1, True)).fields()
    assert (cleared["run_text"], cleared["run_from_start"]) == ("0 of 2", False)


def test_progress_ignores_status_without_counts() -> None:
    run = _run_after(("idle", 5, 0, False))
    run.observe("executing_queue", None, 2, True, 9.0)
    assert run.fields()["run_total"] is None and not run.active


def test_rows_carry_the_run_progress() -> None:
    statuses = iter(
        [
            {"manager_state": "idle", "items_in_queue": 2, "items_in_history": 7},
            {"manager_state": "executing_queue", "items_in_queue": 1, "items_in_history": 7},
        ]
    )

    class Scripted(FakeAPI):
        def status(self, *, reload: bool = False) -> dict[str, Any]:
            return next(statuses)

    monitor = QueueMonitor((ZMQ,), lambda _c: Scripted(), clock=lambda: 130.0)
    first, second = monitor.rows()[0], monitor.rows()[0]
    assert first["run_text"] is None and second["run_text"] == "0 of 2"
    assert second["run_progress"] == 0.0 and second["run_since"] == 130.0
