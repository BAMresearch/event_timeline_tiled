# Adapter endpoints

The contract between the adapter and the dashboards (plan §6.1). Fields are
added, never redefined; incompatible changes are recorded in
[migrations](migrations.md).

- Times are **epoch seconds** (UTC), except the Grafana time fields of
  `/clips/frame` and the `time` column of time-series data, which are
  **milliseconds**.
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
| `status`, `ok`, `latency_ms`, `detail` | live check now: `ok`, `unreachable`, `unauthorised`, `forbidden`, `not_found`, `error` |
| `last_success`, `last_attempt` | the timeline's last window fetch from this root (seconds or `null`) |
| `data_age_s` | seconds since `last_success` |
| `last_error`, `last_error_detail` | category of the last failed window fetch, `null` after a success |
| `tiled_version`, `adapter_version`, `checked_at` | |

## `GET /clips?from&to`

Runs overlapping `[from, to)`: the de-duplicated union of a closed-run and an
open-run search per root (plan §5.4).

```json
{"from": 0, "to": 0, "clips": [...], "truncated": false, "unplaceable": [],
 "sources": [{"root": "/", "ok": true, "error": null, "last_success": 0}]}
```

Each clip:

| Field | Meaning |
|---|---|
| `uid` | the run's key in Tiled (use this to select the run) |
| `start_uid`, `uid_mismatch` | the start document's `uid`; `true` if it differs from the key |
| `root` | catalogue root it was found under |
| `lane_key`, `lane` | `facility/instrument/station/role` (`-` for missing parts); display name |
| `role` | `acquisition` or `processing` (start `kind` is a processing kind) |
| `start`, `stop` | start and stop document times; `stop` is `null` while open |
| `status` | recorded outcome: `success`, `failed`, `aborted`, `unknown`, `open` |
| `last_event`, `last_event_age_s`, `possibly_stalled` | inferred activity, open runs only (re-read at most every `ETT_ACTIVITY_REFRESH_S`) |
| `stale` | the root could not be fetched; this is the last good data |
| `label` | from the view table's label keys, else the short UID |

`truncated` is true when a root held more than `ETT_MAX_RUNS` runs in the
window. `unplaceable` lists runs without a usable start time.

## `GET /clips/frame?from&to`

The State timeline frame (plan §8), a list of rows in lane order:

| Field | Meaning |
|---|---|
| `lane` | final lane, with sub-lane suffix (` /2`, ` /3`) for overlaps |
| `start`, `stop` | display extent, **ms**; open runs end at the last successful fetch; very short clips are widened to 0.2 % of the window |
| `state` | the visible value: `status`, except `possibly stalled` or `stale` for open runs |
| `status`, `possibly_stalled`, `stale` | kept separate, as in `/clips` |
| `uid`, `label` | hidden helpers for links and tooltips |
| `clip_from_ms`, `clip_to_ms` | true extent, for "Zoom to run" |

## `GET /runs/{uid}`

```json
{"run": {...}, "summary": [{"key": "...", "value": ...}], "metadata": [{"document": "start", "key": "a.b", "value": ...}]}
```

`run` holds `uid`, `start_uid`, `uid_mismatch`, `root`, `lane`, `role`,
`status`, `reason`, `label`, `plan_name`, `start`, `stop`, `duration_s`,
`last_event`, `last_event_age_s`, `possibly_stalled`; `summary` is the same
as key/value rows. `metadata` flattens the start and stop documents with
dotted keys; lists of scalars are joined with `, `, other lists are JSON text.

## `GET /runs/{uid}/streams`

`stream`, `events` (from `stop.num_events` when closed, else counted),
`events_source`, `first_event`, `last_event`, `fields`.

## `GET /runs/{uid}/streams/{stream}/fields`

`field`, `dtype`, `dtype_numpy`, `shape` (JSON text), `units`, `hinted`,
`external`.

## `GET /runs/{uid}/views`

The views the [view table](../design/plan.md#7-views-per-instrument) gives this
run, the default first:

```json
{"default": "readings", "views": [{"name": "readings", "title": "Readings", "kind": "table",
  "streams": [...], "fields": [...], "units": {...}, "available": true, "reason": "", "default": true}]}
```

Kinds: `table`, `timeseries`, `summary`. View names in version 1:
`readings`, `baseline` (MOUSE), `hinted`, `numeric`, `events`, `no_data`
(fallback), `summary` (all runs).

## `GET /runs/{uid}/data?view&max_points`

Rows of one view (default view if `view` is omitted); `max_points` 10–20000,
default 2000.

- `table`: `{"rows": [{"stream", "seq_num", "time", "field", "value", "units"}], "truncated", "row_limit"}`,
  at most 2000 rows.
- `timeseries`: one stream, `{"stream", "fields", "units", "rows": [{"time": <ms>, "<field>": value}],
  "decimation": {"method", "every", "source_rows", "returned"}, "non_finite"}`. Decimation keeps
  every k-th event and always the last; all rows are read from Tiled (it
  cannot select rows), then decimated here.
- `summary`: the metadata rows of `/runs/{uid}`.
- An unavailable view returns no rows and its `reason`.

Non-finite numbers are returned as `null`.
