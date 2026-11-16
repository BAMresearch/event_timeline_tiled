"""The Bluesky-in-Tiled layout and the catalogue-specific query choices.

The only module that knows how ``bluesky.callbacks.tiled_writer.TiledWriter``
(Bluesky 1.14.6, Tiled 0.2.21) lays runs out (plan §6, "Layout module facts"):

- a run is ``{root}/{key}``, its key normally the start document's ``uid``;
- run metadata holds the ``start`` and, once closed, the ``stop`` document;
- a stream is ``{run}/{stream}``, with ``data_keys``, ``hints`` and
  ``configuration`` in its metadata;
- scalar event data is the table ``{run}/{stream}/internal`` with columns
  ``seq_num``, ``time``, each field and ``ts_{field}``;
- external arrays are ``{run}/{stream}/{field}``.

Searches are server-side and paged; runs are never fetched one by one for a
window. ``Contains`` is never used for UID membership (it matches substrings).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from attrs import field, frozen
from tiled.queries import Comparison, KeyPresent

from .tiled_http import HealthStatus, Query, SourceError, TiledReader

TABLE = "internal"
"""Name of a stream's scalar event table."""


def _compare(operator: str, key: str, value: float) -> Query:
    query: Query = Comparison(operator, key, value)  # type: ignore[no-untyped-call]
    return query


def _no_stop() -> Query:
    query: Query = KeyPresent("stop", exists=False)
    return query


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


@frozen
class RunRecord:
    """One run as found in a catalogue root: its key and its start/stop documents."""

    root: str
    key: str
    start: Mapping[str, Any] = field(repr=False)
    stop: Mapping[str, Any] | None = field(repr=False)

    @property
    def path(self) -> str:
        return f"{self.root}/{self.key}".strip("/")

    @property
    def start_uid(self) -> str | None:
        uid = self.start.get("uid")
        return uid if isinstance(uid, str) else None

    @property
    def uid_mismatch(self) -> bool:
        return self.start_uid != self.key

    @property
    def start_time(self) -> float | None:
        return _finite(self.start.get("time"))

    @property
    def stop_time(self) -> float | None:
        return None if self.stop is None else _finite(self.stop.get("time"))

    @property
    def exit_status(self) -> str | None:
        if self.stop is None:
            return None
        status = self.stop.get("exit_status")
        return status if isinstance(status, str) else None

    def num_events(self, stream: str) -> int | None:
        """Producer-reported event count of a closed run's stream."""
        counts = None if self.stop is None else self.stop.get("num_events")
        value = counts.get(stream) if isinstance(counts, Mapping) else None
        return value if isinstance(value, int) and not isinstance(value, bool) else None


@frozen
class StreamInfo:
    name: str
    data_keys: Mapping[str, Mapping[str, Any]]
    hints: tuple[str, ...]
    has_table: bool


def _record(root: str, item: Mapping[str, Any]) -> RunRecord | None:
    """A run record from a search item, or ``None`` if it is not a run."""
    key = item.get("id")
    metadata = (item.get("attributes") or {}).get("metadata")
    if not isinstance(key, str) or not isinstance(metadata, Mapping):
        return None
    start, stop = metadata.get("start"), metadata.get("stop")
    if not isinstance(start, Mapping):
        return None
    return RunRecord(root, key, start, stop if isinstance(stop, Mapping) else None)


class Layout:
    """Bluesky runs in Tiled, read through a :class:`TiledReader`."""

    def __init__(self, reader: TiledReader) -> None:
        self.reader = reader

    def window_runs(
        self, root: str, t_from: float, t_to: float, horizon_s: float, max_runs: int
    ) -> tuple[list[RunRecord], bool]:
        """Runs overlapping ``[t_from, t_to)`` under one root (plan §5.4).

        The de-duplicated union of two searches: open runs (no ``stop`` key)
        with ``to - horizon <= start.time < to``; closed runs with
        ``start.time < to`` and ``stop.time >= from``. Returns
        ``(runs, truncated)``, sorted by start time.

        The open search goes first: a run that closes between the two searches
        is then found closed by the second instead of by neither. A key found
        by both is kept as the closed record. The two searches are not one
        snapshot of the catalogue; anything else that changes between them is
        seen at the next refresh.

        At most ``max_runs`` runs are returned. Above that, open runs are kept
        first (an operator needs what is still going on), then closed runs in
        Tiled's order (insertion order: the earliest written), and
        ``truncated`` is true.
        """
        opened, truncated_open = self.reader.search(
            root,
            [
                _no_stop(),
                _compare("lt", "start.time", t_to),
                _compare("ge", "start.time", t_to - horizon_s),
            ],
            max_items=max_runs,
        )
        closed, truncated_closed = self.reader.search(
            root,
            [_compare("lt", "start.time", t_to), _compare("ge", "stop.time", t_from)],
            max_items=max_runs,
        )
        open_runs = [r for r in (_record(root, item) for item in opened) if r is not None]
        closed_runs = {r.key: r for r in (_record(root, item) for item in closed) if r is not None}
        runs = [r for r in open_runs if r.key not in closed_runs]
        runs += list(closed_runs.values())
        truncated = truncated_open or truncated_closed or len(runs) > max_runs
        return sorted(runs[:max_runs], key=lambda r: (r.start_time or 0.0, r.key)), truncated

    def find_run(self, roots: Sequence[str], key: str) -> RunRecord | None:
        """The run stored under ``key`` in the first root that has it."""
        for root in roots:
            try:
                body = self.reader.get_json(f"/metadata/{_join(root, key)}")
            except SourceError as exc:
                if exc.status is HealthStatus.NOT_FOUND:
                    continue
                raise
            data = body.get("data")
            if isinstance(data, Mapping):
                record = _record(root, data)
                if record is not None:
                    return record
        return None

    def streams(self, run: RunRecord) -> list[StreamInfo]:
        items, _ = self.reader.search(run.path, fields=("metadata",))
        out = []
        for item in items:
            name, metadata = item.get("id"), (item.get("attributes") or {}).get("metadata")
            if not isinstance(name, str) or not isinstance(metadata, Mapping):
                continue
            data_keys = metadata.get("data_keys")
            hints = metadata.get("hints")
            hinted: list[str] = []
            if isinstance(hints, Mapping):
                for hint in hints.values():
                    if isinstance(hint, Mapping) and isinstance(hint.get("fields"), list):
                        hinted += [f for f in hint["fields"] if isinstance(f, str)]
            data_keys = data_keys if isinstance(data_keys, Mapping) else {}
            out.append(
                StreamInfo(
                    name=name,
                    data_keys={k: v for k, v in data_keys.items() if isinstance(v, Mapping)},
                    hints=tuple(hinted),
                    has_table=any(not _is_external(v) for v in data_keys.values()),
                )
            )
        return sorted(out, key=lambda s: s.name)

    def event_columns(
        self, run: RunRecord, stream: str, fields: Sequence[str] = ()
    ) -> dict[str, list[Any]]:
        """``seq_num``, ``time`` and the given scalar fields of a stream's events,
        in ``seq_num`` order. Tiled returns table rows in storage order, which
        for SQL-backed tables (PostgreSQL in the demo stack) is not the order
        they were written in."""
        columns = ["seq_num", "time", *[f for f in fields if f not in {"seq_num", "time"}]]
        data = self.reader.table_columns(f"{run.path}/{stream}/{TABLE}", columns)
        lengths = {len(values) for values in data.values()}
        if len(lengths) > 1:
            raise SourceError(HealthStatus.ERROR, "table columns of different lengths")
        order = sorted(range(len(data["seq_num"])), key=lambda i: _sort_key(data, i))
        return {name: [values[i] for i in order] for name, values in data.items()}

    def last_event_time(self, run: RunRecord) -> float | None:
        """Latest event time over all streams with scalar data. Reads each
        stream's whole ``time`` column (only that column, unsorted), so use it
        for open runs only (plan §5.2)."""
        latest: float | None = None
        for stream in self.streams(run):
            if not stream.has_table:
                continue
            column = self.reader.table_columns(f"{run.path}/{stream.name}/{TABLE}", ["time"])
            for t in (_finite(v) for v in column["time"]):
                if t is not None and (latest is None or t > latest):
                    latest = t
        return latest


def _sort_key(data: Mapping[str, list[Any]], i: int) -> tuple[float, float]:
    seq, t = _finite(data["seq_num"][i]), _finite(data["time"][i])
    return (math.inf if seq is None else seq, math.inf if t is None else t)


def _is_external(data_key: Any) -> bool:
    return isinstance(data_key, Mapping) and bool(data_key.get("external"))


def _join(root: str, key: str) -> str:
    from .tiled_http import tiled_path

    return tiled_path(f"{root}/{key}")
