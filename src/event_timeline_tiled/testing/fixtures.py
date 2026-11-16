"""Write synthetic Bluesky runs into a disposable Tiled.

No real sample names, proposals, user names, hosts or paths appear here.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Literal

import bluesky.plan_stubs as bps
import bluesky.preprocessors as bpp
import event_model
import numpy as np
from attrs import define, field, frozen
from bluesky import RunEngine
from bluesky.callbacks.tiled_writer import TiledWriter
from bluesky.preprocessors import SupplementalData
from event_model.documents.stream_datum import StreamRange
from h5py import File as H5File  # type: ignore[import-untyped]
from ophyd.sim import SynSignal, motor  # type: ignore[attr-defined]

Doc = tuple[str, dict[str, Any]]
ExitStatus = Literal["success", "abort", "fail"]
DAY = 86400.0

PROCESSING_KIND = "modacor_correction"
PROCESSING_PRODUCT = "saxs_1d_2d"


@frozen
class ProcessingOutputs:
    """Sizes of the synthetic corrected products."""

    curve_points: int = 2000
    image_shape: tuple[int, int] = (1024, 1024)


@define
class FixtureWriter:
    """Writes acquisition and processing runs into the Tiled behind ``client``.

    ``processed_dir`` must be readable by the Tiled **server** at the same path,
    because processing arrays are registered there as external HDF5 files.
    """

    client: Any
    processed_dir: Path = field(converter=Path)
    outputs: ProcessingOutputs = field(factory=ProcessingOutputs)
    _writer: TiledWriter = field(init=False)
    _re: RunEngine = field(init=False)
    _signals: dict[str, SynSignal] = field(init=False)

    def __attrs_post_init__(self) -> None:
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self._writer = TiledWriter(self.client, batch_size=1)
        rng = np.random.default_rng(1)
        self._signals = {
            "roi": SynSignal(func=lambda: float(rng.integers(1000, 2000)), name="roi"),
            "stats": SynSignal(func=lambda: float(rng.random()), name="stats"),
            "env_temperature": SynSignal(func=lambda: 21.5, name="env_temperature"),
            "cell_voltage": SynSignal(
                func=lambda: float(3.7 + 0.1 * rng.random()), name="cell_voltage"
            ),
        }
        self._re = RunEngine({})
        self._re.preprocessors.append(SupplementalData(baseline=[motor]))

    # Acquisition runs -------------------------------------------------------------------

    def mouse_measurement(
        self,
        md: dict[str, Any],
        start: float,
        stop: float | None,
        *,
        n_events: int = 3,
        snapshot: bool = True,
        exit_status: ExitStatus = "success",
    ) -> str:
        """A MOUSE-like measurement: ``baseline``, optional ``snapshot``, and
        ``n_events`` readings in ``mouse_eiger_measure``. ``stop=None`` leaves it open."""
        roi, stats, temp = (self._signals[k] for k in ("roi", "stats", "env_temperature"))

        @bpp.run_decorator(md=md)
        def plan() -> Iterator[Any]:
            if snapshot:
                yield from bps.create("snapshot")
                yield from bps.read(temp)
                yield from bps.save()
            for _ in range(n_events):
                yield from bps.trigger_and_read([roi, stats], name="mouse_eiger_measure")

        return self._write_captured(plan(), start, stop, exit_status)

    def scalar_run(
        self,
        md: dict[str, Any],
        start: float,
        stop: float | None,
        *,
        n_events: int = 5,
        signal: str = "env_temperature",
        exit_status: ExitStatus = "success",
    ) -> str:
        """A generic run with ``n_events`` scalar readings in ``primary``."""
        sig = self._signals[signal]

        @bpp.run_decorator(md=md)
        def plan() -> Iterator[Any]:
            for _ in range(n_events):
                yield from bps.trigger_and_read([sig], name="primary")

        return self._write_captured(plan(), start, stop, exit_status)

    def mouse_measurement_live(
        self, md: dict[str, Any], *, interval: float, n_events: int = 3, snapshot: bool = True
    ) -> str:
        """A MOUSE-like measurement acquired now, in real time: the same streams as
        :meth:`mouse_measurement`, one ``mouse_eiger_measure`` reading every
        ``interval`` seconds, written to Tiled as the RunEngine emits them."""
        roi, stats, temp = (self._signals[k] for k in ("roi", "stats", "env_temperature"))

        @bpp.run_decorator(md=md)
        def plan() -> Iterator[Any]:
            if snapshot:
                yield from bps.create("snapshot")
                yield from bps.read(temp)
                yield from bps.save()
            for _ in range(n_events):
                yield from bps.sleep(interval)
                yield from bps.trigger_and_read([roi, stats], name="mouse_eiger_measure")

        token = self._re.subscribe(self._writer)
        try:
            uids = _uids(self._re(plan()))
        finally:
            self._re.unsubscribe(token)
        return str(uids[0])

    def operando_forever(
        self,
        md: dict[str, Any],
        now: float,
        *,
        backfill: int,
        step: float,
        interval: float,
        live_readings: int | None = None,
    ) -> str:
        """A generic high-cadence run that never closes (demo stack only).

        The RunEngine first takes ``backfill`` readings in ``primary`` at once;
        those documents are retimed to ``step`` seconds apart ending at ``now``,
        so the run appears to have started ``backfill * step`` seconds ago. It
        then takes one reading every ``interval`` seconds, in real time, until
        the process is stopped (or, for tests, ``live_readings`` readings and a
        stop document).
        """
        sig = self._signals["cell_voltage"]
        t_start = now - backfill * step
        state = {"backfilling": True}
        streams: dict[str, str] = {}

        def retime(name: str, doc: dict[str, Any]) -> None:
            if state["backfilling"]:
                doc = copy.deepcopy(doc)
                t = t_start
                if name == "descriptor":
                    streams[doc["uid"]] = doc["name"]
                if name == "event" and streams.get(doc["descriptor"]) == "primary":
                    t = t_start + doc["seq_num"] * step
                if "time" in doc:
                    doc["time"] = t
                if name == "event":
                    doc["timestamps"] = dict.fromkeys(doc["timestamps"], t)
            self._writer(name, doc)

        @bpp.run_decorator(md=md)
        def plan() -> Iterator[Any]:
            for _ in range(backfill):
                yield from bps.trigger_and_read([sig], name="primary")
            state["backfilling"] = False
            taken = 0
            while live_readings is None or taken < live_readings:
                yield from bps.sleep(interval)
                yield from bps.trigger_and_read([sig], name="primary")
                taken += 1

        token = self._re.subscribe(retime)
        try:
            uids = _uids(self._re(plan()))
        finally:
            self._re.unsubscribe(token)
        return str(uids[0])

    def _write_captured(
        self, plan: Any, start: float, stop: float | None, exit_status: ExitStatus
    ) -> str:
        docs: list[Doc] = []
        token = self._re.subscribe(lambda name, doc: docs.append((name, copy.deepcopy(doc))))
        try:
            self._re(plan)
        finally:
            self._re.unsubscribe(token)
        docs = _retime(docs, start, stop if stop is not None else start + 60.0)
        for name, doc in docs:
            if name == "stop":
                if stop is None:
                    continue
                doc["exit_status"] = exit_status
                if exit_status != "success":
                    doc["reason"] = f"synthetic {exit_status}"
            self._writer(name, doc)
        return str(docs[0][1]["uid"])

    # Processing runs --------------------------------------------------------------------

    def processing_run(
        self,
        parents: Sequence[str],
        start: float,
        stop: float | None,
        *,
        instrument: str = "MOUSE",
        station: str | None = None,
        exit_status: ExitStatus = "success",
        with_outputs: bool = True,
        request_id: str | None = None,
        attempt: int = 1,
        reason: str | None = None,
        partial: bool = False,
    ) -> str:
        """A processing run per the draft contract (processing-runs.md §1, §1.1).

        ``with_outputs=False`` publishes nothing: with ``success`` that is the
        contract's "nothing to do" case, so a ``reason`` is required. A failed
        run with outputs is the contract's partial-output failure.
        """
        if stop is not None and exit_status == "success" and not with_outputs and not reason:
            raise ValueError("a successful run without outputs must state a reason")
        if partial and exit_status != "fail":
            raise ValueError("partial outputs are a failure (processing-runs.md §1)")
        md: dict[str, Any] = {
            "facility": "BAM",
            "instrument": instrument,
            "processing_schema_version": 1,
            "kind": PROCESSING_KIND,
            "product": PROCESSING_PRODUCT,
            "derived_from": list(parents),
            "inputs": {},
            "pipeline": {"name": "synthetic-correction", "version": "0", "hash": "synthetic"},
            "software": {"modacor": "synthetic"},
            "request_id": request_id or f"req-{parents[0]}",
            "attempt": attempt,
            "modacor": {"session_id": f"session-{parents[0][:8]}", "run_id": f"run-{attempt}"},
            "plan_name": "modacor",
        }
        if station is not None:
            md["station"] = station
        bundle = event_model.compose_run(metadata=md, time=start)
        docs: list[Doc] = [("start", dict(bundle.start_doc))]
        published: list[str] = []
        if partial:
            # Only part of the product was published: no product counts as published.
            docs += self._outputs(bundle, (stop or start + 60.0) - 1.0, streams=("corrected_1d",))
        elif with_outputs:
            docs += self._outputs(bundle, (stop or start + 60.0) - 1.0)
            published = [PROCESSING_PRODUCT]
        if stop is not None:
            if reason is None:
                reason = "" if exit_status == "success" else f"synthetic {exit_status}"
            stop_doc: dict[str, Any] = dict(
                bundle.compose_stop(exit_status=exit_status, reason=reason, time=stop)
            )
            stop_doc["outputs"] = published
            docs.append(("stop", stop_doc))
        for name, doc in docs:
            self._writer(name, doc)
        return str(bundle.start_doc["uid"])

    def _outputs(
        self, bundle: Any, t: float, streams: Sequence[str] = ("corrected_1d", "corrected_2d")
    ) -> list[Doc]:
        n = self.outputs.curve_points
        h, w = self.outputs.image_shape
        q = np.logspace(-2, 0, n, dtype="<f4")
        intensity = (1e3 * q**-2.5).astype("<f4")
        yy, xx = np.mgrid[0:h, 0:w]
        image = (1e5 * (np.hypot(yy - h / 2, xx - w / 2) + 1) ** -2).astype("<f4")
        path = self.processed_dir / f"{bundle.start_doc['uid']}.h5"
        with H5File(path, "w") as f:
            f["Q"] = q[None]
            f["I"] = intensity[None]
            f["I_uncertainty"] = (0.05 * intensity)[None]
            f["image"] = image[None]
        uri = f"file://localhost{path.resolve()}"
        # field: (shape, chunks, units, extra data-key metadata)
        layout: dict[str, dict[str, tuple[list[int], list[int], str, dict[str, Any]]]] = {
            "corrected_1d": {
                "Q": ([n], [1, n], "1/nm", {}),
                "I": ([n], [1, n], "1/(m sr)", {}),
                "I_uncertainty": (
                    [n],
                    [1, n],
                    "1/(m sr)",
                    {"uncertainty_of": "I", "meaning": "standard uncertainty (1 sigma)"},
                ),
            },
            "corrected_2d": {
                "image": (
                    [h, w],
                    [1, min(h, 256), min(w, 256)],
                    "1/(m sr)",
                    {
                        "dims": ["y", "x"],
                        "axes": {
                            "y": {"name": "detector row", "units": "pixel"},
                            "x": {"name": "detector column", "units": "pixel"},
                        },
                    },
                ),
            },
        }
        docs: list[Doc] = []
        for stream, fields in layout.items():
            if stream not in streams:
                continue
            data_keys = {
                k: {
                    "source": "modacor",
                    "dtype": "array",
                    "dtype_numpy": "<f4",
                    "shape": shape,
                    "units": units,
                    "external": "STREAM:",
                    **extra,
                }
                for k, (shape, _, units, extra) in fields.items()
            }
            desc = bundle.compose_descriptor(name=stream, data_keys=data_keys, time=t)
            docs.append(("descriptor", desc.descriptor_doc))
            resources = []
            for k, (_, chunks, _, _) in fields.items():
                res = bundle.compose_stream_resource(
                    mimetype="application/x-hdf5",
                    uri=uri,
                    data_key=k,
                    parameters={
                        "dataset": f"/{k}",
                        "swmr": False,
                        "join_method": "stack",
                        "chunk_shape": chunks,
                    },
                )
                docs.append(("stream_resource", res.stream_resource_doc))
                resources.append(res)
            # Contract §1.1: the event first, then one stream datum per field.
            docs.append(("event", desc.compose_event(data={}, timestamps={}, time=t, seq_num=1)))
            for res in resources:
                docs.append(
                    (
                        "stream_datum",
                        res.compose_stream_datum(
                            indices=StreamRange(start=0, stop=1),
                            seq_nums=StreamRange(start=1, stop=2),
                            descriptor=desc.descriptor_doc,
                        ),
                    )
                )
        return docs


def _uids(result: Any) -> tuple[str, ...]:
    """The run UIDs a RunEngine call returns (``call_returns_result`` is off)."""
    if not isinstance(result, tuple):
        raise TypeError(f"expected a tuple of run UIDs, got {type(result).__name__}")
    return tuple(str(uid) for uid in result)


def _retime(docs: list[Doc], start: float, stop: float) -> list[Doc]:
    """Map the captured documents' times linearly onto ``[start, stop]``."""
    t0 = next(d for n, d in docs if n == "start")["time"]
    t1 = next(d for n, d in docs if n == "stop")["time"]
    span = max(t1 - t0, 1e-9)

    def m(t: float) -> float:
        return float(start + (t - t0) / span * (stop - start))

    for name, doc in docs:
        if "time" in doc:
            doc["time"] = m(doc["time"])
        if name == "event":
            doc["timestamps"] = {k: m(v) for k, v in doc["timestamps"].items()}
        if name == "stop":
            doc["time"] = stop
    return docs


# Scenarios ------------------------------------------------------------------------------

WINDOW = (1000.0, 2000.0)
"""Window used by :func:`window_scenario`, as offsets from its reference time."""


def window_scenario(fw: FixtureWriter, t0: float) -> dict[str, Any]:
    """Edge cases for the window search of plan §5.4 (window ``t0 + WINDOW``),
    run state (§5.1) and processing selection (processing-runs.md §1.2).

    Returns a manifest: per case its UID, and as applicable whether it should
    be found in the window (``expect_in_window``), its expected status
    (``expect_status``), whether it is a complete MOUSE measurement
    (``expect_complete``), and for processing runs their parents,
    ``request_id`` and ``attempt``. ``selection`` lists, per measurement, the
    expected latest processing attempt and latest usable result.
    """
    runs: dict[str, dict[str, Any]] = {}
    status_of = {"success": "success", "fail": "failed", "abort": "aborted"}

    def acq(case: str, start: float, stop: float | None, expect: bool, **kw: Any) -> str:
        md = {
            "facility": "BAM",
            "instrument": "MOUSE",
            "sampleid": f"S-{case}",
            "proposal": "P-0000",
            "destination": f"/synthetic/{case}",
            "sample_exposure_time": 600,
        }
        md.update(kw.pop("md", {}))
        exit_status = kw.get("exit_status", "success")
        n_events = kw.get("n_events", 3)
        uid = fw.mouse_measurement(md, t0 + start, None if stop is None else t0 + stop, **kw)
        runs[case] = {
            "uid": uid,
            "expect_in_window": expect,
            "expect_status": "open" if stop is None else status_of[exit_status],
            "expect_complete": stop is not None and exit_status == "success" and n_events == 3,
        }
        return uid

    for case, a, b, exp in [
        ("before", 500, 900, False),
        ("stop_eq_from", 900, 1000, True),
        ("inside", 1500, 1600, True),
        ("spanning", 500, 2500, True),
        ("stop_eq_to", 500, 2000, True),
        ("start_eq_to", 2000, 2100, False),
        ("crosses_to", 1990, 2050, True),
    ]:
        acq(case, a, b, exp)
    # MOUSE: RE.stop() records "success" with fewer than three readings
    # (plan §3.2); an interruption records "abort"; a failure records "fail".
    acq("stopped_success", 1200, 1250, True, n_events=1, snapshot=False)
    acq("aborted", 1260, 1270, True, n_events=2, exit_status="abort")
    acq("failed", 1280, 1290, True, n_events=1, exit_status="fail")
    acq("open_inside", 1800, None, True)
    acq("open_20d", 2000 - 20 * DAY, None, True)
    acq("open_22d", 2000 - 22 * DAY, None, False)
    # A run whose stored start.uid differs from its Tiled key (e.g. edited or
    # copied metadata): written by the RunEngine, then its start metadata changed.
    uid = acq("uid_mismatch", 1310, 1320, True)
    node = fw.client[uid]
    start_doc = dict(node.metadata["start"], uid="00000000-0000-4000-8000-000000000000")
    node.update_metadata(metadata={"start": start_doc})
    runs["uid_mismatch"]["expect_start_uid"] = start_doc["uid"]
    runs["generic_inside"] = {
        "uid": fw.scalar_run(
            {"facility": "BAM", "instrument": "generic", "station": "A"}, t0 + 1100, t0 + 1900
        ),
        "expect_in_window": True,
        "expect_status": "success",
    }
    runs["no_instrument"] = {
        "uid": fw.scalar_run({"facility": "BAM"}, t0 + 1300, t0 + 1350),
        "expect_in_window": True,
        "expect_status": "success",
    }

    def proc(
        case: str,
        parents: list[str],
        start: float,
        stop: float | None,
        attempt: int = 1,
        **kw: Any,
    ) -> None:
        parent_uids = [runs[p]["uid"] for p in parents]
        request_id = f"req-{'-'.join(parents)}"
        uid = fw.processing_run(
            parent_uids,
            t0 + start,
            None if stop is None else t0 + stop,
            request_id=request_id,
            attempt=attempt,
            **kw,
        )
        exit_status = kw.get("exit_status", "success")
        runs[case] = {
            "uid": uid,
            "derived_from": parent_uids,
            "request_id": request_id,
            "attempt": attempt,
            "expect_in_window": start < WINDOW[1] and (stop is None or stop >= WINDOW[0]),
            "expect_status": "open" if stop is None else status_of[exit_status],
        }

    # "inside": failed, then succeeded, then a later retry failed.
    proc("proc_inside_failed", ["inside"], 1650, 1690, 1, exit_status="fail", with_outputs=False)
    proc("proc_inside_ok", ["inside"], 1700, 1760, 2)
    proc("proc_inside_retry_failed", ["inside"], 1800, 1820, 3, exit_status="fail",
         with_outputs=False)  # fmt: skip
    proc("proc_open", ["stop_eq_from"], 1980, None, with_outputs=False)
    proc("proc_late_for_before", ["before"], 7 * DAY, 7 * DAY + 60)
    proc(
        "proc_two_parents",
        ["spanning", "stop_eq_to"],
        1400,
        1450,
        with_outputs=False,
        reason="nothing to do: synthetic no-op",
    )
    # Two results for "crosses_to" stopping at the same time: the UID decides.
    proc("proc_tie_a", ["crosses_to"], 2060, 2100, 1)
    proc("proc_tie_b", ["crosses_to"], 2070, 2100, 2)
    proc("proc_partial", ["stop_eq_to"], 2010, 2030, 1, exit_status="fail", partial=True,
         reason="corrected_2d failed")  # fmt: skip
    tie = max(runs["proc_tie_a"]["uid"], runs["proc_tie_b"]["uid"])
    selection = {
        "inside": {
            "latest_attempt": runs["proc_inside_retry_failed"]["uid"],
            "latest_usable": runs["proc_inside_ok"]["uid"],
        },
        "crosses_to": {"latest_attempt": tie, "latest_usable": tie},
        "stop_eq_to": {"latest_attempt": runs["proc_partial"]["uid"], "latest_usable": None},
        "spanning": {"latest_attempt": runs["proc_two_parents"]["uid"], "latest_usable": None},
        "before": {
            "latest_attempt": runs["proc_late_for_before"]["uid"],
            "latest_usable": runs["proc_late_for_before"]["uid"],
        },
    }
    return {
        "t0": t0,
        "window": [t0 + WINDOW[0], t0 + WINDOW[1]],
        "runs": runs,
        "selection": selection,
    }
