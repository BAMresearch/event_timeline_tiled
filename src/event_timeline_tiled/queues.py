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


RUNNING_STATES = frozenset({"starting_queue", "executing_queue", "paused"})
"""Manager states (bluesky-queueserver 0.0.26 ``MState``) in which the queue runs."""


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


@define
class QueueRun:
    """Progress of the queue's current or last run, as this adapter saw it.

    The RE Manager records neither when items were added nor which items make
    up a run; it reports how many items are in the queue and in the history.
    So a run starts when the manager is first seen running the queue, items
    done are the growth of the history since then, and the total is that plus
    the items still queued and the running one. If the manager was seen idle
    before, the history size then is the start (``from_start``: everything since
    the queue started is counted, though ``since`` is when the adapter noticed);
    otherwise (the adapter started mid-run) counting starts at first sight.
    Items added or removed during a run change the total; a cleared history
    restarts the count.
    """

    idle_history: int | None = None
    """The history size when the manager was last seen not running the queue."""
    start_history: int | None = None
    since: float | None = None
    from_start: bool = False
    active: bool = False
    done: int | None = None
    total: int | None = None

    def observe(
        self, manager_state: Any, history: Any, in_queue: Any, running: bool, now: float
    ) -> None:
        history, in_queue = _count(history), _count(in_queue)
        if history is None or in_queue is None:
            return
        if manager_state in RUNNING_STATES:
            if not self.active or self.start_history is None:
                self.active = True
                idle = self.idle_history
                self.from_start = idle is not None and idle <= history
                self.start_history = idle if idle is not None and idle <= history else history
                self.since = now
            elif history < self.start_history:  # history cleared during the run
                self.start_history, self.from_start, self.since = history, False, now
            self.done = history - self.start_history
            self.total = self.done + in_queue + (1 if running else 0)
            return
        if self.active and self.start_history is not None and history >= self.start_history:
            # The run ended: finished, or stopped with items left in the queue.
            self.done = history - self.start_history
            self.total = self.done + in_queue
        self.active = False
        self.idle_history = history

    def fields(self) -> dict[str, Any]:
        done, total = self.done, self.total
        text = None
        if done is not None and total is not None:
            text = f"{done} of {total}"
            if not self.active and done < total:
                text += ", stopped"
        return {
            "run_active": self.active,
            "run_done": done,
            "run_total": total,
            "run_progress": round(done / total, 4) if done is not None and total else None,
            "run_since": self.since,
            "run_from_start": self.from_start if self.since is not None else None,
            "run_text": text,
        }


@define
class _State:
    queue: ReadOnlyQueue | None = None
    last_success: float | None = None
    error: str | None = None
    detail: str = ""
    run: QueueRun = field(factory=QueueRun)
    lock: threading.Lock = field(factory=threading.Lock)


@define
class QueueMonitor:
    configs: tuple[QueueConfig, ...]
    factory: ApiFactory = _make_api
    clock: Callable[[], float] = time.time
    _states: dict[str, _State] = field(init=False, factory=dict)

    def rows(self, admits: Callable[[str], bool] = lambda _: True) -> list[dict[str, Any]]:
        """One row per configured RE Manager whose instrument ``admits``
        accepts, queried now."""
        return [self._row(config) for config in self.configs if admits(config.instrument)]

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
            **QueueRun().fields(),
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
            with state.lock:
                state.run.observe(
                    status.get("manager_state"),
                    status.get("items_in_history"),
                    status.get("items_in_queue"),
                    isinstance(running, Mapping) and bool(running),
                    now,
                )
                row.update(state.run.fields())
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
