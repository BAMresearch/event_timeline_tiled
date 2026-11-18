"""Lanes and placements: a preset applied to runs (presets.md §6)."""

from typing import Any

from event_timeline_tiled.clips import Clip, make_clip
from event_timeline_tiled.lanes import Arrangement, arrange, final_names, lane_id
from event_timeline_tiled.layout import RunRecord
from event_timeline_tiled.presets import Preset, load, load_registry, parse_preset
from event_timeline_tiled.run_state import Activity

QUIET = Activity(None, None, False)
DEFAULT = load_registry().get("instruments")


def run(key: str, start: float = 0.0, root: str = "", stop: dict[str, Any] | None = None,
        **md: Any) -> RunRecord:  # fmt: skip
    done = stop if stop is not None else {"time": start + 1, "exit_status": "success"}
    return RunRecord(root, key, {"uid": key, "time": start, **md}, done)


def apply(
    preset: Preset, *runs: RunRecord, streams: tuple[str, ...] | None = None, **budget: int
) -> Arrangement:
    clips: list[Clip] = []
    for r in runs:
        c = make_clip(r, QUIET, False, f"view label {r.key}", 0.0)
        assert c is not None
        clips.append(c)
    records = {(r.root, r.key): r for r in runs}
    return arrange(preset, clips, records, 1000.0, lambda r: streams,
                   budget.get("lanes", 100), budget.get("placements", 20_000))  # fmt: skip


def names(arranged: Arrangement) -> dict[str, str]:
    """Run key → its first lane's final name."""
    final, _ = final_names(arranged.lanes)
    return {p.clip.uid: final[p.lane.id] for p in arranged.placements}


def preset(text: str) -> Preset:
    data, marks = load("version: 1\nid: p\ntitle: P\n" + text)
    return parse_preset(data, marks)


def test_the_default_preset_lanes() -> None:
    arranged = apply(
        DEFAULT,
        run("m", facility="BAM", instrument="MOUSE"),
        run("p", facility="BAM", instrument="MOUSE", kind="modacor_correction"),
        run("s", facility="BAM", instrument="MOUSE", station="S1"),
        run("x"),
        run("e", facility="BAM", instrument="electrochemistry-k2460"),
    )
    assert names(arranged) == {
        "m": "MOUSE · acquisition", "p": "MOUSE · processing", "s": "MOUSE · S1 · acquisition",
        "x": "(unknown) · acquisition", "e": "electrochemistry-k2460 · acquisition",
    }  # fmt: skip
    # Sorted by root, facility, instrument, station, role (text case-insensitively,
    # missing last): processing right after its acquisition lane.
    assert [lane.base_name for lane in arranged.lanes] == [
        "electrochemistry-k2460 · acquisition", "MOUSE · S1 · acquisition",
        "MOUSE · acquisition", "MOUSE · processing", "(unknown) · acquisition",
    ]  # fmt: skip


def test_roots_and_facilities_are_part_of_the_key_not_the_name() -> None:
    arranged = apply(
        DEFAULT,
        run("a", root="raw", instrument="X"),
        run("b", root="other", instrument="X"),
        run("c", root="raw", facility="F2", instrument="X"),
    )
    final = names(arranged)
    assert len(arranged.lanes) == 3 and len(set(final.values())) == 3
    assert sorted(final.values()) == ["X · acquisition", "X · acquisition (2)",
                                      "X · acquisition (3)"]  # fmt: skip


def test_list_valued_identity_fields_count_as_missing_in_the_default() -> None:
    arranged = apply(
        DEFAULT, run("l", instrument=["X"]), run("m", facility={"a": 1}, instrument="Y")
    )
    assert names(arranged) == {"l": "(unknown) · acquisition", "m": "Y · acquisition"}
    assert arranged.invalid_groups == 2 and arranged.matched == 2


def test_a_missing_value_and_the_fallback_text_are_different_lanes() -> None:
    p = preset("tracks: [{id: t, name: T, lane_prefix: false, group_by: {field: start.s}}]\n")
    arranged = apply(p, run("a"), run("b", s="(unknown)"), run("c", s=""), run("d", s=None))
    final = names(arranged)
    # Text sorts before missing, so the literal text keeps the plain name.
    assert final["a"] == final["c"] == final["d"] == "(unknown) (2)"
    assert final["b"] == "(unknown)" and len(arranged.lanes) == 2


def test_numbers_are_one_group_strings_another() -> None:
    p = preset("tracks: [{id: t, name: T, lane_prefix: false, group_by: {field: start.n}}]\n")
    arranged = apply(p, run("a", n=1), run("b", n=1.0), run("c", n="1"), run("d", n=True))
    lanes = {p.clip.uid: p.lane.id for p in arranged.placements}
    assert lanes["a"] == lanes["b"] != lanes["c"] != lanes["d"]
    # Booleans, then numbers, then strings.
    assert [lane.key for lane in arranged.lanes] == [(True,), (1,), ("1",)]


def test_invalid_exclude_leaves_the_run_out_of_that_track() -> None:
    p = preset("tracks:\n  - {id: t, name: T, group_by: {field: start.s}}\n"
               "  - {id: all, name: All}\n")  # fmt: skip
    arranged = apply(p, run("a", s=[1]), run("b", s="x"))
    tracks = sorted((p.clip.uid, p.lane.track) for p in arranged.placements)
    assert tracks == [("a", "all"), ("b", "all"), ("b", "t")]
    assert arranged.invalid_groups == 1


def test_a_run_appears_in_every_track_it_matches() -> None:
    p = preset(
        "tracks:\n"
        "  - {id: all, name: All runs}\n"
        "  - {id: failed, name: Failed, where: {field: status, op: eq, value: failed}}\n"
        "lanes: {order: [failed]}\n"
    )
    failed = run("f", stop={"time": 5, "exit_status": "fail"})
    arranged = apply(p, failed, run("ok"))
    assert [(p.clip.uid, p.lane.track) for p in arranged.placements] == [
        ("f", "failed"), ("f", "all"), ("ok", "all"),
    ]  # fmt: skip
    assert arranged.matched == 2 and len(arranged.placements) == 3
    assert arranged.first_lane("", "f") == arranged.lanes[0]  # lanes.order first
    assert [c.uid for c in arranged.runs_in(arranged.lanes[1].id)] == ["f", "ok"]


def test_scope_and_require_streams() -> None:
    p = preset("scope: [{field: start.instrument, op: eq, value: MOUSE}]\n"
               "tracks: [{id: t, name: T, require_streams: [primary]}]\n")  # fmt: skip
    arranged = apply(p, run("m", instrument="MOUSE"), run("o", instrument="other"),
                     streams=("primary",))  # fmt: skip
    assert [pl.clip.uid for pl in arranged.placements] == ["m"] and arranged.out_of_scope == 1
    unknown = apply(p, run("m", instrument="MOUSE"), streams=None)
    assert (unknown.placements, unknown.streams_unknown) == ([], 1)


def test_labels_from_a_template_else_the_view_table() -> None:
    p = preset("tracks: [{id: t, name: T, label: ['{start.sample}', '{start.plan_name}']}]\n")
    arranged = apply(p, run("a", sample="S1", plan_name="count"), run("b"))
    labels = {pl.clip.uid: pl.label for pl in arranged.placements}
    assert labels == {"a": "S1 · count", "b": "view label b"}


def test_names_templates_prefix_and_cut() -> None:
    p = preset("tracks: [{id: t, name: Samples, group_by: {field: start.s, "
               "name: 'sample {start.s|none}'}}]\n")  # fmt: skip
    arranged = apply(p, run("a", s="S1"), run("b"), run("c", s="x" * 300))
    final = names(arranged)
    assert final["a"] == "Samples · sample S1" and final["b"] == "Samples · sample none"
    assert len(final["c"]) == 180 and final["c"].endswith("…")


def test_lane_ids_are_stable_and_url_safe() -> None:
    first = apply(DEFAULT, run("m", instrument="MOUSE")).lanes[0].id
    again = apply(DEFAULT, run("other", 99.0, instrument="MOUSE")).lanes[0].id
    assert first == again == lane_id("instruments", "runs", (None, None, "MOUSE", None,
                                                              "acquisition"))  # fmt: skip
    assert first.replace(".", "").replace("-", "").isalnum()


def test_budgets_cut_lanes_and_placements_and_count_them() -> None:
    runs = [run(f"r{i}", float(i), instrument=f"I{i}") for i in range(5)]
    arranged = apply(DEFAULT, *runs, lanes=3)
    assert len(arranged.lanes) == 3 and arranged.lanes_cut == 2
    assert {p.lane.id for p in arranged.placements} == {lane.id for lane in arranged.lanes}
    cut = apply(DEFAULT, *runs, placements=2)
    assert len(cut.placements) == 2 and cut.placements_cut == 3
    capped = preset("tracks: [{id: t, name: T, group_by: {field: start.instrument}}]\n"
                    "lanes: {max_lanes: 2}\n")  # fmt: skip
    assert apply(capped, *runs).lanes_cut == 3
