"""Queue status from RE Managers, read-only (plan §5.5).

The adapter reads each configured RE Manager through ``bluesky-queueserver-api``
(0MQ straight to the RE Manager, or HTTP through Bluesky HTTP Server). The
adapter's code uses the API only through :class:`ReadOnlyQueue`, which offers
the two read calls (``status``, ``queue_get``) and ``close``. Do not widen it.

The wrapper narrows what the adapter's code *uses*; it is not a security
boundary: its bound methods still reference the API object, which has the
control calls. What keeps the adapter read-only is that it never calls
anything else, and tests record every request it sends at the transport
level (0MQ and HTTP) and allow only ``status`` and ``queue_get``. Enforcement
outside the adapter exists only over HTTP, where the server's API key is
limited to ``read:status`` and ``read:queue``; the 0MQ control port accepts
any command from anyone who can reach it.

An RE Manager that cannot be reached is reported as unreachable, never idle.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from attrs import define, field

from .settings import QueueConfig

READ_ONLY_INTERFACE = frozenset({"instrument", "transport", "status", "queue", "close"})
"""Everything :class:`ReadOnlyQueue` offers; a test keeps it that way."""
READ_REQUESTS = frozenset({"status", "queue_get"})
"""The only requests the adapter sends to an RE Manager (checked at the transport by tests)."""

ApiFactory = Callable[[QueueConfig], Any]


def _make_api(config: QueueConfig) -> Any:  # the API package is untyped
    if config.transport == "zmq":
        from bluesky_queueserver_api.zmq import REManagerAPI as ZmqAPI

        return ZmqAPI(  # type: ignore[no-untyped-call]
            zmq_control_addr=config.address, timeout_recv=2.0, timeout_send=0.5
        )
    from bluesky_queueserver_api.http import REManagerAPI as HttpAPI

    api = HttpAPI(http_server_uri=config.address, timeout=5.0)  # type: ignore[no-untyped-call]
    if config.api_key:
        api.set_authorization_key(api_key=config.api_key)  # type: ignore[no-untyped-call]
    return api


class ReadOnlyQueue:
    """One RE Manager's status and queue: the only calls the adapter makes."""

    __slots__ = ("_close", "_lock", "_queue_get", "_status", "instrument", "transport")

    def __init__(self, config: QueueConfig, factory: ApiFactory = _make_api) -> None:
        api = factory(config)
        self.instrument = config.instrument
        self.transport = config.transport
        # Keep the two read calls and close, not the API object as an attribute.
        # (The bound methods still reference it: a narrow interface, not a sandbox.)
        self._status: Callable[..., Mapping[str, Any]] = api.status
        self._queue_get: Callable[..., Mapping[str, Any]] = api.queue_get
        self._close: Callable[[], None] = api.close
        self._lock = threading.Lock()

    def status(self) -> Mapping[str, Any]:
        with self._lock:
            return self._status(reload=True)

    def queue(self) -> Mapping[str, Any]:
        with self._lock:
            return self._queue_get(reload=True)

    def close(self) -> None:
        self._close()


def _category(exc: Exception) -> str:
    name = type(exc).__name__
    unreachable = ("Timeout", "Connect", "Connection", "Unreachable")
    return "unreachable" if any(word in name for word in unreachable) else "error"


def _item_name(item: Any) -> str | None:
    if isinstance(item, Mapping) and isinstance(item.get("name"), str):
        return str(item["name"])
    return None


@define
class _State:
    queue: ReadOnlyQueue | None = None
    last_success: float | None = None
    error: str | None = None
    detail: str = ""


@define
class QueueMonitor:
    configs: tuple[QueueConfig, ...]
    factory: ApiFactory = _make_api
    clock: Callable[[], float] = time.time
    _states: dict[str, _State] = field(init=False, factory=dict)

    def rows(self) -> list[dict[str, Any]]:
        """One row per configured RE Manager, queried now."""
        return [self._row(config) for config in self.configs]

    def _row(self, config: QueueConfig) -> dict[str, Any]:
        state = self._states.setdefault(config.instrument, _State())
        now = self.clock()
        row: dict[str, Any] = {
            "instrument": config.instrument,
            "transport": config.transport,
            "reachable": False,
            "manager_state": None,
            "re_state": None,
            "environment_open": None,
            "running_plan": None,
            "running_since": None,
            "items_in_queue": None,
            "next_items": None,
            "queue_stop_pending": None,
        }
        try:
            if state.queue is None:
                state.queue = ReadOnlyQueue(config, self.factory)
            status = state.queue.status()
            queue = state.queue.queue()
        except Exception as exc:  # any failure of the API is a source failure
            state.error, state.detail = _category(exc), type(exc).__name__
            if state.queue is not None:
                try:
                    state.queue.close()
                finally:
                    state.queue = None
        else:
            state.last_success, state.error, state.detail = now, None, ""
            running = queue.get("running_item") if isinstance(queue, Mapping) else None
            items = queue.get("items") if isinstance(queue, Mapping) else None
            properties = running.get("properties") if isinstance(running, Mapping) else None
            started = properties.get("time_start") if isinstance(properties, Mapping) else None
            names = [n for n in map(_item_name, items or []) if n]
            row.update(
                reachable=True,
                manager_state=status.get("manager_state"),
                re_state=status.get("re_state"),
                environment_open=status.get("worker_environment_exists"),
                running_plan=_item_name(running),
                running_since=started if isinstance(started, int | float) else None,
                items_in_queue=status.get("items_in_queue"),
                next_items=", ".join(names[:3]) or None,
                queue_stop_pending=status.get("queue_stop_pending"),
            )
        # One display value: the manager's own state, or why there is none.
        row["state"] = row["manager_state"] if row["reachable"] else state.error
        row.update(
            last_success=state.last_success,
            data_age_s=None if state.last_success is None else round(now - state.last_success, 1),
            error=state.error,
            error_detail=state.detail,
        )
        return row

    def close(self) -> None:
        for state in self._states.values():
            if state.queue is not None:
                state.queue.close()
