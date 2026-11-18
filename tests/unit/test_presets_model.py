"""Preset format v1: validation (presets.md §4)."""

import re
from pathlib import Path

import pytest

from event_timeline_tiled.presets import PresetError, load, parse_preset
from event_timeline_tiled.presets.predicates import All, Leaf, Not

GOOD = """\
version: 1
id: beamline-by-sample
title: Beamline by sample
roots: [acquisition]
scope:
  - {field: start.beamline, op: in, value: [beamline-a]}
tracks:
  - id: measurements
    name: Measurements
    where:
      all:
        - {field: role, op: eq, value: acquisition}
        - {field: start.plan_name, op: regex, value: '^measure_'}
    require_streams: [primary]
    group_by: {field: start.sample_id, fallback: '(no sample)'}
    label: ['{start.sample_id}', '{start.plan_name}']
  - id: failures
    name: Failed or aborted
    where: {field: status, op: in, value: [failed, aborted]}
    group_by:
      fields: [start.beamline, start.station]
lanes:
  order: [failures]
  min_lanes: 4
  max_lanes: 100
"""


def parse(text: str, roots: tuple[str, ...] | None = ("acquisition", "processed"),
          budget: int | None = 200):  # type: ignore[no-untyped-def]  # fmt: skip
    data, marks = load(text, "p.yaml")
    return parse_preset(data, marks, "p.yaml", roots, budget)


def test_the_design_example_loads() -> None:
    preset = parse(GOOD)
    assert (preset.id, preset.roots, preset.lanes.min_lanes) == (
        "beamline-by-sample", ("acquisition",), 4,
    )  # fmt: skip
    # lanes.order first, the others after, in file order.
    assert [t.id for t in preset.tracks] == ["failures", "measurements"]
    measurements = preset.tracks[1]
    assert isinstance(measurements.where, All) and measurements.require_streams == ("primary",)
    assert measurements.group_by is not None and measurements.group_by.fallback == "(no sample)"
    assert preset.tracks[0].group_by.fields == (("start", "beamline"), ("start", "station"))  # type: ignore[union-attr]
    assert preset.scope == (Leaf("start.beamline", ("start", "beamline"), "in", ("beamline-a",)),)


def edit(old: str, new: str) -> str:
    assert old in GOOD
    return GOOD.replace(old, new, 1)


@pytest.mark.parametrize(
    ("text", "path", "reason"),
    [
        (edit("version: 1", "version: true"), "version", "whole number 1"),
        (edit("version: 1", "version: 1.0"), "version", "whole number 1"),
        (edit("version: 1", "version: 2"), "version", "whole number 1"),
        (edit("version: 1\n", ""), "", "missing key(s) version"),
        (edit("id: beamline-by-sample", "id: Beamline"), "id", "not an identity"),
        (edit("title: Beamline by sample", "title: ''"), "title", "non-empty text"),
        (edit("title: Beamline by sample", "title: null"), "title", "null is not allowed"),
        (GOOD + "colour: red\n", "", "unknown key(s) colour"),
        (edit("    name: Measurements", "    name: Measurements\n    colour: red"),
         "tracks[0]", "unknown key(s) colour"),
        (edit("roots: [acquisition]", "roots: [other]"), "roots", "not configured"),
        (edit("roots: [acquisition]", "roots: []"), "roots", "must not be empty"),
        (edit("  - id: failures", "  - id: measurements"), "tracks", "repeated track id"),
        (edit("op: regex, value: '^measure_'", "op: like, value: x"), "tracks[0].where.all[1].op",
         "unknown operator"),
        (edit("op: regex, value: '^measure_'", "op: regex, value: '('"),
         "tracks[0].where.all[1].value", "not a valid regular expression"),
        (edit("{field: role, op: eq, value: acquisition}", "{field: role, op: eq, value: null}"),
         "tracks[0].where.all[0].value", "null is not allowed"),
        (edit("{field: role, op: eq, value: acquisition}", "{field: role, op: exists, value: 1}"),
         "tracks[0].where.all[0]", "unknown key(s) value"),
        (edit("{field: role, op: eq, value: acquisition}", "{field: rol, op: exists}"),
         "tracks[0].where.all[0].field", "starts with one of"),
        (edit("{field: role, op: eq, value: acquisition}", "{field: role.x, op: exists}"),
         "tracks[0].where.all[0].field", "no keys below it"),
        (edit("{field: role, op: eq, value: acquisition}",
              "{field: start.time, op: gt, value: {relative: -1y}}"),
         "tracks[0].where.all[0].value.relative", "like '-24h'"),
        (edit("{field: role, op: eq, value: acquisition}",
              "{field: start.time, op: eq, value: {relative: '-1h'}}"),
         "tracks[0].where.all[0].value", "only with gt"),
        (edit("{field: role, op: eq, value: acquisition}", "{field: start.n, op: gt, value: x}"),
         "tracks[0].where.all[0].value", "needs a number"),
        (edit("{field: status, op: in, value: [failed, aborted]}",
              "[{field: status, op: exists}]"), "tracks[1].where", "wrap a list in all"),
        (edit("{field: status, op: in, value: [failed, aborted]}",
              "{all: [], any: []}"), "tracks[1].where", "only key"),
        (edit("{field: status, op: in, value: [failed, aborted]}",
              "{field: status, op: in, value: [failed, null]}"), "tracks[1].where.value",
         "null in a list"),
        (edit("- {field: start.beamline, op: in, value: [beamline-a]}",
              "- {field: start.beamline, op: neq, value: x}"), "scope[0].op", "only eq and in"),
        (edit("- {field: start.beamline, op: in, value: [beamline-a]}",
              "- {field: role, op: eq, value: x}"), "scope[0].field", "start-document keys"),
        (edit("- {field: start.beamline, op: in, value: [beamline-a]}",
              "- {field: start.n, op: in, value: [1, a]}"), "scope[0].value", "one type"),
        (edit("- {field: start.beamline, op: in, value: [beamline-a]}",
              "- {field: start.n, op: eq, value: [1]}"), "scope[0].value", "text, numbers"),
        (edit("fields: [start.beamline, start.station]", "field: x\n      fields: [y]"),
         "tracks[1].group_by", "exactly one of"),
        (edit("fields: [start.beamline, start.station]", "fields: [streams]"),
         "tracks[1].group_by.fields", "cannot be a group"),
        (edit("fields: [start.beamline, start.station]",
              "fields: [start.beamline]\n      name: '{start.station}'"),
         "tracks[1].group_by.name", "only use the group's own fields"),
        (edit("fields: [start.beamline, start.station]",
              "fields: [start.beamline]\n      invalid: drop"), "tracks[1].group_by.invalid",
         "exclude, missing"),
        (edit("label: ['{start.sample_id}', '{start.plan_name}']", "label: '{start.x|a|b}'"),
         "tracks[0].label", "may not contain '|'"),
        (edit("order: [failures]", "order: [nope]"), "lanes.order", "unknown track id"),
        (edit("min_lanes: 4", "min_lanes: 21"), "lanes.min_lanes", "0–20"),
        (edit("max_lanes: 100", "max_lanes: 500"), "lanes.max_lanes", "only lower"),
        (edit("max_lanes: 100", "max_lanes: 1.5"), "lanes.max_lanes", "whole number"),
    ],
)  # fmt: skip
def test_validation_errors_point_into_the_file(text: str, path: str, reason: str) -> None:
    with pytest.raises(PresetError, match=re.escape(reason)) as caught:
        parse(text)
    assert caught.value.path == path and caught.value.source == "p.yaml"
    assert caught.value.line is not None


def test_minimal_preset_and_defaults() -> None:
    preset = parse("version: 1\nid: all\ntitle: All runs\ntracks: [{id: runs, name: Runs}]\n")
    [track] = preset.tracks
    assert (preset.roots, preset.scope, track.group_by, track.label) == (None, (), None, None)
    assert track.lane_prefix is True and preset.lanes.max_lanes is None


def test_regex_length_and_predicate_count(monkeypatch: pytest.MonkeyPatch) -> None:
    long = edit("value: '^measure_'", f"value: '{'a' * 1025}'")
    with pytest.raises(PresetError, match="at most 1024"):
        parse(long)
    from event_timeline_tiled.presets import model

    monkeypatch.setattr(model, "MAX_PREDICATE_NODES", 2)
    with pytest.raises(PresetError, match="more than 2 predicate nodes"):
        parse(GOOD)


def test_not_is_parsed() -> None:
    text = edit("where: {field: status, op: in, value: [failed, aborted]}",
                "where: {not: {field: status, op: eq, value: success}}")  # fmt: skip
    assert isinstance(parse(text).tracks[0].where, Not)


def test_the_shipped_default_is_a_valid_preset() -> None:
    from event_timeline_tiled import presets

    raw = (Path(presets.__file__).parent / "instruments.yaml").read_text()
    preset = parse(raw, roots=None)
    [track] = preset.tracks
    assert track.lane_prefix is False and track.group_by is not None
    assert track.group_by.field_texts == (
        "root", "start.facility", "start.instrument", "start.station", "role",
    )  # fmt: skip
    assert track.group_by.invalid == "missing"
