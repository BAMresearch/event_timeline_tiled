"""Queue status: read-only by construction, unreachable is never idle (plan §5.5)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from event_timeline_tiled.adapter import create_app
from event_timeline_tiled.queues import READ_ONLY_INTERFACE, QueueMonitor, ReadOnlyQueue
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


def test_wrapper_exposes_reads_only() -> None:
    api = FakeAPI()
    wrapper = ReadOnlyQueue(ZMQ, lambda _c: api)
    public = {name for name in dir(wrapper) if not name.startswith("_")}
    assert public == READ_ONLY_INTERFACE
    held = [getattr(wrapper, slot) for slot in ReadOnlyQueue.__slots__ if slot != "_lock"]
    assert all(value is not api for value in held)  # the API object itself is not kept
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
