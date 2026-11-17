"""Ordering of a measurement's processing results (processing-runs.md §1.2)."""

from typing import Any

from event_timeline_tiled.layout import RunRecord
from event_timeline_tiled.related import (
    NO_PIPELINE,
    NO_PRODUCT,
    default_result,
    families,
    family_of,
    problems,
    usable,
)


def proc(
    uid: str,
    start: float,
    stop: float | None,
    exit_status: str = "success",
    outputs: list[str] | None = None,
    product: str = "saxs",
    pipeline: str = "p1",
) -> RunRecord:
    doc: dict[str, Any] = {
        "uid": uid, "time": start, "processing_schema_version": 1, "product": product,
        "pipeline": {"name": pipeline}, "derived_from": ["m"], "request_id": "req",
        "attempt": 1,
    }  # fmt: skip
    stop_doc = None
    if stop is not None:
        published = [product] if outputs is None and exit_status == "success" else outputs or []
        stop_doc = {"time": stop, "exit_status": exit_status, "outputs": published}
    return RunRecord("", uid, doc, stop_doc)


def test_usable_needs_success_and_the_product_published() -> None:
    assert usable(proc("a", 0, 10))
    assert not usable(proc("b", 0, 10, outputs=[]))  # nothing to do
    assert not usable(proc("c", 0, 10, "fail", outputs=["saxs"]))  # partial
    assert not usable(proc("d", 0, None))  # open
    assert not usable(proc("e", 0, 10, outputs=["other"]))


def test_latest_attempt_and_latest_usable() -> None:
    ok = proc("ok", 10, 20)
    failed_later = proc("failed", 30, 40, "fail")
    still_open = proc("open", 50, None)
    [family] = families([failed_later, ok, still_open])
    assert family.runs == (ok, failed_later, still_open)
    assert family.latest_attempt is failed_later  # open attempts never count
    assert family.latest_usable is ok


def test_equal_stop_times_are_ordered_by_uid_as_text() -> None:
    a, b = proc("0aaa", 10, 30), proc("0bbb", 20, 30)
    [family] = families([b, a])
    assert family.latest_attempt is b and family.latest_usable is b


def test_families_and_the_default_result() -> None:
    old = proc("old", 0, 10, pipeline="p1")
    new = proc("new", 0, 20, pipeline="p2")
    unusable = proc("bad", 0, 30, "fail", pipeline="p3")
    found = families([old, unusable, new])
    assert [f.name for f in found] == ["saxs · p2", "saxs · p1", "saxs · p3"]
    assert default_result(found) is new
    assert default_result(families([unusable])) is None


def test_family_names_tolerate_missing_keys() -> None:
    assert family_of({}) == f"{NO_PRODUCT} · {NO_PIPELINE}"
    assert family_of({"product": "saxs", "pipeline": "not a mapping"}) == f"saxs · {NO_PIPELINE}"


def test_families_are_told_apart_by_their_parts_not_their_label() -> None:
    """Review 06, finding 7: the separator in a part does not merge families."""
    a = proc("a", 0, 10, product="a · b", pipeline="c")
    b = proc("b", 0, 20, product="a", pipeline="b · c")
    found = families([a, b])
    assert len(found) == 2 and sorted(f.latest_usable.key for f in found if f.latest_usable) == [
        "a",
        "b",
    ]


def test_runs_that_break_the_contract_are_never_latest() -> None:
    """Review 06, finding 6: listed, with problems, but never promoted."""
    good = proc("good", 0, 10)
    bare = RunRecord("", "bare", {"product": "saxs"}, {"exit_status": "success",
                                                       "outputs": ["saxs"]})  # fmt: skip
    newer = RunRecord("", "v2", {**good.start, "uid": "v2", "processing_schema_version": 2},
                      {**(good.stop or {}), "time": 99})  # fmt: skip
    assert "no processing_schema_version" in problems(bare)
    assert {"no start uid", "no start time", "no stop time", "no pipeline name"} <= set(
        problems(bare)
    )
    assert problems(newer) == ["processing_schema_version 2 not supported"]
    assert problems(good) == [] and not usable(bare) and not usable(newer)
    [family] = families([good, newer])
    assert family.runs == (good, newer)
    assert family.latest_usable is good and family.latest_attempt is good
    assert default_result(families([bare])) is None
