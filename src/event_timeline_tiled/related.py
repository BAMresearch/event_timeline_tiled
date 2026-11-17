"""Processing results of a measurement (processing-runs.md §1, §1.2).

Which runs are the processing runs of a measurement is a search (the layout
module); this module checks them against the contract and orders them. Per
result family (``product`` and ``pipeline.name``):

- the **latest attempt** is the latest finished run, by stop time, ties
  broken by the greater start-document UID compared as text; open attempts
  have no stop time and are never the latest attempt;
- the **latest usable result** is the latest finished run that is usable:
  ``exit_status == "success"`` and its product among the stop document's
  ``outputs``.

A run that breaks the contract (:func:`problems`) is listed with its
problems but is never the latest attempt or result: it lacks the facts the
ordering needs, or its schema version is not one this viewer reads.

The default result for the viewer is the latest usable result over all
families (by the same ordering).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from attrs import frozen

from .layout import RunRecord

SCHEMA_VERSIONS = frozenset({1})
"""``processing_schema_version`` values this viewer reads."""
NO_PRODUCT = "(no product)"
NO_PIPELINE = "(no pipeline)"
EXIT_STATUSES = frozenset({"success", "fail", "abort"})

FamilyKey = tuple[str | None, str | None]


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _pipeline(start: Mapping[str, Any]) -> str | None:
    pipeline = start.get("pipeline")
    return _text(pipeline.get("name")) if isinstance(pipeline, Mapping) else None


def family_key(start: Mapping[str, Any]) -> FamilyKey:
    """``(product, pipeline.name)``, ``None`` for a missing part."""
    return _text(start.get("product")), _pipeline(start)


def family_label(key: FamilyKey) -> str:
    return f"{key[0] or NO_PRODUCT} · {key[1] or NO_PIPELINE}"


def family_of(start: Mapping[str, Any]) -> str:
    """The family as one display string (not an identity: see :func:`family_key`)."""
    return family_label(family_key(start))


def problems(run: RunRecord) -> list[str]:
    """How a processing run breaks the contract (processing-runs.md §1) in what
    the viewer relies on: schema version, identity, ordering and outcome."""
    start, out = run.start, []
    version = start.get("processing_schema_version")
    if isinstance(version, bool) or version not in SCHEMA_VERSIONS:
        out.append(
            "no processing_schema_version"
            if version is None
            else f"processing_schema_version {version!r} not supported"
        )
    if _text(start.get("product")) is None:
        out.append("no product")
    if _pipeline(start) is None:
        out.append("no pipeline name")
    if run.start_uid is None:
        out.append("no start uid")
    if run.start_time is None:
        out.append("no start time")
    attempt = start.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int):
        out.append("no attempt number")
    if _text(start.get("request_id")) is None:
        out.append("no request_id")
    parents = start.get("derived_from")
    if (
        not isinstance(parents, list)
        or not parents
        or not all(isinstance(p, str) and p for p in parents)
    ):
        out.append("derived_from is not a list of UIDs")
    if run.stop is not None:
        if run.stop_time is None:
            out.append("no stop time")
        if run.exit_status not in EXIT_STATUSES:
            out.append("unknown exit_status")
        if not isinstance(run.stop.get("outputs"), list):
            out.append("no outputs list")
    return out


def usable(run: RunRecord) -> bool:
    """A finished, successful run that published its product (and is valid)."""
    if run.stop is None or run.exit_status != "success" or problems(run):
        return False
    outputs = run.stop.get("outputs")
    product = run.start.get("product")
    return isinstance(outputs, list) and isinstance(product, str) and product in outputs


def _order(run: RunRecord) -> tuple[float, str]:
    return (run.stop_time if run.stop_time is not None else float("-inf"), run.start_uid or "")


@frozen
class Family:
    key: FamilyKey
    runs: tuple[RunRecord, ...]
    """All runs of the family, by start time."""
    latest_attempt: RunRecord | None
    latest_usable: RunRecord | None

    @property
    def name(self) -> str:
        return family_label(self.key)


def families(runs: Iterable[RunRecord]) -> list[Family]:
    """The result families of one measurement's processing runs, the family
    with the latest usable result first, then by latest attempt."""
    grouped: dict[FamilyKey, list[RunRecord]] = {}
    for run in runs:
        grouped.setdefault(family_key(run.start), []).append(run)
    out = []
    for key, members in grouped.items():
        finished = [r for r in members if r.stop is not None and not problems(r)]
        out.append(
            Family(
                key,
                tuple(sorted(members, key=lambda r: (r.start_time or 0.0, r.key))),
                max(finished, key=_order, default=None),
                max((r for r in finished if usable(r)), key=_order, default=None),
            )
        )

    def rank(f: Family) -> tuple[bool, tuple[float, str], tuple[float, str], str]:
        none = (float("-inf"), "")
        return (
            f.latest_usable is not None,
            _order(f.latest_usable) if f.latest_usable else none,
            _order(f.latest_attempt) if f.latest_attempt else none,
            f.name,
        )

    return sorted(out, key=rank, reverse=True)


def default_result(found: Iterable[Family]) -> RunRecord | None:
    """The latest usable result over all families."""
    results = [f.latest_usable for f in found if f.latest_usable is not None]
    return max(results, key=_order, default=None)
