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


def clip(
    r: RunRecord, act: Activity = QUIET, stale: bool = False, observed_at: float = 1000.0
) -> Clip:
    c = make_clip(r, act, stale, r.key, observed_at)
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
    rows = {r["uid"]: r["lane"] for r in frame(clips, 0, 1000)}
    assert rows == {"a": "MOUSE", "b": "MOUSE /2", "c": "MOUSE", "d": "MOUSE /3"}


def test_frame_units_open_runs_and_widening() -> None:
    open_run = clip(run("o", 900, None), Activity(950, 50, False), observed_at=990)
    tiny = clip(run("t", 100, 100.5))
    rows = {r["uid"]: r for r in frame([open_run, tiny], 0, 10_000)}
    assert (rows["o"]["start"], rows["o"]["stop"]) == (900_000, 990_000)
    assert rows["o"]["state"] == rows["o"]["status"] == "open"
    assert rows["t"]["stop"] - rows["t"]["start"] == 20_000  # 0.2 % of the window
    assert (rows["t"]["clip_from_ms"], rows["t"]["clip_to_ms"]) == (100_000, 100_500)


def test_widening_never_covers_the_next_clip() -> None:
    # Queued back to back: a 1 s rest, then a 17 s run 0.5 s later. In a 1 h
    # window the rest is widened (7.2 s) only up to the next start, and both
    # stay in one lane, as they do at 10 minutes.
    rest, pulse = clip(run("r", 0, 1)), clip(run("p", 1.5, 18.5))
    for window in (600, 3600):
        rows = {r["uid"]: r for r in frame([rest, pulse], -window + 20, 20)}
        assert rows["r"]["lane"] == rows["p"]["lane"] == "MOUSE", window
        assert rows["r"]["stop"] <= rows["p"]["start"] == 1_500
        assert (rows["p"]["start"], rows["p"]["stop"]) == (1_500, 18_500)
    rows = {r["uid"]: r for r in frame([rest, pulse], 0, 3600)}
    assert rows["r"]["stop"] == 1_500 and rows["r"]["clip_to_ms"] == 1_000


def test_clips_starting_together_never_share_a_sub_lane() -> None:
    a, b = clip(run("a", 100, 100)), clip(run("b", 100, 100))
    rows = frame([a, b], 0, 10_000)
    assert len({r["lane"] for r in rows}) == 2
    assert all(r["stop"] - r["start"] == 20_000 for r in rows)


def test_state_shows_inference_but_status_stays_recorded() -> None:
    stalled = clip(run("s", 0, None), Activity(10, 5000, True))
    stale = clip(run("x", 0, None), stale=True)
    unknown = clip(run("u", 0, None), Activity(10, None, False, "forbidden"))
    rows = {r["uid"]: r for r in frame([stalled, stale, unknown], 0, 10_000)}
    assert (rows["s"]["state"], rows["s"]["status"]) == ("possibly stalled", "open")
    assert (rows["x"]["state"], rows["x"]["status"], rows["x"]["stale"]) == ("stale", "open", True)
    assert (rows["u"]["state"], rows["u"]["status"], rows["u"]["possibly_stalled"]) == (
        "activity unknown",
        "open",
        False,
    )
    assert rows["u"]["activity_error"] == "forbidden"


def test_lanes_are_grouped_acquisition_before_processing() -> None:
    clips = [
        clip(run("p", 10, 20, kind="modacor_correction")),
        clip(run("g", 5, 30, instrument="generic")),
        clip(run("m", 0, 40)),
    ]
    assert [r["lane"] for r in frame(clips, 0, 100)] == [
        "MOUSE",
        "MOUSE · processing",
        "generic",
    ]


def test_run_without_start_time_is_not_a_clip() -> None:
    assert make_clip(RunRecord("", "x", {"uid": "x"}, None), QUIET, False, "x") is None


def test_status_of_clip() -> None:
    aborted = RunRecord("", "a", {"uid": "a", "time": 0}, {"time": 1, "exit_status": "abort"})
    assert clip(aborted).status is Status.ABORTED


def test_lane_keys_are_unambiguous() -> None:
    missing = LaneKey(None, "X", None, Role.ACQUISITION)
    dash = LaneKey("-", "X", "-", Role.ACQUISITION)
    slash = LaneKey("A/B", "X", None, Role.ACQUISITION)
    assert missing.as_text() == "-/X/-/acquisition"
    assert dash.as_text() == "%2D/X/%2D/acquisition"
    assert slash.as_text() == "A%2FB/X/-/acquisition"
    other = LaneKey("A", "B/X", None, Role.ACQUISITION)
    texts = {k.as_text() for k in (missing, dash, slash, other)}
    assert len(texts) == 4


def test_different_lane_keys_never_share_a_name() -> None:
    a = LaneKey("A", "X Y", None, Role.ACQUISITION)
    b = LaneKey("A", "X", "Y", Role.ACQUISITION)
    c = LaneKey("B", "X Y", None, Role.ACQUISITION)
    names = lane_names([c, b, a])
    assert len(set(names.values())) == 3
    assert names == lane_names([a, b, c])  # the same names whatever the order


def test_sub_lanes_never_take_another_lanes_name() -> None:
    # An instrument literally called "X /2" next to X's second sub-lane.
    clips = [
        clip(run("a", 100, 200, instrument="X")),
        clip(run("b", 150, 250, instrument="X")),
        clip(run("c", 150, 250, instrument="X /2")),
    ]
    rows = frame(clips, 0, 1000)
    lanes = {r["uid"]: r["lane"] for r in rows}
    assert lanes["a"] == "X" and lanes["c"] == "X /2"
    assert lanes["b"] not in {"X", "X /2"} and lanes["b"].startswith("X /2")
    # One final lane, one lane key: no overlapping clips in one series.
    by_lane: dict[str, set[str]] = {}
    for r in rows:
        by_lane.setdefault(r["lane"], set()).add(r["uid"])
    assert all(len(uids) == 1 for uids in by_lane.values())


def test_zoom_range_is_never_empty() -> None:
    clips = [clip(run("z", 100, 100)), clip(run("l", 0, 1000))]
    rows = {r["uid"]: r for r in frame(clips, 0, 2000)}
    zero = rows["z"]
    assert zero["clip_from_ms"] == zero["clip_to_ms"] == 100_000
    assert (zero["zoom_from_ms"], zero["zoom_to_ms"]) == (95_000, 105_000)
    assert (rows["l"]["zoom_from_ms"], rows["l"]["zoom_to_ms"]) == (-50_000, 1_050_000)


def test_open_clips_end_at_their_own_observation() -> None:
    fresh = clip(run("f", 100, None, instrument="A"), observed_at=1100)
    stale = clip(run("s", 100, None, instrument="B"), stale=True, observed_at=1000)
    rows = {r["uid"]: r for r in frame([fresh, stale], 0, 2000)}
    assert rows["f"]["stop"] == rows["f"]["clip_to_ms"] == 1_100_000
    assert rows["s"]["stop"] == rows["s"]["clip_to_ms"] == 1_000_000


def test_closed_run_without_stop_time_is_not_drawn_as_running() -> None:
    broken = RunRecord("", "b", {"uid": "b", "time": 100.0}, {"exit_status": "success"})
    [row] = frame([clip(broken, observed_at=5000)], 0, 10_000)
    assert row["clip_to_ms"] == 100_000 and row["state"] == "success"
