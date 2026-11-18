# User guide

The dashboards are for operators following the queue: which runs are
recorded on which instrument, and with what status. The timeline shows
**recorded runs**: what has reached Tiled. The *Queue* panel shows what each
RE Manager reports right now, which covers what Tiled cannot: a paused queue,
or a plan that failed before it opened a run.

Sign in to Grafana with your Viewer account and open **Overview** or
**Timeline** (each links to the other at the top, keeping the time range,
the preset and the selection). *Overview* goes from the general at the top to
the selected run further down; *Timeline* is the full view, with every
panel.

## Overview

From top to bottom:

- **A narrow top row:** *Queue* and *Data status*, as on the Timeline (below).
- **Timeline**, in the preset's lanes, with *Select run*, *Zoom to run*,
  *Unfold lane into streams* and *Fold* on every block.
- **Trend** of the selected run and the runs like it on the lane you
  clicked (the line above names the lane).
- **The selected run**, in three columns: the latest runs on its lane
  (before a selection: the preset's runs in the range; click one to select
  it), its streams, and its state (status, duration, reason, activity) above
  its start and stop documents.
- **Its curves:** the time series of its events beside its corrected curve
  (for a measurement, its latest usable processing result's). Each says in
  the line above it when there is nothing to draw.

Failure reasons in a table, the corrected image, related runs, the readings
table and stream fields are on the Timeline.

## Timeline

From top to bottom, in sections you can collapse by clicking their title
(the next reload opens them all again):

- **Top, always shown:** *Queue*, per instrument: the RE Manager's state
  (`idle`, `executing_queue`, `paused`, or `unreachable` when it does not
  answer), the running plan, since when it runs, the number of queued
  items, and the progress of the current run of the queue in plans ("1 of
  2": plans finished since the queue started, out of those plus the ones
  still queued; the long history before it does not count); and *Data
  status* (below).
- **Runs and the selected run.** On the left, *Runs in range* (newest
  first), *Failed and aborted* with the recorded reason, and for the
  selected run its details and streams. On the right, the selected run's
  data in its default view (for MOUSE measurements: the readings of
  `mouse_eiger_measure`, `snapshot` and `baseline`; for electrochemistry
  runs: voltage and current), and a time series when the run has one.
  Fields in other units than the first are drawn against a second axis on
  the right; the legend gives each field's units. The line above the data
  says which view it is and whether rows were left out (a table shows at
  most 2000 rows).
- **Timeline.** The lanes come from the **preset** chosen at the top left.
  The default, *Instruments*, has one lane per instrument (and station) and
  role: `MOUSE · acquisition`, with `MOUSE · processing` right below it.
  Other presets can group runs differently (by plan, by sample, failures
  apart); a run can then appear in several lanes. Each block is one run, from
  its start to its stop. Overlapping runs on one lane are spread over
  sub-lanes (`MOUSE · acquisition /2`). Very short runs are drawn a little
  wider than they are, so they stay visible; their real times are in the run
  details. With few lanes, the rest of the panel stays empty.
- **Unfolding a lane.** Click a block and choose *Unfold lane into streams*:
  that lane is drawn once per stream, `MOUSE · acquisition · baseline`,
  `… · mouse_eiger_measure`, and so on, each bar from the stream's first to
  its last event in that run (a baseline read at the start and the end of a
  measurement spans the whole run; it does not mean it was read
  throughout). Runs beyond the latest 50, and runs with nothing to draw,
  stay on a `· earlier runs` row; *Data status* says how many. *Fold* (on a
  block, or at the top) goes back. One lane is unfolded at a time.
- **Corrected data.** The corrected result of the selected run: for a
  measurement, its latest usable processing result (successful, with its
  product published); for a processing run, its own, with a note if it is
  not usable. *Corrected curve* plots it on log–log axes, with dashed
  bounds y − σ and y + σ where the result gives an uncertainty; the line
  above it names the fields, their units, and points left out (zero or
  negative values cannot be drawn on log axes). *Corrected image* shows the
  2D result on a log scale; zero and negative values are transparent. It
  says "No corrected image" when there is none, and is fetched again on
  every refresh, so a newer result replaces it. *Related runs* lists a
  measurement's processing runs, marking per result family the latest
  attempt and the latest usable result, or a processing run's measurements;
  click a label to select that run. A measurement without processing says
  so; a newer attempt that failed is named in the line above the curve,
  which also names the result shown (label, family and the start of its
  UID). Runs that break the processing contract are listed with their
  problems and never shown as the latest result.
- **Trend: runs like the selected one.** The selected run's time series
  together with every run like it in the time range: the same lane, the
  same plan (`plan_name`) and the same view. Each field is one line across
  those runs, broken between runs, on the same time axis as the timeline,
  so you can follow a value through a series of measurements. The line
  above it says how many runs it covers, and which were left out (at most
  the latest 200 runs). Runs without that view (configuration runs, for
  example) are not part of it.
- **Run metadata.** Everything the selected run's start and stop documents
  record, one row per key (nested keys dotted, for example
  `versions.bluesky`): sample and configuration identifiers, plan
  parameters, the exit status and the event count per stream. Very long
  values end in `…` where they were cut; then the last row says so.
- **Stream fields, collapsed.** Click a stream in *Streams*, then open
  this section to see its fields, types, shapes and units.

**Selecting a run:** click its block on the timeline and choose *Select run*
(or *Zoom to run* to fit the time range to it), or click its label in a
table. The selection, the preset, the lane you clicked and an unfolded lane
all stay in the URL, so a link or a reload keeps them. The trend shows the
runs of the lane you clicked.
Until a run is selected, the run panels say so. If the selected run is not
in this Tiled (an old link or bookmark), *Selected run* says so; select
another.

### Data status

Read this before trusting an empty or quiet timeline. Green `complete`
means every catalogue root was read just now and nothing was left out.
Otherwise each problem has its own line:

| State | Meaning |
|---|---|
| `incomplete` (red) | a root has not been read at all yet, so its runs are missing; or more runs than the limit (`ETT_MAX_RUNS`, default 10000) are in the range, and some are not shown: narrow the time range |
| `stale` (red) | a root cannot be read now; its runs are shown as last seen (grey if open), and new runs are missing |
| `partial` (orange) | the activity of some open runs could not be read (light blue blocks), or some runs have no usable start time |

The timeline refreshes every 30 seconds by default (10 seconds can be
chosen in the refresh picker). The plan asks for 5–10 seconds; 30 stays the
default until the operator trial has measured what the lab's Tiled can
sustain.

### Colours

| Colour | Meaning |
|---|---|
| green | `success`: the run's stop document says it finished successfully |
| red | `failed` |
| orange | `aborted` |
| purple | `unknown`: a stop document with an unrecognised exit status |
| blue | `open`: no stop document yet; drawn up to the last successful read |
| yellow | `possibly stalled`: open, but no event for longer than the instrument's threshold (MOUSE 20 min, others 30 min) |
| light blue | `activity unknown`: open, but its events could not be read just now, so nothing is inferred (see *Data status*) |
| grey | `stale`: Tiled could not be read; this is the run as last seen, drawn up to then |

Green means what the run recorded, not that the measurement is complete: a
MOUSE measurement stopped with `RE.stop()` is recorded as `success` with
fewer than three readings (see *Streams*).

An open run is never called "running": Tiled cannot tell whether it is
acquiring, paused or crashed. *Possibly stalled* only says that nothing new
has arrived for a while.

Runs left open for more than 21 days before the end of the time range are
not shown (*Data status* states the horizon).

## Timeline health

Per catalogue root: whether the adapter can read Tiled now, how long that
took, whether the timeline's own last read failed (*last window fetch
error*) and how old its data is, and the Tiled and adapter versions. The
live check is a simple search; the timeline's search can still fail when
the check passes, which *last window fetch error* shows. Any status other
than `ok` means the timeline cannot be trusted:

| Status | Meaning |
|---|---|
| `unreachable` | No connection to Tiled (network, server down) |
| `unauthorised` | Tiled rejected the adapter's key |
| `forbidden` | The key is valid but may not read this root |
| `not_found` | The configured root does not exist |
| `error` | Any other answer from Tiled |
