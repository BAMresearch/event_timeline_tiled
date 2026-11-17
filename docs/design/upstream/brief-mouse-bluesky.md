# Brief: MOUSE runs say which instrument wrote them

For: whoever works on `mouse_bluesky` (a person or a coding agent).
Self-contained; the change requested here is in that repository only.

## Context

event-timeline-tiled shows Bluesky runs from Tiled as a Grafana timeline,
one lane per instrument, and per run the views that suit its instrument
(for MOUSE: the readings table of `mouse_eiger_measure`, `snapshot` and
`baseline`; the baseline for configuration and vacuum runs). It reads
Tiled only, and it finds a run's instrument from three start-document keys:

| Key | Value for MOUSE | Meaning |
|---|---|---|
| `facility` | `"BAM"` | who runs the instrument |
| `instrument` | `"MOUSE"`, exactly (case matters) | selects MOUSE's lane, views, labels and 20-minute stall threshold |
| `station` | not set | only for an instrument that runs several independent set-ups at once |

This is the identity convention agreed on 2026-11-15 (event-timeline-tiled
plan, section 3.1). Without `instrument`, a run lands in a lane
"(unknown)" with generic views.

## What we found

Checked on `mouse_bluesky` at `d22bdc0` (bluesky 1.14.6):

- None of the three keys is written. The RE Manager's RunEngine is a bare
  `RunEngine({})` (`qserver_profile/profile_mouse/startup/01_re.py`) and no
  plan sets them.
- Runs are opened in several places, all through that RunEngine in queue
  operation: `measure_yzstage` (`plans/public.py`, `run_wrapper(md=run_md)`),
  the `apply_config` audit run (`plans/configure.py`, `open_run`), the
  vacuum runs (`plans/vacuum.py`) and the interactive scans
  (`interactive/`).
- `measure_yzstage` starts its metadata from the caller's `md` (logbook
  entries can contribute). In bluesky 1.14.6, `RunEngine._open_run` merges
  metadata as a `ChainMap` in which per-call metadata and the plan's
  `open_run` metadata take precedence over `RE.md`, so a stray
  `instrument` there would silently win.
- Rows already reach Tiled per event (`TiledWriter(..., batch_size=1)` in
  `02_tiledwriter.py`). Keep it that way: the timeline marks a MOUSE run
  "possibly stalled" when no event has reached Tiled for 20 minutes.

## Requested

1. Every run written by MOUSE carries `facility: "BAM"` and
   `instrument: "MOUSE"` in its start document. One place for both values
   (for example a module constant used by the start-up), set on the RE
   Manager's RunEngine with `RE.md.update(...)` in `01_re.py`, so
   measurements, configuration, vacuum and interactive runs all get them.
   If any other RunEngine writes to Tiled (an interactive or notebook
   session), set them there too, from the same place.
2. Do not set `station` (MOUSE has one set-up). If a second independent
   set-up is ever added, give each a stable `station` value then.
3. Plan metadata cannot override the identity: `measure_yzstage` and the
   other plans drop or refuse `facility`, `instrument` and `station` in a
   caller's `md` (refusing is clearer: the logbook entry is wrong).
4. Document the three keys in `design_documentation/contracts.md` as part
   of the run metadata contract.

No other start or stop keys change. The timeline also reads `sampleid` and
`plan_name` (labels), `activity` (vacuum runs), the stop document's
`exit_status`, `reason` and `num_events`, and the stream names `baseline`,
`snapshot` and `mouse_eiger_measure`; keep them as they are.

Existing runs in Tiled need no change: they stay in the fallback lane. Do
not rewrite their metadata for this.

## Tests

- A unit test with the start-up's RunEngine set-up (or the same `RE.md`):
  the start documents of a simulated measurement, an `apply_config` audit
  run and a vacuum run carry `facility: "BAM"` and `instrument: "MOUSE"`,
  and no `station`.
- A caller's `md` with `instrument` is refused (or ignored, if you choose
  that) and the start document still says `MOUSE`.
- On the instrument, after deployment: one new run read back from Tiled
  has both keys in `metadata["start"]`.

## When done

Report the commit, and whether a caller's identity keys are refused or
ignored. event-timeline-tiled then runs its operator trial on new MOUSE
runs (plan step 4).
