"""Unfolding one lane: budgets and the frame's rows (presets.md §6.5)."""

from typing import Any

from event_timeline_tiled.clips import Clip, make_clip
from event_timeline_tiled.clips import frame as draw
from event_timeline_tiled.lanes import Arrangement, arrange, final_names
from event_timeline_tiled.layout import RunRecord, StreamExtent, StreamState
from event_timeline_tiled.presets import Preset, load, parse_preset
from event_timeline_tiled.run_state import Activity
from event_timeline_tiled.settings import Settings
from event_timeline_tiled.timeline import RunExtents
from event_timeline_tiled.unfold import Unfolded, unfold_lane

QUIET = Activity(None, None, False)
BY_NAME = "tracks: [{id: t, name: T, lane_prefix: false, group_by: {field: start.name}}]\n"


def preset(text: str = BY_NAME) -> Preset:
    data, marks = load("version: 1\nid: p\ntitle: P\n" + text)
    return parse_preset(data, marks)


def runs_of(*specs: tuple[str, float, str]) -> tuple[list[Clip], dict[tuple[str, str], RunRecord]]:
    clips, records = [], {}
    for key, start, name in specs:
        record = RunRecord("", key, {"uid": key, "time": start, "name": name},
                           {"time": start + 100, "exit_status": "success"})  # fmt: skip
        clip = make_clip(record, QUIET, False, key, 0.0)
        assert clip is not None
        clips.append(clip)
        records[("", key)] = record
    return clips, records


def drawn(stream: str, first: float, last: float) -> StreamExtent:
    return StreamExtent(stream, StreamState.DRAWN, first, last, 2)


def settings(**kw: Any) -> Settings:
    return Settings(tiled_url="http://t.test", tiled_api_key="k", **kw)


def arranged_runs(*specs: tuple[str, float, str]) -> tuple[Arrangement, dict[Any, RunRecord]]:
    clips, records = runs_of(*specs)
    return arrange(preset(), clips, records, 0.0, lambda r: None, 100, 1000), records


def test_budgets_runs_time_and_nothing_drawable() -> None:
    arranged, records = arranged_runs(("a", 0, "A"), ("b", 10, "A"), ("c", 20, "A"))
    lane = arranged.lanes[0].id
    extents = {"a": (drawn("x", 1, 2),), "b": (StreamExtent("x", StreamState.EMPTY, rows=0),),
               "c": (drawn("x", 21, 22),)}  # fmt: skip

    def read(run: RunRecord) -> RunExtents:
        return RunExtents(extents[run.key], 0.0, True, "")

    plan = unfold_lane(arranged, lane, records, read, settings(unfold_max_runs=2))
    # The latest two (c, b) are read; b has nothing drawable; a is beyond the cap.
    assert set(plan.extents) == {("", "c")} and (plan.runs_unfolded, plan.runs_folded) == (1, 2)
    assert plan.streams == {"drawn": 1, "empty": 1}
    assert plan.notes == ("only the latest 2 runs are unfolded",)
    ticks = iter([0.0, 0.0, 99.0, 99.0])
    timed = unfold_lane(arranged, lane, records, read, settings(unfold_time_s=5),
                        monotonic=lambda: next(ticks))  # fmt: skip
    assert timed.runs_unfolded == 1 and timed.runs_folded == 2
    assert "reading stopped after 5 s" in timed.notes[0]
    missing = unfold_lane(arranged, "p.t.000000000000", records, read, settings())
    assert missing.lane is None and missing.as_status()["found"] is False


def test_stream_rows_names_overlaps_and_the_earlier_row() -> None:
    # A lane literally named "A · x" next to lane A's stream row x.
    arranged, records = arranged_runs(("a", 0, "A"), ("b", 50, "A"), ("old", -500, "A"),
                                      ("z", 0, "A · x"))  # fmt: skip
    lane = next(lane for lane in arranged.lanes if lane.base_name == "A")
    plan = Unfolded("req", lane, {("", "a"): (drawn("x", 10, 60), drawn("y", 0, 100)),
                                  ("", "b"): (drawn("x", 55, 70),)},
                    runs_unfolded=2, runs_folded=1)  # fmt: skip
    names, taken = final_names(arranged.lanes)
    rows, _ = draw(arranged.placements, names, taken, -1000, 1000, unfolded=plan)
    by = {(r["uid"], r["stream"]): r for r in rows}
    assert by[("z", None)]["lane"] == "A · x"  # the real lane keeps its name
    assert by[("a", "x")]["lane"] == "A · x (2)"  # the stream row gives way
    assert by[("b", "x")]["lane"] == "A · x (2) /2"  # 55 is inside a's 10–60 for x
    assert by[("a", "y")]["lane"] == "A · y"
    assert by[("old", None)]["lane"] == "A · earlier runs"
    assert (by[("a", "x")]["stream_from_ms"], by[("a", "x")]["stream_to_ms"]) == (10_000, 60_000)
    assert (by[("a", "x")]["clip_from_ms"], by[("a", "x")]["clip_to_ms"]) == (0, 100_000)
    assert by[("a", "x")]["zoom_from_ms"] < 0  # zoom keeps the run's extent
    order = [r["lane"] for r in rows]
    # Lane A's streams in name order, then its earlier runs; lane "A · x" after.
    assert order.index("A · x (2)") < order.index("A · y") < order.index("A · earlier runs")
    assert len({r["lane"] for r in rows}) == len({(r["lane_id"], r["stream"], r["lane"])
                                                  for r in rows})  # fmt: skip
