# Adapter endpoints

The contract between the adapter and the dashboards (plan §6.1). Fields are
added, never redefined; incompatible changes are recorded in
[migrations](migrations.md).

- Times are **epoch seconds** (UTC), except where Grafana displays them as
  times: the time fields of `/clips/frame` and the `time` column of data
  rows are **milliseconds**.
- Invalid input is refused with `422`; unknown runs, streams and views with
  `404`; a failing Tiled with `502` and a fixed category
  (`tiled unreachable: ConnectError`), never an upstream body.
- `uid` and stream names are single path segments of `A-Z a-z 0-9 _ ~ . -`.
- Windows: `from < to`, both finite, at most 400 days.
- **Presets** ([presets](presets.md)): `preset=<id>` on `/clips`, `/clips/frame`,
  `/clips/status`, the trend and `/selection/runs` decides which runs are
  shown and in which lanes; without it, the deployment's default
  (`ETT_DEFAULT_PRESET`, shipped: `instruments`). An unknown preset is `422`.
  A run's own details, streams, data, related runs, curve and image never
  depend on a preset.
- **Runs by root:** every run endpoint (`/runs/{uid}/...`, `/selection/...`)
  takes an optional `root` (as `/clips` gives it, `/` for the server root;
  not a configured root: `422`). Without it the key is looked up in every
  root; a key found under several roots is `409` (`/selection/...`: a notice),
  never a guess.

## `GET /presets`

The loaded presets, `[{"id", "title"}]`: the default first, then by title.

## `GET /health`

One row per catalogue root.

| Field | Meaning |
|---|---|
| `component`, `root` | `tiled`; the root (`/` for the server root) |
| `status`, `ok`, `latency_ms`, `detail` | live check now (one unfiltered search): `ok`, `unreachable`, `unauthorised`, `forbidden`, `not_found`, `error` |
| `last_success`, `last_attempt` | the timeline's last window fetch from this root (seconds or `null`) |
| `data_age_s` | seconds since `last_success` |
| `last_error`, `last_error_detail` | category of the last failed window fetch, `null` after a success |
| `tiled_version`, `adapter_version`, `checked_at` | |

The live check and the window fetch are separate: the check can pass while
the (filtered) window search fails. `/clips/status` is what the Timeline
page shows.

## `GET /queues`

One row per RE Manager in `ETT_QUEUES` ([queue status](queues.md)), read at
request time; `[]` if none is configured.

| Field | Meaning |
|---|---|
| `instrument`, `transport` | as configured (`zmq` or `http`) |
| `reachable` | the RE Manager answered now |
| `state` | display value: `manager_state` if reachable, else `unreachable` or `error` |
| `manager_state`, `re_state`, `environment_open`, `queue_stop_pending` | from the RE Manager's status; `null` when unreachable |
| `running_plan`, `running_since` | name of the running item, and when the RE Manager started executing it (seconds; not the run's start document, and not reset by a pause) |
| `items_in_queue`, `next_items` | queued items; names of the next three |
| `run_active`, `run_done`, `run_total`, `run_progress`, `run_text`, `run_since`, `run_from_start` | the queue's current or last run, in plans: done since it started (`run_from_start` false: since the adapter first saw it running), total with the queued and running plans, done ÷ total, `"3 of 12"` (`", stopped"` when it ended with plans left), and when the adapter saw it start ([queue status](queues.md)); `null` until a run has been seen, and while unreachable |
| `last_success`, `data_age_s`, `error`, `error_detail` | when it last answered, and why not now |

## `GET /clips?from&to[&status][&preset]`

Runs overlapping `[from, to)` that the preset places in at least one lane:
the de-duplicated union of an open-run and a closed-run search per root of
the preset (plan §5.4), narrowed by its `scope`, then checked against its
tracks. One row per run.

```json
{"from": 0, "to": 0, "preset": "instruments", "preset_revision": "…",
 "evaluated_at": 0, "clips": [...], "truncated": false, "unplaceable": [],
 "sources": [{"root": "/", "ok": true, "error": null, "last_success": 0,
              "data_age_s": 0, "truncated": false, "runs": 0,
              "activity_errors": 0, "activity_error": null}]}
```

Each clip:

| Field | Meaning |
|---|---|
| `uid` | the run's key in Tiled (use this to select the run) |
| `start_uid`, `uid_mismatch` | the start document's `uid`; `true` if it differs from the key |
| `root` | catalogue root it was found under |
| `lane_key`, `lane` | the run's first placement in the preset's track order: its lane id (`<preset>.<track>.<digest>`) and its name in this window |
| `placements` | every lane the run is in: `{preset, track, lane_id, lane, label}`, in track order (a run appears in every track it matches) |
| `role` | `acquisition` or `processing` (start `kind` is a processing kind) |
| `start`, `stop` | start and stop document times; `stop` is `null` while open |
| `status` | recorded outcome: `success`, `failed`, `aborted`, `unknown`, `open` |
| `last_event`, `last_event_age_s`, `possibly_stalled` | inferred activity, open runs only (re-read at most every `ETT_ACTIVITY_REFRESH_S`) |
| `activity_error` | the events could not be read now (`forbidden`, `unreachable`, …): `last_event` is from an earlier read, `last_event_age_s` is `null` and `possibly_stalled` is `false` |
| `stale` | the root could not be fetched now; this is the run as last observed |
| `observed_at` | when this run was last fetched successfully |
| `label` | from the view table's label keys, else the short UID (a track's own label template is in `placements`) |
| `reason` | the stop document's `reason`, if any |

`status=failed,aborted` keeps only runs with those statuses.

`sources` has one row per root for this request: `ok` is whether its window
search succeeded now; `truncated` is `true` when it matched more than
`ETT_MAX_RUNS` runs and `null` when unknown (failed now). Above the cap the
open runs are kept first, then closed runs in Tiled's order (earliest
written). `truncated` (top level) is true if any root was truncated.
`unplaceable` lists runs without a usable start time. `evaluated_at` is the
one time relative predicates were evaluated against; `preset_revision`
changes whenever a preset file does.

The two searches are not one snapshot of the catalogue. The open search goes
first, so a run closing between them is found closed; other concurrent
changes appear at the next refresh.

## `GET /clips/status?from&to[&preset][&unfold]`

Whether the window's data is complete, as the preset draws it: what the
Timeline page's *Data status* panel shows.

```json
{"preset": "instruments", "preset_revision": "…", "evaluated_at": 0,
 "matched_runs": 0, "out_of_scope": 0, "placements": 0, "lanes": 0, "rows": 0,
 "lanes_cut": 0, "placements_cut": 0, "rows_cut": 0, "streams_unknown": 0,
 "invalid_groups": 0, "unfold": null,
 "state": "complete", "complete": true, "runs": 0, "truncated": false,
 "unplaceable": [], "open_run_horizon_days": 21, "max_runs": 10000,
 "sources": [...], "issues": [{"state": "complete", "root": "", "message": "..."}]}
```

`state` is the worst of the issues: `incomplete` (a root never fetched, or
truncated), `stale` (a root failed now; its runs are shown as last seen),
`partial` (activity of some open runs unknown; runs without a usable start
time; runs left out of tracks that need streams not known yet; list or
mapping group values), else `complete`. Going over a lane, placement or row
budget (`ETT_MAX_LANES`, `ETT_MAX_PLACEMENTS`, `ETT_MAX_SUBLANES`, or the
preset's `lanes.max_lanes`) is `incomplete`: the excess is not drawn, and
says so. `runs` counts runs in the window (each once), `matched_runs` those
the preset places, `placements` and `lanes` what is drawn. With `unfold`,
`unfold` is `{"lane", "requested", "found", "runs_unfolded", "runs_folded",
"streams": {"drawn", "empty", "pending", "invalid", "unreadable", "refused"},
"stale", "notes"}`, and runs left folded, streams not drawn and stale extents
are `partial` (or `stale`) issues: about what is drawn, never a run's status. `issues` has one row per problem, worst first, or
one `complete` row. `sources` is as in `/clips`.

## `GET /clips/frame?from&to[&min_lanes][&preset][&unfold]`

The State timeline frame (plan §8), a list of rows in the preset's lane
order, one per placement (a run in two tracks is in two rows):

| Field | Meaning |
|---|---|
| `lane` | final name, unique per row: a lane's name from its preset (see [presets](presets.md)), sub-lanes with ` /2`, ` /3`, and a name already taken with ` (2)` |
| `start`, `stop` | display extent, **ms**; open runs end at their `observed_at`; a closed run without a usable stop time is drawn at its start; very short clips are widened to 0.2 % of the window, but never over the next clip in their sub-lane |
| `state` | the visible value: `status`, except `stale`, `activity unknown` or `possibly stalled` for open runs |
| `status`, `possibly_stalled`, `stale`, `activity_error` | kept separate, as in `/clips` |
| `uid`, `label` | hidden helpers for links and tooltips |
| `clip_from_ms`, `clip_to_ms` | true extent (open runs: up to `observed_at`) |
| `zoom_from_ms`, `zoom_to_ms` | the true extent with 5 % (at least 5 s) on either side, for "Zoom to run": never an empty range |
| `lane_id`, `track`, `root` | hidden helpers: which placement (lane ids are stable, names may not be) |
| `stream`, `stream_from_ms`, `stream_to_ms` | a stream bar of an unfolded lane: the stream and its true extent (earliest to latest event time); `null` on other rows |

**`unfold=<lane id>`** (empty: none) draws that lane per stream
([presets](../design/presets.md) §6.5): its latest runs (at most
`ETT_UNFOLD_MAX_RUNS`, read within `ETT_UNFOLD_TIME_S`) become one bar per
stream with events, on rows `<lane> · <stream>` in stream-name order, each
from the stream's earliest to latest event time, also for open runs (never
extended to now; a single event is widened for visibility only). Its other
runs, and runs with nothing to draw, stay on one row `<lane> · earlier runs`.
`label` gets ` · <stream>`; `state`, `status`, `clip_*` and `zoom_*` stay the
run's. Other lanes are unchanged. A lane not in the range is a notice in the
status, not an error. Only each stream's `time` column is read (Tiled cannot
select rows): a closed run's declared count refuses a stream over
`ETT_UNFOLD_MAX_EVENTS` before reading, an open stream's transfer stops
there, and at most `ETT_UNFOLD_MAX_STREAMS` streams per run are read.
Extents of finished runs are kept once read completely; open runs are
re-read at most every `ETT_ACTIVITY_REFRESH_S`.

With `min_lanes` (0–20; default: the preset's `lanes.min_lanes`) and fewer
rows than that (sub-lanes included), empty rows are added after the real ones: one row each, `lane`
one or more non-breaking spaces (invisible, unique, never a real lane's
name), `start` = `stop` = `from` in ms, every other field `null`. A panel of
fixed height then never draws a single lane as one huge bar.

The frame says nothing about completeness; that is `/clips/status`.

## `GET /runs/{uid}`

```json
{"run": {...}, "summary": [{"key": "...", "value": ...}],
 "metadata": [{"document": "start", "key": "a.b", "value": ...}], "metadata_truncated": false}
```

`run` holds `uid`, `start_uid`, `uid_mismatch`, `root`, `lane`, `role`,
`status`, `reason`, `label`, `plan_name`, `start`, `stop`, `duration_s`,
`last_event`, `last_event_age_s`, `possibly_stalled`, `activity_error`;
`summary` is the same as key/value rows.

`metadata` flattens the start and stop documents with dotted keys, up to 8
levels; deeper values are JSON text. Lists of scalars are joined with `, `
(strings as they are, other values as JSON: `true`, `null`); other lists are
JSON text. Non-finite numbers become `null` everywhere. At most 2000 rows
and 2000 characters per value (cut values end in `…`, values nested deeper
than JSON text can carry are `…`); `metadata_truncated` says whether
anything was cut, and then the last row is `{"document": "", "key": "(cut)",
"value": "Some values were cut: …"}`, so a table shows it. Values are plain
text: Grafana's tables show markup as text.

## `GET /runs/{uid}/streams`

`stream`, `events` (from `stop.num_events` when closed, else counted),
`events_source`, `first_event`, `last_event`, `fields`.

A stream without a table yet has no rows: `TiledWriter` creates a stream's
table with its first batch of rows (or at the stop document), so a running
run's streams often have none. That is `events: 0` and no event times, and
for activity no last event (the age counts from the start), never an
error. Data views of an open run without rows say "(none in Tiled yet)" in
their `notice`.

## `GET /runs/{uid}/streams/{stream}/fields`

`field`, `dtype`, `dtype_numpy`, `shape` (JSON text), `units`, `hinted`,
`external`.

## `GET /runs/{uid}/views`

The views the [view table](../design/plan.md#7-views-per-instrument) gives this
run, the default first:

```json
{"default": "readings", "views": [{"name": "readings", "title": "Readings", "kind": "table",
  "streams": [...], "fields": [...], "units": {...},
  "columns": [{"stream": "...", "field": "...", "units": "..."}],
  "available": true, "reason": "", "default": true}]}
```

`columns` is what a table view reads, per stream: a field that occurs in
several streams appears once per stream, with that stream's units. `fields`
lists each field name once; `units` gives each field the units of the first
stream that has it.

Kinds: `table`, `timeseries`, `summary`, `curve`, `image`. View names in
version 1: `readings`, `baseline` (MOUSE), `hinted`, `numeric`, `events`,
`no_data` (fallback), `summary` (all runs), `corrected_curve`,
`corrected_image` (MOUSE processing runs). `hinted` and `numeric` always mean a time
series of one stream's hinted numeric scalars, or all its numeric scalars:
the fallback's stream, or the stream an instrument's entry names (MOUSE
measurements: `mouse_eiger_measure`, after the default `readings`).

A `curve` view reads 1D external arrays of one stream: `fields` are x, y
and, if the stream declares one (a data key with `uncertainty_of` naming y),
the uncertainty of y. An `image` view reads one 2D external array. Both read
result 0 of a processing run's arrays (processing-runs.md §1.1). MOUSE
processing runs: `corrected_curve` (`corrected_1d`: `Q`, `I`) is the
default, then `corrected_image` (`corrected_2d`: `image`). A processing run
that published neither gets the fallback views.

## `GET /runs/{uid}/data?view|kind&max_points&axis`

Rows of one view (default view if `view` is omitted). Every response has a
`notice`: one line saying what is shown (and what is not).

- `table`: `{"rows": [{"stream", "seq_num", "time": <ms>, "field", "value", "units"}], "truncated", "row_limit"}`,
  stream by stream in view order, each with its own fields and units
  (`columns` of the view). At most 2000 rows; `truncated` is true only if
  there are more. Reading stops at the first row beyond the limit.
- `timeseries`: one stream, long format,
  `{"stream", "axis", "fields", "units", "rows": [{"time": <ms>, "field", "series", "value"}],
  "truncated", "decimation": {"method", "every", "source_rows", "events", "returned", "max_points"}, "non_finite"}`.
  `series` is the field with its units (`voltage_v [V]`), for legends.
  `axis=1` returns only the fields in the units of the view's first field,
  `axis=2` all others (other units or none), for a second y axis; without
  `axis`, all fields. Ignored by other kinds.
  `max_points` (10–20000, default 2000) bounds the **rows** returned, over
  all fields: each field gets `max_points // fields` events, every k-th from
  the first and always the last. With more fields than `max_points`, only
  the first `max_points` fields are returned and `truncated` is true.
  Infinity's `timeseries` query format turns the rows into one series per
  field with a real time field (its auto-detection types a millisecond or
  ISO time as a number or string, which Grafana's time-series panel cannot
  use). All rows are read from Tiled (it cannot select rows), then decimated
  here.
- `curve`: the rows of `/runs/{uid}/curve` for this run (never followed to
  another run).
- `image`: no rows; the `notice` points to the image panel.
- `kind=table|timeseries|summary|curve|image` instead of `view`: the first available
  view of that kind, or no rows and a `reason` if the run has none.
- `summary`: the metadata rows of `/runs/{uid}`, `truncated` as
  `metadata_truncated`.
- An unavailable view returns no rows and its `reason`.

Non-finite numbers are returned as `null`. A view's rows are reused for 5
seconds (the dashboard reads the default view twice per refresh).

## `GET /runs/{uid}/trend?from&to&max_points&axis[&preset][&lane]`

The run's time series view (the first available `timeseries` view, as
`/runs/{uid}/data?kind=timeseries`) over **the runs like it** in
`[from, to)` (epoch seconds, as `/clips`): the runs of the preset's lane
`lane` (a lane id from the frame or `/clips`; without it, the run's first
lane in the preset's track order, said in the notice), with the same role
and the same start-document `plan_name` (both missing counts as the same),
whose views include the same view of the same stream. A lane not in the
preset's range, or a run the preset places nowhere, gives no runs and says
so. The run
itself is one of them if it is in the window. One series per field, all
runs joined; between two runs, one row per field with `value: null`
halfway between them, so the line breaks there.

`{"view", "kind", "stream", "axis", "fields", "units", "plan_name", "preset",
"lane", "lane_name", "rows": [{"time": <ms>, "field", "series", "value"}], "truncated",
"runs": {"like", "read", "plotted", "without_view", "unreadable",
"lacking_fields", "max_runs"}, "decimation": {...}, "non_finite", "notice"}`

- `fields`, `units`, `series` and `axis` are those of the selected run's
  view, as in `/runs/{uid}/data`. A field missing from another run, or in
  other units there, is `null` for that run (never mixed in);
  `lacking_fields` counts such runs.
- At most the latest 200 runs like it (`max_runs`) are read; `like` counts
  all of them. A run whose streams or table cannot be read is left out and
  counted in `unreadable`; one without the view, in `without_view`.
- `max_points` (10–20000, default 2000) bounds the rows returned, gap rows
  included: every k-th event over all runs together, always the last. At
  most `max_points // 3` fields.
- `truncated` is true if runs were left out by `max_runs` or by the
  window's `ETT_MAX_RUNS`, or fields by `max_points`. The `notice` says
  which, and how many runs were left out for any reason.
- No `timeseries` view: no rows, `view: null` and a `reason`.
- Finished runs are read once and kept, per run, view and the selected
  run's units of the fields (up to two million values and 5000 runs, oldest
  use evicted first; a run with more than two million values is never
  kept); open runs are read on every request. Runs are read four at a
  time; a run that fails is retried once on its own. That retry does not
  make DuckDB table storage reliable: Tiled 0.2.21 serves DuckDB through one
  shared connection, and reads made at the same time by other requests
  still fail; such runs are counted in `unreadable` (review 06).
- The trend's cost grows with the events of the runs read, not with
  `max_points`: every chosen run's columns are read whole (Tiled cannot
  select rows). Bounding that work is open (review 06, finding 9).

Runs that overlap in time (several sub-lanes) are joined in time order, so
their lines interleave.

## `GET /runs/{uid}/related`

For a **measurement**: its processing runs (start `kind` a processing kind
and its UID in `start.derived_from`), under every catalogue root, whatever
their times. For a **processing run**: the measurements in its
`derived_from`.

`{"role", "rows": [...], "truncated", "default", "notice"}`, one row per run:

| Field | Meaning |
|---|---|
| `relation` | `processing` or `measurement` |
| `uid`, `label`, `start`, `stop`, `status`, `reason` | as in `/clips` (`label` `(not in Tiled)` for a listed measurement that is not found) |
| `family` | `product · pipeline.name` (processing runs) |
| `attempt` | the start document's `attempt` |
| `usable` | `exit_status` `success`, the product in the stop document's `outputs`, and no `problems` |
| `problems` | how a processing run breaks the contract, `; `-separated (unsupported or missing `processing_schema_version`, missing product, pipeline name, start UID or time, attempt, `request_id`, stop time, `outputs`, an unknown `exit_status`, a `derived_from` that is not a list of UIDs), or `null` |
| `latest_attempt`, `latest_usable` | per family: the latest finished attempt, and the latest usable result (by stop time; ties by the greater start UID as text). Open attempts, and runs with `problems`, are never latest |
| `mark` | the same as text, for tables: `latest usable`, `latest attempt`, `latest usable · latest attempt`, or `null` |

`default` is the latest usable result over all families (`null` if none),
which `/curve` and `/image` show for a measurement. Rows are by family (the
family with the latest usable result first), then by start time. Families
are told apart by `product` and `pipeline.name` themselves; `family` is
their display label. At most 500 processing runs per root: with more
(`truncated`), the latest one may not be among them, so nothing is marked
latest, `default` is `null`, rows are unranked, and the `notice` says so.
Never cached.

The search is exact (decision D-008): `Like` on the quoted UID for UIDs of
letters, digits, `.`, `~` and `-`; otherwise a scan of up to 10,000 runs
with `start.derived_from`, `truncated` beyond that.

For a processing run, the `notice` also names its `problems`.

## `GET /runs/{uid}/curve?view&max_points`

The run's first available `curve` view (or the one named `view`). For a
**measurement**, the curve of its latest usable processing result
(`default` of `/related`); for a processing run, its own, also when it is not
usable (said in the `notice`).

`{"view", "kind": "curve", "source": {"uid", "label", "family", "usable", "followed"},
"x", "y", "uncertainty", "units", "rows": [{"x", "y", "y_lower", "y_upper"}],
"truncated", "points", "plotted", "decimation", "non_finite", "non_positive",
"lower_bound_dropped", "uncertainty_invalid", "description", "notice"}`

The `notice` names the result as `label (family, first 8 characters of the
UID)`.

- For log–log axes: points with a non-finite or non-positive x or y are left
  out and counted (`non_finite`, `non_positive`).
- `y_lower`, `y_upper`: y ∓ the uncertainty; `null` without one; both
  `null` where the uncertainty is negative or not finite
  (`uncertainty_invalid`); `y_lower` `null` where it is not positive, and
  either `null` where it is not finite (`lower_bound_dropped`).
- `max_points` (10–20000, default 2000): every k-th point
  (`decimation` k, `truncated` if k > 1).
- Without a curve: no rows, `view` and `source` `null`, and the `notice`
  says why (no processing run, no usable result and the latest attempt's
  status, a processing run without the output, more processing runs than
  `/related` can rank).
- A result whose arrays Tiled cannot serve (a registered file that is gone
  gives `HTTP 500` in Tiled 0.2.21): no rows, and a `notice` that it could
  not be read.
- Curves of finished processing runs are kept (32 most recent).

## `GET /runs/{uid}/image?view&scale&size`

The run's first available `image` view as a PNG, followed from a measurement
as `/curve`. Also served as `/run-images/{uid}/image`, the path the proxy
forwards for signed-in browsers (plan §6).

- `scale`: `log` (default) or `linear`. On a log scale, zero, negative and
  non-finite values are transparent; on a linear one, non-finite values.
- Colours (viridis) span the 1st to the 99.5th percentile of the coloured
  values; a constant image is drawn in the middle colour.
- `size` (64–2048, default 768): the longer side at most, by block means of
  finite values. Arrays are strided on the server first to at most
  `min(2 × size, 2048)` pixels per side, so a render never reads more than
  about four million values; at most two images render at the same time.
  Row 0 is the top row.
- Other parameters are ignored: the dashboard adds `t=${__to}` so that each
  refresh asks again and a newer result replaces the old one.
- `404` with a `detail` if there is no image (the reasons of `/curve`).
- Caching: a finished processing run's own image `private, max-age=86400`;
  a measurement's (its result can be superseded) `private, no-cache`; both
  with an `ETag` (`If-None-Match` gives `304`). Open runs `no-store`.

## `GET /selection/...`

What the Timeline dashboard reads for the selected run. The same reads as
`/runs/{uid}/...`, with the run and stream as query parameters that may be
**empty**: nothing selected is the dashboard's normal initial state, not an
error. Nor is a selected run this Tiled does not have (a bookmark, a shared
link, a run from another server): it gets a message, not `404`.
`/runs/{uid}` still answers `404` for it.

| Endpoint | With a selection | With `uid=` empty |
|---|---|---|
| `/selection/run?uid` | `{"selected": true, "found": true, "run", "summary", "metadata", "metadata_truncated"}` as `/runs/{uid}` | `{"selected": false, "found": false, "summary": [{"key": "run", "value": "Select a run …"}], "metadata": [], "metadata_truncated": false}` |
| `/selection/streams?uid` | as `/runs/{uid}/streams` | `[]` |
| `/selection/fields?uid&stream` | as `/runs/{uid}/streams/{stream}/fields` | `[]` (also with `stream=` empty) |
| `/selection/data?uid&view|kind&max_points&axis` | as `/runs/{uid}/data` | no rows, `notice` "Select a run …" |
| `/selection/trend?uid&from&to&max_points&axis` | as `/runs/{uid}/trend` | no rows, `notice` "Select a run …" |
| `/selection/related?uid` | as `/runs/{uid}/related` | no rows, `role` and `default` `null`, `notice` "Select a run …" |
| `/selection/curve?uid&view&max_points` | as `/runs/{uid}/curve` | no rows, `notice` "Select a run …" |
| `/selection/runs?from&to&preset&lane&uid&limit` | the latest runs on `lane`, each once, newest first: `{"preset", "lane", "lane_name", "rows": [{"uid", "root", "label", "start", "stop", "status", "plan_name", "lane_id", "lane"}], "truncated", "notice"}`; with `uid` and no `lane`, the run's first lane | the preset's runs in the window, each once, newest first |

Every `/selection/...` endpoint also takes `root` (see the top of this page).
`/selection/runs`: `limit` 1–500 (default 50); a `lane` not in the preset's
range gives no rows and a notice, never another lane.

With a `uid` that is not found: `/selection/run` gives `{"selected": true,
"found": false, "summary": [{"key": "run", "value": "The selected run is not
in Tiled …"}], "metadata": [], "metadata_truncated": false}`, `/selection/data`, `/selection/trend`, `/selection/related` and `/selection/curve` no rows with that `notice`, and
`/selection/streams` and `/selection/fields` `[]`.
