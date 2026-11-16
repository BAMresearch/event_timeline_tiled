"""Run state, defined in one place (plan §5.1-5.3).

Three things are kept apart and never conflated:

- **status**, the recorded outcome, from the stop document only;
- **activity**, inferred for open runs: the last observed event, its age and
  whether that age makes the run *possibly stalled*. An open run is never
  reported as "running";
- **source health**, whether the data could be fetched at all (see
  :mod:`event_timeline_tiled.sources`); stale data is flagged, not hidden.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from attrs import frozen

PROCESSING_KINDS = frozenset({"modacor_correction"})
"""Start-document ``kind`` values that make a run a processing run
(processing-runs.md §1); ``derived_from`` alone does not."""


class Status(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    ABORTED = "aborted"
    UNKNOWN = "unknown"
    OPEN = "open"


class Role(StrEnum):
    ACQUISITION = "acquisition"
    PROCESSING = "processing"


_EXIT = {"success": Status.SUCCESS, "fail": Status.FAILED, "abort": Status.ABORTED}


def status_of(stop: Mapping[str, Any] | None) -> Status:
    """``success → success``, ``fail → failed``, ``abort → aborted``, anything else
    ``unknown``; no stop document ``open``."""
    if stop is None:
        return Status.OPEN
    exit_status = stop.get("exit_status")
    if not isinstance(exit_status, str):
        return Status.UNKNOWN
    return _EXIT.get(exit_status, Status.UNKNOWN)


def role_of(start: Mapping[str, Any]) -> Role:
    kind = start.get("kind")
    if isinstance(kind, str) and kind in PROCESSING_KINDS:
        return Role.PROCESSING
    return Role.ACQUISITION


@frozen
class Activity:
    """What is known about an open run's progress."""

    last_event: float | None
    """Time of the last observed event, or ``None`` if none was observed (yet)."""
    age_s: float | None
    possibly_stalled: bool


def activity(
    status: Status,
    start_time: float | None,
    last_event: float | None,
    now: float,
    stall_after_s: float,
) -> Activity:
    """Activity of a run. Only open runs can be possibly stalled: when the time
    since the last event (or since the start, if there was none) exceeds
    ``stall_after_s``."""
    if status is not Status.OPEN:
        return Activity(last_event, None, False)
    reference = last_event if last_event is not None else start_time
    age = None if reference is None else max(0.0, now - reference)
    return Activity(last_event, age, age is not None and age > stall_after_s)
