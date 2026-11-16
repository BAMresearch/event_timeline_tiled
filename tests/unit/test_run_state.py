from typing import Any

import pytest

from event_timeline_tiled.run_state import Role, Status, activity, role_of, status_of


@pytest.mark.parametrize(
    "stop, status",
    [
        (None, Status.OPEN),
        ({"exit_status": "success"}, Status.SUCCESS),
        ({"exit_status": "fail"}, Status.FAILED),
        ({"exit_status": "abort"}, Status.ABORTED),
        ({"exit_status": "paused?"}, Status.UNKNOWN),
        ({"exit_status": None}, Status.UNKNOWN),
        ({}, Status.UNKNOWN),
    ],
)
def test_status_comes_from_the_stop_document_only(stop: Any, status: Status) -> None:
    assert status_of(stop) is status


def test_role_needs_the_processing_kind_not_derived_from() -> None:
    assert role_of({"kind": "modacor_correction"}) is Role.PROCESSING
    assert role_of({"derived_from": ["x"]}) is Role.ACQUISITION
    assert role_of({"kind": "something_else"}) is Role.ACQUISITION


def test_only_open_runs_can_be_possibly_stalled() -> None:
    assert not activity(Status.SUCCESS, 0, 10, now=10_000, stall_after_s=60).possibly_stalled
    fresh = activity(Status.OPEN, 0, 9_990, now=10_000, stall_after_s=60)
    assert (fresh.age_s, fresh.possibly_stalled) == (10, False)
    quiet = activity(Status.OPEN, 0, 9_000, now=10_000, stall_after_s=60)
    assert quiet.possibly_stalled
    no_event = activity(Status.OPEN, 9_000, None, now=10_000, stall_after_s=60)
    assert (no_event.last_event, no_event.age_s, no_event.possibly_stalled) == (None, 1000, True)
