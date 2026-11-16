"""The view table and its fallback chain (plan §7)."""

from typing import Any

from event_timeline_tiled.layout import RunRecord, StreamInfo
from event_timeline_tiled.run_state import Role
from event_timeline_tiled.views import ViewKind, label, resolve, stall_after_s

NUM = {"dtype": "number", "shape": [], "source": "sim"}


def run(**start: Any) -> RunRecord:
    return RunRecord("", "abcdef0123", {"uid": "abcdef0123", "time": 0, **start}, None)


def stream(name: str, fields: dict[str, Any], hints: tuple[str, ...] = ()) -> StreamInfo:
    return StreamInfo(
        name, fields, hints, has_table=any(not f.get("external") for f in fields.values())
    )


MOUSE_STREAMS = [
    stream("baseline", {"motor": NUM}),
    stream("mouse_eiger_measure", {"roi": {**NUM, "units": "counts"}, "stats": NUM}),
    stream("snapshot", {"env_temperature": NUM}),
]


def test_mouse_measurement_defaults_to_readings() -> None:
    views = resolve(run(instrument="MOUSE"), Role.ACQUISITION, MOUSE_STREAMS)
    assert [v.name for v in views] == ["readings", "summary"]
    readings = views[0]
    assert readings.streams == ("mouse_eiger_measure", "snapshot", "baseline")
    assert readings.units == {"roi": "counts"}


def test_mouse_configuration_and_vacuum_runs() -> None:
    for start in ({"plan_name": "apply_config"}, {"activity": "evacuate"}):
        views = resolve(run(instrument="MOUSE", **start), Role.ACQUISITION, MOUSE_STREAMS[:1])
        assert views[0].name == "baseline"


def test_mouse_rule_without_its_streams_falls_back() -> None:
    views = resolve(run(instrument="MOUSE"), Role.ACQUISITION, [stream("primary", {"x": NUM})])
    assert views[0].name == "numeric"


def test_fallback_chain() -> None:
    hinted = [
        stream("primary", {"a": NUM, "b": NUM, "s": {"dtype": "string", "shape": []}}, ("b",))
    ]
    views = resolve(run(instrument="other"), Role.ACQUISITION, hinted)
    assert [(v.name, v.kind, v.fields) for v in views[:3]] == [
        ("hinted", ViewKind.TIMESERIES, ("b",)),
        ("numeric", ViewKind.TIMESERIES, ("a", "b")),
        ("events", ViewKind.TABLE, ("a", "b", "s")),
    ]
    text_only = [stream("primary", {"s": {"dtype": "string", "shape": []}})]
    assert resolve(run(), Role.ACQUISITION, text_only)[0].name == "events"
    arrays = [
        stream("corrected_1d", {"I": {"dtype": "array", "shape": [9], "external": "STREAM:"}})
    ]
    first = resolve(run(instrument="MOUSE"), Role.PROCESSING, arrays)[0]
    assert (first.name, first.available) == ("no_data", False)


def test_labels_and_stall_thresholds_come_from_the_table() -> None:
    assert label(run(instrument="MOUSE", sampleid="S-1", plan_name="m")) == "S-1 · m"
    assert label(run(instrument="other", plan_name="scan", scan_id=4)) == "scan · 4"
    assert label(run()) == "abcdef01"
    processing = run(kind="modacor_correction", product="saxs", attempt=2, plan_name="modacor")
    assert label(processing) == "saxs · attempt 2"
    assert stall_after_s(run(instrument="MOUSE")) == 1200
    assert stall_after_s(run(instrument="other")) == 1800
