"""The Bluesky-in-Tiled layout and the catalogue-specific query choices.

The only module that knows how ``bluesky.callbacks.tiled_writer.TiledWriter``
(Bluesky 1.14.6, Tiled 0.2.21) lays runs out (plan §6, "Layout module facts"):

- a run is ``{root}/{key}``, its key normally the start document's ``uid``;
- run metadata holds the ``start`` and, once closed, the ``stop`` document;
- a stream is ``{run}/{stream}``, with ``data_keys``, ``hints`` and
  ``configuration`` in its metadata;
- scalar event data is the table ``{run}/{stream}/internal`` with columns
  ``seq_num``, ``time``, each field and ``ts_{field}``;
- external arrays are ``{run}/{stream}/{field}``; a processing run's arrays
  carry a leading result dimension, read at index 0 (processing-runs.md §1.1).

Searches are server-side and paged; runs are never fetched one by one for a
window. ``Contains`` is never used for UID membership (it matches substrings).

Processing runs of a measurement (processing-runs.md §1.2, decision D-008)
are checked for exact ``start.derived_from`` list membership here, after a
server-side search for candidates. ``Eq`` with a list is JSONB containment
on PostgreSQL, but Tiled 0.2.21 cannot bind a list on SQLite (the server
fails), and the adapter does not know which catalogue it reads. So:

- a UID of letters, digits, ``.``, ``~`` and ``-`` only (UUIDs): ``Like`` on
  the JSON-quoted UID. None of these characters is a ``LIKE`` wildcard or
  escaped in JSON, so the candidates are exactly the runs that list it;
- any other UID (``_`` is a wildcard, non-ASCII and ``\\`` are serialised
  differently by the databases): every run with ``start.derived_from``,
  capped at :data:`SCAN_MAX_RUNS`.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from attrs import field, frozen
from tiled.queries import Comparison, Eq, In, KeyPresent, Like

from .tiled_http import HealthStatus, Query, SourceError, TiledReader

TABLE = "internal"
"""Name of a stream's scalar event table."""


def _compare(operator: str, key: str, value: float) -> Query:
    query: Query = Comparison(operator, key, value)  # type: ignore[no-untyped-call]
    return query


def _no_stop() -> Query:
    query: Query = KeyPresent("stop", exists=False)
    return query


def _key_present(key: str) -> Query:
    query: Query = KeyPresent(key, exists=True)
    return query


def _eq(key: str, value: Any) -> Query:
    query: Query = Eq(key, value)
    return query


def _like(key: str, pattern: str) -> Query:
    query: Query = Like(key, pattern)
    return query


def _in(key: str, values: Sequence[str]) -> Query:
    query: Query = In(key, list(values))
    return query


PUSHDOWN_VERSIONS = frozenset({"0.2.21"})
"""Tiled versions whose ``Eq``/``In`` on start keys are proven to return a
superset of what the adapter's own ``eq``/``in`` keep, on SQLite and
PostgreSQL (Codex review 07): a preset's ``scope`` is sent to these only."""
Scope = Sequence[tuple[str, Sequence[Any]]]
"""Start-document keys (dotted, ``start.`` included) and the values each may
take: a preset's ``scope`` as the window searches receive it."""

SCAN_MAX_RUNS = 10_000
"""Runs with ``derived_from`` scanned at most for a UID ``Like`` cannot match."""
_LIKE_SAFE = re.compile(r"^[A-Za-z0-9.~-]+$")

MAX_ARRAY_BYTES = 64 * 1024 * 1024
"""Largest array slice read from Tiled in one request."""


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
class ArrayInfo:
    """One result of an external array: its shape (without the leading result
    dimension) and its numeric type."""

    shape: tuple[int, ...]
    dtype: np.dtype[Any] = field(eq=False)


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
        self._pushdown: bool | None = None

    def pushdown(self) -> bool:
        """Whether this server gets a preset's ``scope`` (asked once; an
        unreadable version means no)."""
        if self._pushdown is None:
            self._pushdown = self.reader.server_version() in PUSHDOWN_VERSIONS
        return self._pushdown

    def _scope_queries(self, scope: Scope) -> list[Query]:
        if not scope or not self.pushdown():
            return []
        return [
            _eq(key, values[0]) if len(values) == 1 else _in(key, values) for key, values in scope
        ]

    def window_runs(
        self,
        root: str,
        t_from: float,
        t_to: float,
        horizon_s: float,
        max_runs: int,
        scope: Scope = (),
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

        ``scope`` narrows both searches where :meth:`pushdown` allows; the
        caller still checks every run against it (a superset comes back).
        """
        narrowed = self._scope_queries(scope)
        opened, truncated_open = self.reader.search(
            root,
            [
                _no_stop(),
                _compare("lt", "start.time", t_to),
                _compare("ge", "start.time", t_to - horizon_s),
                *narrowed,
            ],
            max_items=max_runs,
        )
        closed, truncated_closed = self.reader.search(
            root,
            [_compare("lt", "start.time", t_to), _compare("ge", "stop.time", t_from), *narrowed],
            max_items=max_runs,
        )
        open_runs = [r for r in (_record(root, item) for item in opened) if r is not None]
        closed_runs = {r.key: r for r in (_record(root, item) for item in closed) if r is not None}
        runs = [r for r in open_runs if r.key not in closed_runs]
        runs += list(closed_runs.values())
        truncated = truncated_open or truncated_closed or len(runs) > max_runs
        return sorted(runs[:max_runs], key=lambda r: (r.start_time or 0.0, r.key)), truncated

    def find_runs(self, roots: Sequence[str], key: str) -> list[RunRecord]:
        """The runs stored under ``key`` in each of ``roots`` (a key may be in
        several roots: the caller decides what that means)."""
        found = []
        for root in roots:
            record = self.find_run([root], key)
            if record is not None:
                found.append(record)
        return found

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

    def derived_runs(self, root: str, uid: str, max_runs: int) -> tuple[list[RunRecord], bool]:
        """Runs under ``root`` whose ``start.derived_from`` list holds ``uid``
        (any role; the caller decides what a processing run is), at most
        ``max_runs``. Returns ``(runs, truncated)``: ``truncated`` is true when
        more may exist, so the runs cannot be taken as complete."""
        if _LIKE_SAFE.match(uid):
            query = _like("start.derived_from", f"%{json.dumps(uid)}%")
            cap = max_runs
        else:
            query, cap = _key_present("start.derived_from"), SCAN_MAX_RUNS
        items, truncated = self.reader.search(root, [query], max_items=cap)
        runs = []
        for record in (_record(root, item) for item in items):
            parents = None if record is None else record.start.get("derived_from")
            if record is not None and isinstance(parents, list) and uid in parents:
                runs.append(record)
        if len(runs) > max_runs:
            runs, truncated = runs[:max_runs], True
        return runs, truncated

    def runs_by_start_uid(self, root: str, uids: Sequence[str]) -> list[RunRecord]:
        """Runs under ``root`` whose start document has one of ``uids``, in one
        search."""
        if not uids:
            return []
        items, _ = self.reader.search(root, [_in("start.uid", uids)], max_items=len(uids) * 2)
        records = (_record(root, item) for item in items)
        return [r for r in records if r is not None and r.start_uid in uids]

    def result_info(self, run: RunRecord, stream: str, name: str) -> ArrayInfo:
        """Shape and type of an external array field of a processing run."""
        body = self.reader.get_json(f"/metadata/{_join(run.path, f'{stream}/{name}')}")
        attributes = (body.get("data") or {}).get("attributes") or {}
        structure = attributes.get("structure") or {}
        shape, data_type = structure.get("shape"), structure.get("data_type") or {}
        if (
            attributes.get("structure_family") != "array"
            or not isinstance(shape, list)
            or len(shape) < 2
            or not all(isinstance(n, int) and n >= 0 for n in shape)
            or shape[0] < 1
        ):
            raise SourceError(HealthStatus.ERROR, f"{stream}/{name} is not a result array")
        kind, size = data_type.get("kind"), data_type.get("itemsize")
        endianness = str(data_type.get("endianness"))
        order = {"little": "<", "big": ">", "not_applicable": "|"}.get(endianness)
        if kind not in {"f", "i", "u"} or not isinstance(size, int) or order is None:
            raise SourceError(HealthStatus.ERROR, f"{stream}/{name} is not numeric")
        return ArrayInfo(tuple(shape[1:]), np.dtype(f"{order}{kind}{size}"))

    def result_array(
        self, run: RunRecord, stream: str, name: str, info: ArrayInfo, step: int = 1
    ) -> np.ndarray[Any, Any]:
        """Result 0 of an external array field, every ``step``-th element along
        each axis (strided on the server), as float64."""
        step = max(1, step)
        shape = tuple(-(-n // step) for n in info.shape)
        expected = math.prod(shape) * info.dtype.itemsize
        if expected > MAX_ARRAY_BYTES:
            raise SourceError(HealthStatus.ERROR, f"{stream}/{name} is too large to read")
        selection = ",".join(["0", *([f"::{step}"] * len(info.shape))])
        body = self.reader.get_bytes(
            f"/array/full/{_join(run.path, f'{stream}/{name}')}",
            {"slice": selection, "format": "application/octet-stream"},
            max_bytes=expected,
        )
        if len(body) != expected:
            raise SourceError(HealthStatus.ERROR, f"{stream}/{name}: unexpected array size")
        return np.frombuffer(body, dtype=info.dtype).reshape(shape).astype(np.float64)

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
        data = self._table_columns(run, stream, columns)
        lengths = {len(values) for values in data.values()}
        if len(lengths) > 1:
            raise SourceError(HealthStatus.ERROR, "table columns of different lengths")
        order = sorted(range(len(data["seq_num"])), key=lambda i: _sort_key(data, i))
        return {name: [values[i] for i in order] for name, values in data.items()}

    def last_event_time(self, run: RunRecord) -> float | None:
        """Latest event time over all streams with scalar data. Reads each
        stream's whole ``time`` column (only that column, unsorted), so use it
        for open runs only (plan §5.2)."""
        return self.open_run_reading(run)[0]

    def stream_names(self, run: RunRecord) -> tuple[str, ...]:
        """The run's stream names, sorted: from a closed run's
        ``stop.num_events`` when it has one (descriptor-created streams,
        with or without events: Bluesky 1.14.6's RunEngine through
        ``TiledWriter``), else listed from Tiled."""
        counts = None if run.stop is None else run.stop.get("num_events")
        if isinstance(counts, Mapping) and all(isinstance(k, str) for k in counts):
            return tuple(sorted(counts))
        return tuple(sorted(s.name for s in self.streams(run)))

    def open_run_reading(self, run: RunRecord) -> tuple[float | None, tuple[str, ...]]:
        """An open run's latest event time (as :meth:`last_event_time`) and its
        stream names, from one listing of its streams."""
        latest: float | None = None
        streams = self.streams(run)
        for stream in streams:
            if not stream.has_table:
                continue
            column = self._table_columns(run, stream.name, ["time"])
            for t in (_finite(v) for v in column["time"]):
                if t is not None and (latest is None or t > latest):
                    latest = t
        return latest, tuple(sorted(s.name for s in streams))

    def _table_columns(
        self, run: RunRecord, stream: str, columns: Sequence[str]
    ) -> dict[str, list[Any]]:
        """The stream's event table, or no rows if it does not exist yet.

        ``TiledWriter`` creates a stream's table with its first batch of rows
        (``batch_size`` rows, or at the stop document), so a listed stream of a
        running run often has none: that is "no events written yet", not an
        error. A finished run whose stop document reports events for the
        stream must have its table: there, a missing table is missing data."""
        try:
            return self.reader.table_columns(f"{run.path}/{stream}/{TABLE}", columns)
        except SourceError as exc:
            if exc.status is not HealthStatus.NOT_FOUND:
                raise
            if run.stop is not None and (run.num_events(stream) or 0) > 0:
                raise SourceError(
                    HealthStatus.ERROR, f"{stream} reports events but has no table"
                ) from None
            return {name: [] for name in columns}


def _sort_key(data: Mapping[str, list[Any]], i: int) -> tuple[float, float]:
    seq, t = _finite(data["seq_num"][i]), _finite(data["time"][i])
    return (math.inf if seq is None else seq, math.inf if t is None else t)


def _is_external(data_key: Any) -> bool:
    return isinstance(data_key, Mapping) and bool(data_key.get("external"))


def _join(root: str, key: str) -> str:
    from .tiled_http import tiled_path

    return tiled_path(f"{root}/{key}")
