"""Fill a disposable Tiled with synthetic runs.

Scenarios:

- ``window``: the edge cases of plan §5.4 around a fixed reference time
  (tests and debugging); writes a manifest if ``--manifest`` is given.
- ``demo``: a day of MOUSE-like history ending now (measurements, configuration
  and vacuum runs, processing runs including failures, an aborted run).
  Skipped if the catalogue already holds MOUSE runs. With ``--live``, keeps
  acquiring through the RunEngine: one MOUSE measurement at a time
  (``baseline``, ``snapshot``, three ``mouse_eiger_measure`` readings
  ``--interval`` seconds apart), each followed by a processing run.
  Also, if the catalogue holds no electrochemistry runs, a day of
  electrochemistry-bluesky-like runs on one station: run sheets in sequence,
  with a calibration every few hours.
- ``operando``: a second, generic instrument with a run that started three
  days ago and never closes, receiving a reading every ``--interval``
  seconds. Runs until stopped; start it as a separate process.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

from tiled.client import from_uri
from tiled.queries import Key

from .fixtures import DAY, ExitStatus, FixtureWriter, window_scenario

HOUR = 3600.0


def demo_history(fw: FixtureWriter, now: float, rng: random.Random) -> dict[str, Any]:
    t = now - DAY
    count = 0
    while t < now - 0.5 * HOUR:
        count += 1
        if count % 4 == 1:
            fw.scalar_run(
                {
                    "facility": "BAM",
                    "instrument": "MOUSE",
                    "plan_name": "apply_config",
                    "config_id": 100 + count % 3,
                },
                t,
                t + 90,
                n_events=1,
            )
            t += 120
        duration = rng.uniform(8, 13) * 60
        status: ExitStatus = "abort" if count == 17 else "success"
        md = {
            "facility": "BAM",
            "instrument": "MOUSE",
            "plan_name": "measure_yzstage",
            "sampleid": f"S-{count:04d}",
            "proposal": "P-0000",
            "sample_exposure_time": 600,
        }
        uid = fw.mouse_measurement(
            md, t, t + duration, exit_status=status, n_events=1 if status == "abort" else 3
        )
        if status == "success":
            p0 = t + duration + 60
            ok = count % 8 != 5
            fw.processing_run(
                [uid],
                p0,
                p0 + rng.uniform(30, 90),
                exit_status="success" if ok else "fail",
                with_outputs=ok,
            )
        if count == 30:
            fw.scalar_run(
                {
                    "facility": "BAM",
                    "instrument": "MOUSE",
                    "plan_name": "evacuate",
                    "activity": "evacuate",
                },
                t + duration + 30,
                t + duration + 900,
            )
            t += 900
        t += duration + rng.uniform(2, 8) * 60
    return {"mouse_measurements": count}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--tiled-url", required=True)
    p.add_argument("--api-key-file", required=True, type=Path)
    p.add_argument("--processed-dir", required=True, type=Path)
    p.add_argument("--scenario", choices=["demo", "window", "operando"], default="demo")
    p.add_argument("--t0", type=float, default=1_700_000_000.0, help="reference time (window)")
    p.add_argument("--manifest", type=Path)
    p.add_argument("--live", action="store_true")
    p.add_argument("--interval", type=float, default=60.0, help="seconds between readings")
    args = p.parse_args(argv)

    client = from_uri(args.tiled_url, api_key=args.api_key_file.read_text().strip())
    fw = FixtureWriter(client, args.processed_dir)
    rng = random.Random(3)
    now = time.time()

    if args.scenario == "operando":
        operando(fw, now, args.interval)
        return
    if args.scenario == "demo":
        if len(client.search(Key("start.instrument") == ECHEM)) > 0:
            print("seed: catalogue already holds electrochemistry runs; not rewritten")
        else:
            n = electrochemistry_history(fw, now, rng)
            print(f"seed: wrote {n} electrochemistry runs")
    if args.scenario == "window":
        manifest = window_scenario(fw, args.t0)
    elif len(client.search(Key("start.instrument") == "MOUSE")) > 0:
        print("seed: catalogue already holds MOUSE runs; history not rewritten")
        manifest = {}
    else:
        manifest = demo_history(fw, now, rng)
        print(f"seed: wrote demo history ({manifest['mouse_measurements']} MOUSE measurements)")
    if args.manifest:
        args.manifest.write_text(json.dumps(manifest, indent=1))
    if args.scenario == "demo" and args.live:
        live(fw, args.interval)


ECHEM = "electrochemistry-k2460"


def electrochemistry_history(fw: FixtureWriter, now: float, rng: random.Random) -> int:
    """A day of run sheets on one station, one at a time (one sample at a
    time): each ``run_sequence`` run discharges a little further than the last,
    so the trend over them falls; a ``calibrate`` run every fifth."""
    t = now - DAY
    count = 0
    while t < now - HOUR:
        count += 1
        md = {"instrument": ECHEM, "station": "bench-a", "sample_id": "S-cell-02"}
        if count % 5 == 1:
            fw.electrochemistry_run(
                {**md, "measurement_id": f"cal-{count:03d}", "plan_name": "calibrate"},
                t, t + 300, n_events=5, level=1.0,
            )  # fmt: skip
            t += 300 + 60
        duration = rng.uniform(35, 45) * 60
        fw.electrochemistry_run(
            {**md, "measurement_id": f"sheet-{count:03d}", "plan_name": "run_sequence"},
            t, t + duration, n_events=40, level=4.1 - 0.02 * count, drop=0.3,
        )  # fmt: skip
        t += duration + rng.uniform(3, 10) * 60
    return count


def operando(fw: FixtureWriter, now: float, interval: float) -> None:
    """The generic instrument's open run. Runs until stopped."""
    print("seed: operando run open", file=sys.stderr)
    fw.operando_forever(
        {
            "facility": "BAM",
            "instrument": "generic",
            "station": "A",
            "plan_name": "operando_cycle",
            "sampleid": "S-cell-01",
        },
        now,
        backfill=int(3 * DAY / 600),
        step=600,
        interval=interval,
    )


def live(fw: FixtureWriter, interval: float) -> None:
    """Keep the demo moving. Runs until stopped."""
    n = 0
    while True:
        n += 1
        md = {
            "facility": "BAM",
            "instrument": "MOUSE",
            "plan_name": "measure_yzstage",
            "sampleid": f"S-live-{n:03d}",
            "proposal": "P-0000",
        }
        uid = fw.mouse_measurement_live(md, interval=interval)
        time.sleep(min(interval, 10))
        p0 = time.time()
        ok = bool(n % 6)
        fw.processing_run(
            [uid], p0, p0 + 5, exit_status="success" if ok else "fail", with_outputs=ok
        )
        print(f"seed: live measurement {n} done", file=sys.stderr)
