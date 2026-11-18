"""Sub-lanes and the frame (plan §3.1, §8), in the default preset's lanes."""

from typing import Any

from event_timeline_tiled.clips import Clip, make_clip
from event_timeline_tiled.clips import frame as draw_frame
from event_timeline_tiled.lanes import arrange, final_names
from event_timeline_tiled.layout import RunRecord
from event_timeline_tiled.presets import load_registry
from event_timeline_tiled.run_state import Activity, Status

QUIET = Activity(None, None, False)
DEFAULT = load_registry().get("instruments")


def run(key: str, start: float, stop: float | None, root: str = "", **md: Any) -> RunRecord:
    start_doc = {"uid": key, "time": start, "facility": "BAM", "instrument": "MOUSE", **md}
    stop_doc = None if stop is None else {"time": stop, "exit_status": "success"}
    return RunRecord(root, key, start_doc, stop_doc)


def clip(
    r: RunRecord, act: Activity = QUIET, stale: bool = False, observed_at: float = 1000.0
) -> Clip:
    c = make_clip(r, act, stale, r.key, observed_at)
    assert c is not None
    RECORDS[(c.root, c.uid)] = r
    return c


RECORDS: dict[tuple[str, str], RunRecord] = {}


def frame(
    clips: list[Clip], t_from: float, t_to: float, min_lanes: int = 0, max_rows: int | None = None
) -> list[dict[str, Any]]:
    arranged = arrange(DEFAULT, clips, RECORDS, 0.0, lambda r: None, 100, 20_000)
    names, taken = final_names(arranged.lanes)
    return draw_frame(arranged.placements, names, taken, t_from, t_to, min_lanes, max_rows)[0]


def test_overlaps_get_sub_lanes_and_touching_clips_share() -> None:
    clips = [
        clip(run("a", 100, 200)),
        clip(run("b", 150, 250)),  # overlaps a
        clip(run("c", 200, 300)),  # touches a: shares its lane
        clip(run("d", 160, 170)),  # overlaps a and b
    ]
    rows = {r["uid"]: r["lane"] for r in frame(clips, 0, 1000)}
    m = "MOUSE · acquisition"
    assert rows == {"a": m, "b": f"{m} /2", "c": m, "d": f"{m} /3"}


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
        assert rows["r"]["lane"] == rows["p"]["lane"] == "MOUSE · acquisition", window
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
        "generic · acquisition",
        "MOUSE · acquisition",
        "MOUSE · processing",
    ]


def test_run_without_start_time_is_not_a_clip() -> None:
    assert make_clip(RunRecord("", "x", {"uid": "x"}, None), QUIET, False, "x") is None


def test_status_of_clip() -> None:
    aborted = RunRecord("", "a", {"uid": "a", "time": 0}, {"time": 1, "exit_status": "abort"})
    assert clip(aborted).status is Status.ABORTED


def test_sub_lanes_never_take_another_lanes_name() -> None:
    # A station literally called "acquisition /2" next to X's second sub-lane.
    clips = [
        clip(run("a", 100, 200, instrument="X")),
        clip(run("b", 150, 250, instrument="X")),
        clip(
            run("c", 150, 250, instrument="X", station="acquisition /2", kind="modacor_correction")
        ),
    ]
    rows = frame(clips, 0, 1000)
    lanes = {r["uid"]: r["lane"] for r in rows}
    second = "X · acquisition /2 · processing"
    assert lanes["a"] == "X · acquisition" and lanes["c"] == second
    assert lanes["b"] == "X · acquisition /2" and lanes["b"] not in {lanes["a"], second}
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


def test_few_lanes_are_padded_with_empty_lanes_below() -> None:
    clips = [clip(run("a", 100, 200)), clip(run("b", 150, 250))]  # one lane, two sub-lanes
    real = frame(clips, 0, 1000)
    padded = frame(clips, 0, 1000, min_lanes=4)
    assert padded[: len(real)] == real
    empty = padded[len(real) :]
    lanes = [r["lane"] for r in empty]
    assert len(lanes) == 2 and len(set(lanes)) == 2
    assert all(name.strip() == "" and name not in {r["lane"] for r in real} for name in lanes)
    assert all(r["start"] == r["stop"] == 0 and r["state"] is None for r in empty)
    assert set(empty[0]) == set(real[0])  # the same columns, for Grafana
    assert frame(clips, 0, 1000, min_lanes=2) == real == frame(clips, 0, 1000, min_lanes=0)


def test_rows_carry_the_placement_and_respect_the_row_budget() -> None:
    clips = [clip(run("a", 100, 200)), clip(run("b", 150, 250)), clip(run("g", 0, 10,
             instrument="zz"))]  # fmt: skip
    rows = frame(clips, 0, 1000)
    assert {r["track"] for r in rows} == {"runs"} and {r["root"] for r in rows} == {"/"}
    assert (
        rows[0]["lane_id"].startswith("instruments.runs.")
        and len({r["lane_id"] for r in rows}) == 2
    )
    arranged = arrange(DEFAULT, clips, RECORDS, 0.0, lambda r: None, 100, 20_000)
    names, taken = final_names(arranged.lanes)
    kept, left_out = draw_frame(arranged.placements, names, taken, 0, 1000, 0, max_rows=2)
    # Rows in lane order: MOUSE and its second sub-lane fit, zz does not.
    assert {r["uid"] for r in kept} == {"a", "b"} and left_out == 1
