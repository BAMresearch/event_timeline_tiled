"""Queue status against a real RE Manager (bluesky-queueserver, Redis) with the
demo profile in deploy/queue/startup. The test itself drives the queue to get a
running plan; the adapter's code under test only reads."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from event_timeline_tiled.queues import QueueMonitor
from event_timeline_tiled.settings import QueueConfig

STARTUP = Path(__file__).parents[2] / "deploy" / "queue" / "startup"
REDIS = shutil.which("redis-server")
if REDIS is None and os.environ.get("ETT_REQUIRE_REDIS") == "1":
    pytest.fail("ETT_REQUIRE_REDIS=1 but redis-server is not installed", pytrace=False)
pytestmark = [
    pytest.mark.queueserver,
    pytest.mark.skipif(REDIS is None, reason="redis-server not installed"),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait(condition: Callable[[], bool], timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if condition():
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError(what)


@pytest.fixture(scope="module")
def manager(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Any, str]]:
    from bluesky_queueserver_api.zmq import REManagerAPI

    assert REDIS is not None
    tmp = tmp_path_factory.mktemp("re-manager")
    redis_port, control, info = _free_port(), _free_port(), _free_port()
    redis = subprocess.Popen(
        [REDIS, "--port", str(redis_port), "--save", "", "--appendonly", "no", "--dir", str(tmp)],
        stdout=subprocess.DEVNULL,
    )
    log = (tmp / "manager.log").open("w")
    process = subprocess.Popen(
        [str(Path(sys.executable).parent / "start-re-manager"),
         "--startup-dir", str(STARTUP),
         "--existing-plans-devices", str(tmp / "existing_plans_and_devices.yaml"),
         "--update-existing-plans-devices", "ENVIRONMENT_OPEN",
         "--user-group-permissions", str(STARTUP / "user_group_permissions.yaml"),
         "--redis-addr", f"localhost:{redis_port}",
         "--zmq-control-addr", f"tcp://127.0.0.1:{control}",
         "--zmq-info-addr", f"tcp://127.0.0.1:{info}"],
        start_new_session=True, stdout=log, stderr=subprocess.STDOUT,
    )  # fmt: skip
    control_api = REManagerAPI(zmq_control_addr=f"tcp://127.0.0.1:{control}", timeout_recv=5)
    try:
        _wait(lambda: control_api.status(reload=True)["manager_state"] == "idle", 60, "start")
        yield control_api, f"tcp://127.0.0.1:{control}"
    finally:
        try:
            control_api.manager_stop(option="safe_off")
            process.wait(timeout=30)
        except Exception:
            process.kill()
        control_api.close()
        redis.terminate()
        redis.wait(timeout=10)


def test_idle_running_and_unreachable(manager: tuple[Any, str]) -> None:
    from bluesky_queueserver_api import BPlan

    control_api, address = manager
    monitor = QueueMonitor((QueueConfig("demo", "zmq", address),))
    [row] = monitor.rows()
    assert (row["reachable"], row["manager_state"], row["error"]) == (True, "idle", None)
    assert row["environment_open"] is False and row["items_in_queue"] == 0

    # The test, not the adapter, drives the queue.
    control_api.environment_open()
    control_api.wait_for_idle(timeout=60)
    control_api.item_add(BPlan("demo_measurement", duration=5))
    control_api.item_add(BPlan("demo_measurement", duration=1))
    control_api.queue_start()
    _wait(lambda: monitor.rows()[0]["manager_state"] == "executing_queue", 30, "running")
    [row] = monitor.rows()
    assert row["running_plan"] == "demo_measurement" and row["environment_open"] is True
    assert row["items_in_queue"] == 1 and row["next_items"] == "demo_measurement"
    assert isinstance(row["running_since"], float) and row["running_since"] <= time.time()
    control_api.wait_for_idle(timeout=60)
    control_api.environment_close()
    control_api.wait_for_idle(timeout=60)
    monitor.close()


def test_nothing_listening_is_unreachable() -> None:
    monitor = QueueMonitor((QueueConfig("demo", "zmq", f"tcp://127.0.0.1:{_free_port()}"),))
    [row] = monitor.rows()
    assert (row["reachable"], row["manager_state"], row["error"]) == (False, None, "unreachable")
    monitor.close()
