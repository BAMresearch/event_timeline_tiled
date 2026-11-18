"""The predicate truth table, field paths and relative times (presets.md §5)."""

from typing import Any

import pytest

from event_timeline_tiled.presets import MISSING, Context, evaluate, load, parse_preset, resolve
from event_timeline_tiled.presets.model import Match
from event_timeline_tiled.presets.predicates import Leaf, same


def ctx(
    start: dict[str, Any] | None = None,
    stop: dict[str, Any] | None = None,
    streams: tuple[str, ...] | None = ("baseline", "primary"),
    status: str = "success",
) -> Context:
    return Context("key-1", "raw", {"uid": "u", "time": 100.0, **(start or {})}, stop,
                   "acquisition", status, streams)  # fmt: skip


def where(text: str) -> Any:
    """The ``where`` of a one-track preset written in YAML."""
    data, marks = load(f"version: 1\nid: p\ntitle: P\ntracks:\n  - id: t\n    name: T\n"
                       f"    where: {text}\n")  # fmt: skip
    return parse_preset(data, marks).tracks[0].where


def holds(predicate: str, context: Context, at: float = 1000.0) -> bool:
    return evaluate(where(predicate), context, at)


def test_paths_resolve_through_mappings_only() -> None:
    c = ctx({"md": {"a": [1, {"b": 2}]}, "dot.key": 3, "none": None}, {"exit_status": "fail"})
    assert resolve(c, ("start", "md", "a")) == [1, {"b": 2}]
    assert resolve(c, ("start", "md", "a", "0")) is MISSING  # no list indices
    assert resolve(c, ("start", "none")) is MISSING  # null is missing
    assert resolve(c, ("stop", "exit_status")) == "fail"
    assert resolve(ctx(), ("stop", "exit_status")) is MISSING  # open run
    assert (resolve(c, ("uid",)), resolve(c, ("root",)), resolve(c, ("status",))) == (
        "key-1", "raw", "success",
    )  # fmt: skip
    assert resolve(ctx(streams=None), ("streams",)) is MISSING


@pytest.mark.parametrize(
    ("a", "b", "equal"),
    [
        (1, 1.0, True), (True, 1, False), (False, 0, False), ("1", 1, False),
        ([1, "a"], [1.0, "a"], True), ([True], [1], False), ({"a": 1}, {"a": 1.0}, True),
        ({"a": 1}, {"a": 1, "b": 2}, False), ([1, 2], [1], False), (None, MISSING, True),
        (float("nan"), float("nan"), False),
    ],
)  # fmt: skip
def test_typed_equality(a: Any, b: Any, equal: bool) -> None:
    assert same(a, b) is equal


@pytest.mark.parametrize(
    ("predicate", "start", "expected"),
    [
        ("{field: start.n, op: eq, value: 1}", {"n": 1.0}, True),
        ("{field: start.n, op: eq, value: 1}", {"n": True}, False),
        ("{field: start.n, op: eq, value: '1'}", {"n": 1}, False),
        ("{field: start.n, op: neq, value: 1}", {}, True),
        ("{field: start.n, op: eq, value: 1}", {}, False),
        ("{field: start.n, op: in, value: [1, 2]}", {"n": 2.0}, True),
        ("{field: start.n, op: in, value: [1, 2]}", {"n": [1]}, False),  # whole value
        ("{field: start.n, op: in, value: [[1]]}", {"n": [1]}, True),
        ("{field: start.n, op: in, value: []}", {"n": 1}, False),
        ("{field: start.n, op: nin, value: []}", {"n": 1}, True),
        ("{field: start.n, op: nin, value: [1]}", {}, True),
        ("{field: start.n, op: gt, value: 1}", {"n": 2}, True),
        ("{field: start.n, op: gt, value: 1}", {"n": True}, False),  # booleans are not numbers
        ("{field: start.n, op: gt, value: 1}", {"n": "2"}, False),
        ("{field: start.n, op: lte, value: 1}", {}, False),
        ("{field: start.n, op: exists}", {"n": None}, False),
        ("{field: start.n, op: missing}", {"n": None}, True),
        ("{field: start.n, op: not_empty}", {"n": "x"}, True),
        ("{field: start.n, op: not_empty}", {"n": []}, False),
        ("{field: start.n, op: not_empty}", {"n": 0}, False),
        ("{field: start.n, op: not_empty}", {"n": {}}, False),
        ("{field: start.n, op: contains, value: ea}", {"n": "measure"}, True),
        ("{field: start.n, op: contains, value: '1'}", {"n": 12}, False),
        ("{field: start.n, op: starts_with, value: me}", {"n": "measure"}, True),
        ("{field: start.n, op: ends_with, value: re}", {"n": "measure"}, True),
        ("{field: start.n, op: regex, value: 'sur'}", {"n": "measure"}, True),  # search
        ("{field: start.n, op: regex, value: '^sur'}", {"n": "measure"}, False),
        ("{field: start.n, op: regex, value: 'x'}", {"n": ["x"]}, False),
        ("{all: []}", {}, True),
        ("{any: []}", {}, False),
        ("{not: {field: start.n, op: eq, value: 1}}", {}, True),
        ("{any: [{field: start.n, op: eq, value: 1}, {field: start.m, op: exists}]}",
         {"m": 0}, True),
        ("{field: start.n, op: eq, value: {a: [1]}}", {"n": {"a": [1.0]}}, True),
    ],
)  # fmt: skip
def test_truth_table(predicate: str, start: dict[str, Any], expected: bool) -> None:
    assert holds(predicate, ctx(start)) is expected


def test_not_over_an_open_run() -> None:
    """The design's example: open runs have no exit status, so they pass."""
    assert holds("{not: {field: stop.exit_status, op: eq, value: success}}", ctx()) is True


def test_status_role_uid_and_root() -> None:
    failed = ctx(stop={"exit_status": "fail"}, status="failed")
    assert holds("{field: status, op: in, value: [failed, aborted]}", failed)
    assert not holds("{field: status, op: eq, value: fail}", failed)  # the run_state word
    assert holds("{field: role, op: eq, value: acquisition}", failed)
    assert holds("{field: uid, op: eq, value: key-1}", failed)
    assert holds("{field: root, op: eq, value: raw}", failed)


def test_relative_times_count_from_one_evaluation_time() -> None:
    recent = ctx({"time": 1000.0 - 3600})
    assert holds("{field: start.time, op: gte, value: {relative: '-2h'}}", recent, at=1000.0)
    assert not holds("{field: start.time, op: gte, value: {relative: '-30m'}}", recent, 1000.0)
    assert holds("{field: start.time, op: lt, value: {relative: '+1d'}}", recent, 1000.0)


def test_streams_and_require_streams() -> None:
    data, marks = load("version: 1\nid: p\ntitle: P\ntracks:\n  - id: t\n    name: T\n"
                       "    require_streams: [primary]\n")  # fmt: skip
    track = parse_preset(data, marks).tracks[0]
    assert track.matches(ctx(), 0.0) is Match.YES
    assert track.matches(ctx(streams=("baseline",)), 0.0) is Match.NO
    assert track.matches(ctx(streams=None), 0.0) is Match.STREAMS_UNKNOWN
    assert track.matches(ctx(streams=()), 0.0) is Match.NO
    assert holds("{field: streams, op: not_empty}", ctx())
    assert holds("{field: streams, op: eq, value: [baseline, primary]}", ctx())
    assert not holds("{field: streams, op: exists}", ctx(streams=None))


def test_leaves_compare_by_their_stored_value() -> None:
    leaf = Leaf("start.n", ("start", "n"), "eq", 1)
    assert evaluate(leaf, ctx({"n": 1.0}), 0.0)
