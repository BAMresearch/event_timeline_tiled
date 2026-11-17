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
| `last_success`, `data_age_s`, `error`, `error_detail` | when it last answered, and why not now |

## `GET /clips?from&to[&status]`

Runs overlapping `[from, to)`: the de-duplicated union of an open-run and a
closed-run search per root (plan §5.4).

```json
{"from": 0, "to": 0, "clips": [...], "truncated": false, "unplaceable": [],
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
| `lane_key`, `lane` | `facility/instrument/station/role`, each part percent-encoded and `-` for a missing one (a literal `-` is `%2D`); display name, unique per lane key |
| `role` | `acquisition` or `processing` (start `kind` is a processing kind) |
| `start`, `stop` | start and stop document times; `stop` is `null` while open |
| `status` | recorded outcome: `success`, `failed`, `aborted`, `unknown`, `open` |
| `last_event`, `last_event_age_s`, `possibly_stalled` | inferred activity, open runs only (re-read at most every `ETT_ACTIVITY_REFRESH_S`) |
| `activity_error` | the events could not be read now (`forbidden`, `unreachable`, …): `last_event` is from an earlier read, `last_event_age_s` is `null` and `possibly_stalled` is `false` |
| `stale` | the root could not be fetched now; this is the run as last observed |
| `observed_at` | when this run was last fetched successfully |
| `label` | from the view table's label keys, else the short UID |
| `reason` | the stop document's `reason`, if any |

`status=failed,aborted` keeps only runs with those statuses.

`sources` has one row per root for this request: `ok` is whether its window
search succeeded now; `truncated` is `true` when it matched more than
`ETT_MAX_RUNS` runs and `null` when unknown (failed now). Above the cap the
open runs are kept first, then closed runs in Tiled's order (earliest
written). `truncated` (top level) is true if any root was truncated.
`unplaceable` lists runs without a usable start time.

The two searches are not one snapshot of the catalogue. The open search goes
first, so a run closing between them is found closed; other concurrent
changes appear at the next refresh.

## `GET /clips/status?from&to`

Whether the window's data is complete: what the Timeline page's *Data
status* panel shows.

```json
{"state": "complete", "complete": true, "runs": 0, "truncated": false,
 "unplaceable": [], "open_run_horizon_days": 21, "max_runs": 10000,
 "sources": [...], "issues": [{"state": "complete", "root": "", "message": "..."}]}
```

`state` is the worst of the issues: `incomplete` (a root never fetched, or
truncated), `stale` (a root failed now; its runs are shown as last seen),
`partial` (activity of some open runs unknown; runs without a usable start
time), else `complete`. `issues` has one row per problem, worst first, or
one `complete` row. `sources` is as in `/clips`.

## `GET /clips/frame?from&to`

The State timeline frame (plan §8), a list of rows in lane order:

| Field | Meaning |
|---|---|
| `lane` | final lane, unique per lane and sub-lane: sub-lanes get ` /2`, ` /3`; a name already taken gets ` (2)` |
| `start`, `stop` | display extent, **ms**; open runs end at their `observed_at`; a closed run without a usable stop time is drawn at its start; very short clips are widened to 0.2 % of the window, but never over the next clip in their sub-lane |
| `state` | the visible value: `status`, except `stale`, `activity unknown` or `possibly stalled` for open runs |
| `status`, `possibly_stalled`, `stale`, `activity_error` | kept separate, as in `/clips` |
| `uid`, `label` | hidden helpers for links and tooltips |
| `clip_from_ms`, `clip_to_ms` | true extent (open runs: up to `observed_at`) |
| `zoom_from_ms`, `zoom_to_ms` | the true extent with 5 % (at least 5 s) on either side, for "Zoom to run": never an empty range |

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
and 2000 characters per value (cut values end in `…`);
`metadata_truncated` says whether anything was cut. Values are plain text:
Grafana's tables show markup as text.

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

Kinds: `table`, `timeseries`, `summary`. View names in version 1:
`readings`, `baseline` (MOUSE), `hinted`, `numeric`, `events`, `no_data`
(fallback), `summary` (all runs).

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
- `kind=table|timeseries|summary` instead of `view`: the first available
  view of that kind, or no rows and a `reason` if the run has none.
- `summary`: the metadata rows of `/runs/{uid}`, `truncated` as
  `metadata_truncated`.
- An unavailable view returns no rows and its `reason`.

Non-finite numbers are returned as `null`. A view's rows are reused for 5
seconds (the dashboard reads the default view twice per refresh).

## `GET /selection/...`

What the Timeline dashboard reads for the selected run. The same reads as
`/runs/{uid}/...`, with the run and stream as query parameters that may be
**empty**: nothing selected is the dashboard's normal initial state, not an
error. Nor is a selected run this Tiled does not have (a bookmark, a shared
link, a run from another server): it gets a message, not `404`.
`/runs/{uid}` still answers `404` for it.

| Endpoint | With a selection | With `uid=` empty |
|---|---|---|
| `/selection/run?uid` | `{"selected": true, "found": true, "run", "summary"}` as `/runs/{uid}` (no metadata) | `{"selected": false, "found": false, "summary": [{"key": "run", "value": "Select a run …"}]}` |
| `/selection/streams?uid` | as `/runs/{uid}/streams` | `[]` |
| `/selection/fields?uid&stream` | as `/runs/{uid}/streams/{stream}/fields` | `[]` (also with `stream=` empty) |
| `/selection/data?uid&view|kind&max_points&axis` | as `/runs/{uid}/data` | no rows, `notice` "Select a run …" |

With a `uid` that is not found: `/selection/run` gives `{"selected": true,
"found": false, "summary": [{"key": "run", "value": "The selected run is not
in Tiled …"}]}`, `/selection/data` no rows with that `notice`, and
`/selection/streams` and `/selection/fields` `[]`.
