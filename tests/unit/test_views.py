"""The view table and its fallback chain (plan §7)."""

from typing import Any

import pytest

from event_timeline_tiled.layout import RunRecord, StreamInfo
from event_timeline_tiled.run_state import Role
from event_timeline_tiled.views import ViewKind, ViewSpec, label, resolve, stall_after_s

NUM = {"dtype": "number", "shape": [], "source": "sim"}


def run(**start: Any) -> RunRecord:
    return RunRecord("", "abcdef0123", {"uid": "abcdef0123", "time": 0, **start}, None)


def stream(name: str, fields: dict[str, Any], hints: tuple[str, ...] = ()) -> StreamInfo:
    return StreamInfo(
        name, fields, hints, has_scalar_fields=any(not f.get("external") for f in fields.values())
    )


MOUSE_STREAMS = [
    stream("baseline", {"motor": NUM}),
    stream("mouse_eiger_measure", {"roi": {**NUM, "units": "counts"}, "stats": NUM}),
    stream("snapshot", {"env_temperature": NUM}),
]


def test_mouse_measurement_defaults_to_readings() -> None:
    views = resolve(run(instrument="MOUSE"), Role.ACQUISITION, MOUSE_STREAMS)
    # No hinted detector fields here: "hinted" is listed, unavailable, after the others.
    assert [(v.name, v.available) for v in views] == [
        ("readings", True),
        ("numeric", True),
        ("summary", True),
        ("hinted", False),
    ]
    readings = views[0]
    assert readings.streams == ("mouse_eiger_measure", "snapshot", "baseline")
    assert readings.units == {"roi": "counts"}
    numeric = views[1]
    assert (numeric.kind, numeric.streams) == (ViewKind.TIMESERIES, ("mouse_eiger_measure",))
    assert (numeric.fields, dict(numeric.units)) == (("roi", "stats"), {"roi": "counts"})


def test_mouse_time_series_prefers_the_detectors_hinted_fields() -> None:
    """The first available time series is what Run time series and the trend show."""
    detector = MOUSE_STREAMS[1]
    hinted = [MOUSE_STREAMS[0], stream(detector.name, dict(detector.data_keys), ("roi",))]
    views = resolve(run(instrument="MOUSE"), Role.ACQUISITION, hinted)
    series = [v for v in views if v.available and v.kind is ViewKind.TIMESERIES]
    assert [(v.name, v.fields) for v in series] == [
        ("hinted", ("roi",)),
        ("numeric", ("roi", "stats")),
    ]
    assert views[0].name == "readings"  # the default is still the table


def test_a_time_series_view_reads_exactly_one_stream() -> None:
    for streams in ((), ("a", "b")):
        with pytest.raises(ValueError, match="exactly one stream"):
            ViewSpec("x", "X", ViewKind.TIMESERIES, streams)


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


ECHEM_STREAMS = [
    stream("baseline", {"x": NUM}, ("x",)),
    stream("aux", {"y": NUM}),
    stream(
        "readout",
        {"voltage_v": {**NUM, "units": "V"}, "current_a": {**NUM, "units": "A"}, "t_s": NUM},
        ("voltage_v", "current_a"),
    ),
    stream("sequence_events", {"event_t_s": NUM}),
]


def test_electrochemistry_views_follow_its_hints() -> None:
    """No rules of its own: the fallback's hinted view, on the hinted stream
    (not ``baseline``, not the first stream by name)."""
    start = {"instrument": "electrochemistry-k2460", "station": "simulator"}
    views = resolve(run(**start), Role.ACQUISITION, ECHEM_STREAMS)
    first = views[0]
    assert (first.name, first.kind, first.streams) == ("hinted", ViewKind.TIMESERIES, ("readout",))
    assert first.fields == ("voltage_v", "current_a")
    assert first.units == {"voltage_v": "V", "current_a": "A"}
    labelled = run(**start, measurement_id="example-rest", sample_id="S-1", plan_name="x")
    assert label(labelled) == "example-rest · S-1"
    assert stall_after_s(run(**start)) == 600


def test_fallback_without_hints_takes_the_first_stream_but_baseline() -> None:
    plain = [stream("baseline", {"x": NUM}, ("x",)), stream("b", {"y": NUM}), stream("c", {})]
    views = resolve(run(), Role.ACQUISITION, plain)
    assert (views[0].name, views[0].streams) == ("numeric", ("b",))
    only_baseline = resolve(run(), Role.ACQUISITION, plain[:1])
    assert (only_baseline[0].name, only_baseline[0].streams) == ("hinted", ("baseline",))


def _array(shape: list[int], **extra: Any) -> dict[str, Any]:
    return {"dtype": "array", "shape": shape, "external": "STREAM:", **extra}


CORRECTED = [
    stream(
        "corrected_1d",
        {
            "Q": _array([100], units="1/nm"),
            "I": _array([100], units="1/(m sr)"),
            "I_uncertainty": _array([100], uncertainty_of="I"),
        },
    ),
    stream("corrected_2d", {"image": _array([64, 32])}),
]


def test_mouse_processing_run_has_curve_and_image() -> None:
    views = resolve(run(instrument="MOUSE", kind="modacor_correction"), Role.PROCESSING, CORRECTED)
    assert [(v.name, v.kind, v.available) for v in views] == [
        ("corrected_curve", ViewKind.CURVE, True),
        ("corrected_image", ViewKind.IMAGE, True),
        ("summary", ViewKind.SUMMARY, True),
    ]
    curve, image = views[0], views[1]
    # x, y, then the uncertainty of y the stream declares.
    assert curve.fields == ("Q", "I", "I_uncertainty")
    assert dict(curve.units) == {"Q": "1/nm", "I": "1/(m sr)"}
    assert (image.streams, image.fields) == (("corrected_2d",), ("image",))


def test_processing_run_without_an_image_still_has_its_curve() -> None:
    views = resolve(run(instrument="MOUSE"), Role.PROCESSING, CORRECTED[:1])
    assert [(v.name, v.available) for v in views] == [
        ("corrected_curve", True),
        ("summary", True),
        ("corrected_image", False),
    ]
    assert views[2].reason == "this run has no corrected_2d"


def test_array_views_need_arrays_of_the_right_rank() -> None:
    scalar = [stream("corrected_1d", {"Q": NUM, "I": _array([5])})]
    # Neither curve nor image: the processing rule does not apply; the fallback does.
    views = resolve(run(instrument="MOUSE"), Role.PROCESSING, scalar)
    assert views[0].name == "numeric"
    flat = [stream("corrected_2d", {"image": _array([5])})]
    assert resolve(run(instrument="MOUSE"), Role.PROCESSING, flat)[0].name == "no_data"


def test_array_views_name_their_fields() -> None:
    with pytest.raises(ValueError, match="names 2 fields"):
        ViewSpec("c", "C", ViewKind.CURVE, ("s",), fields=("x",))
    with pytest.raises(ValueError, match="exactly one stream"):
        ViewSpec("i", "I", ViewKind.IMAGE, ("a", "b"), fields=("image",))
    with pytest.raises(ValueError, match="names 0 fields"):
        ViewSpec("t", "T", ViewKind.TABLE, ("s",), fields=("x",))
