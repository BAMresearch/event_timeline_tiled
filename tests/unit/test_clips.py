"""Lanes, sub-lanes and the frame (plan §3.1, §8), from run records."""

from typing import Any

from event_timeline_tiled.clips import Clip, LaneKey, frame, lane_key, lane_names, make_clip
from event_timeline_tiled.layout import RunRecord
from event_timeline_tiled.run_state import Activity, Role, Status

QUIET = Activity(None, None, False)


def run(key: str, start: float, stop: float | None, root: str = "", **md: Any) -> RunRecord:
    start_doc = {"uid": key, "time": start, "facility": "BAM", "instrument": "MOUSE", **md}
    stop_doc = None if stop is None else {"time": stop, "exit_status": "success"}
    return RunRecord(root, key, start_doc, stop_doc)


def clip(r: RunRecord, act: Activity = QUIET, stale: bool = False) -> Clip:
    c = make_clip(r, act, stale, r.key)
    assert c is not None
    return c


def test_lane_key_and_fallback() -> None:
    assert lane_key(run("a", 0, 1)).as_text() == "BAM/MOUSE/-/acquisition"
    proc = run("p", 0, 1, kind="modacor_correction", station="2")
    assert lane_key(proc) == LaneKey("BAM", "MOUSE", "2", Role.PROCESSING)
    bare = RunRecord("", "x", {"uid": "x", "time": 0}, None)
    assert lane_key(bare).instrument == "(unknown)"
    assert lane_key(RunRecord("runs/b", "x", {"uid": "x", "time": 0}, None)).instrument == (
        "(runs/b)"
    )


def test_lane_names_disambiguate_by_facility() -> None:
    a = LaneKey("BAM", "X", None, Role.ACQUISITION)
    b = LaneKey("OTHER", "X", None, Role.ACQUISITION)
    p = LaneKey("BAM", "X", "1", Role.PROCESSING)
    assert lane_names([a, b, p]) == {a: "BAM X", b: "OTHER X", p: "X 1 · processing"}


def test_overlaps_get_sub_lanes_and_touching_clips_share() -> None:
    clips = [
        clip(run("a", 100, 200)),
        clip(run("b", 150, 250)),  # overlaps a
        clip(run("c", 200, 300)),  # touches a: shares its lane
        clip(run("d", 160, 170)),  # overlaps a and b
    ]
    rows = {r["uid"]: r["lane"] for r in frame(clips, 0, 1000, open_until=1000)}
    assert rows == {"a": "MOUSE", "b": "MOUSE /2", "c": "MOUSE", "d": "MOUSE /3"}


def test_frame_units_open_runs_and_widening() -> None:
    open_run = clip(run("o", 900, None), Activity(950, 50, False))
    tiny = clip(run("t", 100, 100.5))
    rows = {r["uid"]: r for r in frame([open_run, tiny], 0, 10_000, open_until=990)}
    assert (rows["o"]["start"], rows["o"]["stop"]) == (900_000, 990_000)
    assert rows["o"]["state"] == rows["o"]["status"] == "open"
    assert rows["t"]["stop"] - rows["t"]["start"] == 20_000  # 0.2 % of the window
    assert (rows["t"]["clip_from_ms"], rows["t"]["clip_to_ms"]) == (100_000, 100_500)


def test_state_shows_inference_but_status_stays_recorded() -> None:
    stalled = clip(run("s", 0, None), Activity(10, 5000, True))
    stale = clip(run("x", 0, None), stale=True)
    rows = {r["uid"]: r for r in frame([stalled, stale], 0, 10_000, open_until=5000)}
    assert (rows["s"]["state"], rows["s"]["status"]) == ("possibly stalled", "open")
    assert (rows["x"]["state"], rows["x"]["status"], rows["x"]["stale"]) == ("stale", "open", True)


def test_lanes_are_grouped_acquisition_before_processing() -> None:
    clips = [
        clip(run("p", 10, 20, kind="modacor_correction")),
        clip(run("g", 5, 30, instrument="generic")),
        clip(run("m", 0, 40)),
    ]
    assert [r["lane"] for r in frame(clips, 0, 100, 100)] == [
        "MOUSE",
        "MOUSE · processing",
        "generic",
    ]


def test_run_without_start_time_is_not_a_clip() -> None:
    assert make_clip(RunRecord("", "x", {"uid": "x"}, None), QUIET, False, "x") is None


def test_status_of_clip() -> None:
    aborted = RunRecord("", "a", {"uid": "a", "time": 0}, {"time": 1, "exit_status": "abort"})
    assert clip(aborted).status is Status.ABORTED
