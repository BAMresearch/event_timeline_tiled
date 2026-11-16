"""The view table: the only place with instrument knowledge (plan §7).

For a run it matches instrument, role and start-document facts in a fixed
order and yields the available views and a default. Instruments without an
entry, and runs no rule matches, get the generic fallback. Endpoints only
resolve what this table says; nothing else tests an instrument name.

Version 1 of the view-table contract: view ``name`` values and ``kind``
values are stable; add new names rather than changing what one means.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from attrs import field, frozen, validators

from .layout import RunRecord, StreamInfo
from .run_state import Role

VIEW_TABLE_VERSION = 1


class ViewKind(StrEnum):
    TABLE = "table"
    """Long-format rows: stream, seq_num, time, field, value, units."""
    TIMESERIES = "timeseries"
    """Wide rows from one stream: time (ms) and one column per field."""
    SUMMARY = "summary"
    """The run's start and stop metadata as key/value rows."""


@frozen
class ViewSpec:
    name: str = field(validator=validators.matches_re(r"^[a-z][a-z0-9_]*$"))
    title: str
    kind: ViewKind
    streams: tuple[str, ...] = ()
    """Streams the view reads, in display order (table views)."""


@frozen
class Rule:
    """Matches a run of the entry's instrument; the first view is the default."""

    role: Role
    views: tuple[ViewSpec, ...] = field(validator=validators.min_len(1))
    plan_names: frozenset[str] | None = None
    """Match only these start-document ``plan_name`` values (``None``: any)."""
    start_keys: frozenset[str] = frozenset()
    """Match only runs whose start document has all of these keys."""

    def matches(self, run: RunRecord, role: Role) -> bool:
        if role is not self.role:
            return False
        if self.plan_names is not None and run.start.get("plan_name") not in self.plan_names:
            return False
        return self.start_keys <= set(run.start)


@frozen
class InstrumentEntry:
    instrument: str
    stall_after_s: float = field(validator=validators.gt(0))
    """Open runs without an event for longer than this are "possibly stalled"."""
    label_keys: tuple[str, ...]
    """Start-document keys whose values make a clip's label, in order."""
    rules: tuple[Rule, ...]


SUMMARY = ViewSpec("summary", "Run metadata", ViewKind.SUMMARY)

# MOUSE (plan §3.2): one run per measurement with baseline, optional snapshot,
# and three readings in mouse_eiger_measure; configuration and vacuum runs are
# separate runs on the same lane.
MOUSE = InstrumentEntry(
    instrument="MOUSE",
    stall_after_s=20 * 60,
    label_keys=("sampleid", "plan_name"),
    rules=(
        Rule(
            Role.ACQUISITION,
            (ViewSpec("baseline", "Baseline", ViewKind.TABLE, ("baseline",)), SUMMARY),
            plan_names=frozenset({"apply_config"}),
        ),
        Rule(
            Role.ACQUISITION,
            (ViewSpec("baseline", "Baseline", ViewKind.TABLE, ("baseline",)), SUMMARY),
            start_keys=frozenset({"activity"}),
        ),
        Rule(
            Role.ACQUISITION,
            (
                ViewSpec(
                    "readings",
                    "Readings",
                    ViewKind.TABLE,
                    ("mouse_eiger_measure", "snapshot", "baseline"),
                ),
                SUMMARY,
            ),
        ),
    ),
)

VIEW_TABLE: tuple[InstrumentEntry, ...] = (MOUSE,)

FALLBACK_STALL_AFTER_S = 30 * 60
FALLBACK_LABEL_KEYS = ("plan_name", "scan_id")
TABLE_ROW_LIMIT = 2000
"""Rows a table view returns at most (the rest is reported as truncated)."""


@frozen
class ResolvedView:
    """A view as resolved for one run."""

    name: str
    title: str
    kind: ViewKind
    streams: tuple[str, ...]
    fields: tuple[str, ...]
    units: Mapping[str, str]
    available: bool
    reason: str


def entry_for(instrument: Any) -> InstrumentEntry | None:
    for entry in VIEW_TABLE:
        if entry.instrument == instrument:
            return entry
    return None


def stall_after_s(run: RunRecord) -> float:
    entry = entry_for(run.start.get("instrument"))
    return entry.stall_after_s if entry else FALLBACK_STALL_AFTER_S


def label(run: RunRecord) -> str:
    entry = entry_for(run.start.get("instrument"))
    keys = entry.label_keys if entry else FALLBACK_LABEL_KEYS
    parts = [str(run.start[k]) for k in keys if run.start.get(k) not in (None, "")]
    return " · ".join(parts) if parts else run.key[:8]


def _scalar_fields(stream: StreamInfo, numeric_only: bool) -> list[str]:
    out = []
    for name, key in stream.data_keys.items():
        if key.get("external") or key.get("shape") not in ([], None):
            continue
        if numeric_only and key.get("dtype") not in {"number", "integer"}:
            continue
        out.append(name)
    return out


def _units(stream: StreamInfo | None, fields: Sequence[str]) -> dict[str, str]:
    if stream is None:
        return {}
    units = {f: stream.data_keys.get(f, {}).get("units") for f in fields}
    return {f: u for f, u in units.items() if isinstance(u, str) and u}


def _table_view(spec: ViewSpec, streams: Mapping[str, StreamInfo]) -> ResolvedView:
    present = tuple(s for s in spec.streams if s in streams and streams[s].has_table)
    fields = tuple(f for s in present for f in _scalar_fields(streams[s], numeric_only=False))
    units = {k: v for s in present for k, v in _units(streams[s], list(fields)).items()}
    return ResolvedView(
        spec.name, spec.title, spec.kind, present, fields, units,
        available=bool(present), reason="" if present else "none of its streams has events",
    )  # fmt: skip


def _summary() -> ResolvedView:
    return ResolvedView(
        SUMMARY.name, SUMMARY.title, ViewKind.SUMMARY, (), (), {}, available=True, reason=""
    )


def _fallback(streams: Mapping[str, StreamInfo]) -> list[ResolvedView]:
    """Hinted numeric scalars against time; else all numeric scalars; else a
    bounded table; else nothing plottable. The stream is ``primary`` if
    present, else the first stream with events."""
    candidates = [s for s in streams.values() if s.has_table]
    stream = streams.get("primary") if "primary" in streams else None
    if stream is None or not stream.has_table:
        stream = candidates[0] if candidates else None
    if stream is None:
        return [
            ResolvedView(
                "no_data",
                "No plottable data",
                ViewKind.TABLE,
                (),
                (),
                {},
                available=False,
                reason="no stream with scalar events",
            ),  # fmt: skip
            _summary(),
        ]
    numeric = _scalar_fields(stream, numeric_only=True)
    hinted = [f for f in stream.hints if f in numeric]
    views = []
    if hinted:
        views.append(
            ResolvedView(
                "hinted",
                "Hinted fields",
                ViewKind.TIMESERIES,
                (stream.name,),
                tuple(hinted),
                _units(stream, hinted),
                available=True,
                reason="",
            )  # fmt: skip
        )
    if numeric:
        views.append(
            ResolvedView(
                "numeric",
                "Numeric fields",
                ViewKind.TIMESERIES,
                (stream.name,),
                tuple(numeric),
                _units(stream, numeric),
                available=True,
                reason="",
            )  # fmt: skip
        )
    views.append(_table_view(ViewSpec("events", "Events", ViewKind.TABLE, (stream.name,)), streams))
    views.append(_summary())
    return views


def resolve(run: RunRecord, role: Role, streams: Sequence[StreamInfo]) -> list[ResolvedView]:
    """The views of a run, the default first."""
    by_name = {s.name: s for s in streams}
    entry = entry_for(run.start.get("instrument"))
    for rule in entry.rules if entry else ():
        if rule.matches(run, role):
            resolved = [
                _summary() if spec.kind is ViewKind.SUMMARY else _table_view(spec, by_name)
                for spec in rule.views
            ]
            if any(v.available and v.kind is not ViewKind.SUMMARY for v in resolved):
                return sorted(resolved, key=lambda v: not v.available)
    return _fallback(by_name)
