# Plan: event-timeline-tiled (prototype)

Status: draft, revision 7 (2026-11-15). Reviewed by Codex: architecture
([response-01](reviews/response-01-architecture.md),
[disposition-01](reviews/disposition-01.md)), Tiled access
([response-02](reviews/response-02-tiled-access.md),
[disposition-02](reviews/disposition-02.md)) and Grafana
([response-03](reviews/response-03-grafana-feasibility.md),
[disposition-03](reviews/disposition-03.md)). Step 1 (spike) is done: all
three gates passed in containers and a headless browser; results and the
findings for the scaffold are in [spike-results.md](spike-results.md).

## 1. Goal and first users

A browser timeline of Bluesky runs and their event streams, read from Tiled,
laid out like a non-linear video editor (NLE):

```text
┌──────────────────────────┬───────────────────────────────────────────────┐
│ Run details / channels   │ Viewer                                        │
│ (top left)               │ (top right)                                   │
│ run summary, streams and │ MOUSE: readings table; corrected 1D curve or  │
│ channels, health         │ 2D image once MoDaCor has processed the run   │
│                          │ electrochemistry (later): 1D plots            │
├──────────────────────────┴───────────────────────────────────────────────┤
│ Timeline: lanes per instrument (and station), plus a "corrected" lane    │
│ clips = recorded runs and their MoDaCor processing runs, by status       │
└──────────────────────────────────────────────────────────────────────────┘
```

**First users are operators following the queue**: what is running where,
and with what status. Defaults: window "last 12 hours" to "now", auto-refresh
every 5–10 s, lanes by instrument, status colours readable at a glance.

**Truth boundary.** The timeline shows **recorded runs**: what has reached
Tiled. From Tiled alone it cannot show a paused or idle queue, a plan that
failed before opening a run, or a stalled ingestion worker. It says so on the
page and shows its own data freshness (section 5.3). Operators today follow
the queue only with `qserver status` on the command line; there is no other
queue display. A small read-only queue-status panel fills that gap
(section 5.5).

**Read-only.** Nothing in the running system writes to Tiled. Only tests and
the demo stack write, to disposable catalogues of their own.

**Data sources.** **MOUSE first.** Electrochemistry gets a Tiled interface
soon and must then fit without redesign. A deployment may have either or both;
with both, one central Tiled with an SQL catalogue on PostgreSQL. Lanes come
from the data, and instrument-specific choices live only in the view table
(section 7).

**Approach.** Built-in Grafana panels and published plugins first.
Customisation (presets, custom panels) only after the basic timeline works.

## 2. What carries over from bluesky-web-plotting

The Dash attempt reached a timeline figure but no plot or configuration panes.
Grafana provides most of the remaining UI plumbing.

Carried over as **decisions**, re-implemented with new tests (the old code
does not implement the overlap, open-run or health semantics this plan needs;
see disposition 3, 4, 15):

- Clip model: a run from `start.time` to `stop.time`; the status mapping and
  the invariant that an open run has no stop time (now refined in section 5).
- UID policy (DD-006): the Tiled container key identifies the run; a mismatch
  with `start.uid` is reported, not hidden.
- Server-side time filtering, always; the view window is distinct from how
  far back the viewer looks for runs that are still open.
- Source health, shown on the dashboard, but as *last successful data fetch*,
  not last attempt.
- Model → table → panel (DD-002).
- Tracks, predicates, grouping and label templates (`CONFIG_SPEC.md`), with
  strict versioned validation: in the customisation step (step 8), not before.

Changed: the shared hover crosshair across panels is the version 1
interaction. The old persistent playhead and selection outline are deferred
unless the spike shows Grafana can do them.

Dropped: the ZMQ live source and live/Tiled merging. `OPERATIONS.md` 1.2
already preferred a central Tiled when several instruments are watched.

## 3. Run identity and what MOUSE records

### 3.1 Identity convention (agreed 2026-11-15)

Implemented in the instrument repositories, not here:

- Start documents carry `facility` (`"BAM"`) and `instrument` (`"MOUSE"`,
  `"electrochemistry"`), set in `RE.md` in the QueueServer start-up, and
  `station` where one instrument runs several independent stations.
- Processing runs copy `facility`, `instrument` and `station` from their
  measurement.

**Lane key** = (facility, instrument, station, role), where role is
`acquisition` or `processing`. The key is stable; the displayed lane name is
derived from it. Runs without `instrument` go to a labelled fallback: the
configured catalogue root they were found under if that identifies an
instrument, else `(unknown)`. A fallback never creates one lane per run.

Today neither instrument sets these keys (both use `RunEngine({})`), so
existing MOUSE runs land in the fallback lane. Setting them in `mouse_bluesky`
is a prerequisite for the operator trial.

### 3.2 MOUSE runs (from `mouse_bluesky` source; not yet checked against real descriptors)

- One measurement = one run. Start-document keys include `entry_row_index`,
  `proposal`, `sampleid`, `sampos`, `ymd`, `batchnum`, `config_id`,
  `repeat_index`, `sequence_index`, `destination`, `sample_exposure_time`
  (default 600 s).
- Streams: `baseline` (RunEngine `SupplementalData`), `snapshot` (only when
  snapshot signals are given), and `mouse_eiger_measure` with three events on
  normal completion (beam profile, beam profile through sample, sample);
  interrupted runs may have fewer. The three events are readings, not three
  detector frames: long exposures are split into several detector frames.
- `RE.stop()` records `exit_status: "success"` without writing `COMPLETE` in
  `destination`; only normal completion writes it. So "success" in Tiled does
  not mean "complete measurement" for MOUSE.
- Configuration runs (`apply_config`) and vacuum runs (`activity: evacuate |
  ventilate`) are separate runs on the same lane.
- The Eiger frames are saved by the detector's FileWriter into `destination`
  and are not registered with Tiled. The event fields appear to be ROI and
  statistics values (the hinted field is the "direct beam" ROI total); a real
  descriptor will confirm the full list.

## 4. Corrected data from MoDaCor

MOUSE's 1D curves and useful 2D images come from MoDaCor processing finished
measurements. **Each processing is a Bluesky-style run of its own**, linked to
its measurement by `derived_from`. The draft contract for those runs, and what
MoDaCor has to build (a run publisher, a watcher with a durable work ledger,
an atomic job request), are in [processing-runs.md](processing-runs.md). That
work happens in MoDaCor and MoDaCor-examples; this repository only reads the
results.

What the viewer relies on:

- A run is a processing run if its start document carries the processing
  `kind` defined in the contract; `derived_from` alone is not enough.
- Processing runs sit on the processing lane of their measurement's
  instrument/station, using processing times for the clip.
- Per measurement and product, the viewer shows the **latest attempt** (it may
  have failed) and the **latest usable result** (the default in the viewer).
- A measurement with no processing run shows "no result", not "awaiting
  processing". "Awaiting" needs an eligibility signal from the processing side
  and is deferred until the contract defines one.

## 5. Run state, time window and freshness

### 5.1 Status (recorded outcome)

From the stop document only: `success → success`, `fail → failed`,
`abort → aborted`, any other value → `unknown`. No stop document → `open`.
Defined in one place and tested.

### 5.2 Activity (inferred, shown separately)

An open run may be acquiring, paused, crashed, or waiting for ingestion.
Tiled cannot tell which. The viewer reports facts: the time of the last
observed event and its age. It flags **possibly stalled** when that age
exceeds a threshold set per instrument in the view table (MOUSE exposures
alone run to 10 minutes). It never calls an open run "running".

**Cost.** Tiled has no cheap "last event time": the adapter must read a
stream's whole `time` column (the table routes select columns but not rows,
and the server reads a full column even for a slice). That is O(events) and
grows over a 14-day run. So the last event is computed **for open runs
only**, at a slower cadence than the timeline refresh (default 60 s), and
cached in between. If that proves too costly, ask upstream for an aggregate
or row-range API, or check whether node update times suffice (follow-up
question for Codex).

An open clip is drawn to the time of the last successful fetch (normally
"now") in a distinct "open" style. If fetching fails, open clips stop growing
and are marked stale.

### 5.3 Source health

Last successful data fetch, last attempt, age of the data shown, and a
sanitised error category; partial failures (one search failed) are reported
as such. Empty results ("no runs in this window") are distinct from
unavailable data.

### 5.4 Which runs are in the window

Window `[from, to)`. A run overlaps it when `start < to` and
(`stop ≥ from` or no stop). The adapter answers this as the de-duplicated
union of two server-side searches per configured catalogue root, each fully
paged (review 02 found the earlier four-search union missed runs stopping
exactly at `to`):

1. **closed:** `start.time < to` and `stop.time ≥ from`;
2. **open:** no `stop` key (`KeyPresent('stop', exists=False)`, not
   `Eq('stop', None)`), `start.time < to` and `start.time ≥ to − horizon`.

The **open-run horizon** defaults to **21 days**: operando battery experiments
run for up to 14 days, plus a margin. It is anchored to the window end, not to
the wall clock, so browsing history is reproducible. Runs left open for
longer are not shown, and the dashboard says so. Boundary fixtures include
`stop == to`, `stop == from` and `start == to`.

Search mechanics (Tiled 0.2.18; review 02 §§2–3): `/search` returns start and
stop metadata with each page (`fields=metadata`, at most 300 per page; follow
`links.next` until null), so one window costs a few requests, not one per run.
In Tiled 0.2.21 `links.next` carries only `page[cursor]` and `page[limit]`,
not the filters or `fields` (found in step 3; the spike paged unfiltered
searches only), so each page repeats the request's own parameters with the
link's paging parameters.
Searches do not descend into nested containers, so each configured root is
searched. Use explicit `Comparison` queries; `TimeRange` filters start times
only. Server-side filtering is not yet shown to use an index on PostgreSQL;
the data gate checks query plans and latency.

Finding processing runs for a measurement is independent of the window: a
correction made today must be found from last week's measurement. On
PostgreSQL this is `Eq('start.derived_from', [uid])`, which compiles to JSONB
containment (`@>`). Never use `Contains`, which is substring matching. SQLite
cannot run that query; there, the layout module filters candidates in Python.
Tiled has no cross-run anti-join, so "measurements without processing" is
computed in the adapter from two searches.

Runs lasting days also mean: a single clip can span the whole default window;
the run summary shows the full start time and duration; and stream data for
such runs is always decimated, never fetched whole.

### 5.5 Queue status (read-only, optional per instrument)

A small panel per configured RE Manager: manager state (idle, running,
paused), the running plan's name and start time, and the number of queued
items. It shows the queue's own view, so it covers what Tiled cannot (a paused
queue, a plan that failed before opening a run).

- The adapter reads it through `bluesky-queueserver-api` (`status`, and
  `queue_get` for the next items), configured per instrument; instruments
  without a configured RE Manager simply have no panel.
- **Two transports, chosen per instrument in configuration.** The library's
  `REManagerAPI` has a 0MQ and an HTTP variant with the same methods:
  - **0MQ, straight to the RE Manager (prototype default):** no extra service
    on the instrument side; needs the control address (default port 60615)
    reachable from the adapter.
  - **HTTP, through Bluesky HTTP Server (`bluesky-httpserver`, default port
    60610):** one more service per instrument host, but read-only is enforced
    by the server with an API key limited to `read:status` and `read:queue`,
    and the same server enables web queue tools later. Preferred once it is
    deployed.
- **Read-only by construction, on either transport.** The 0MQ interface does
  not restrict a client to read-only calls, so the adapter wraps the API in a
  class that exposes only the read methods, and a test fails if any other
  method is reachable. The wrapper stays in place with HTTP as well.
- Its data age and errors are reported like Tiled's (section 5.3); an
  unreachable RE Manager is shown as such, never as "idle".

## 6. Architecture

```text
Grafana (own container; dashboards and data source provisioned from this repo)
   │  Infinity data source, backend queries only, URL allow-list → adapter
   ▼
adapter  (Python, FastAPI, this package; private network only)
   │  Tiled layout module · run state · window searches · view table · caching
   │  holds a read-only Tiled credential
   ▼
central Tiled (PostgreSQL catalogue)
```

**Thin adapter from the start.** Every panel query goes to the adapter. It
starts small: the Tiled layout module, clip projection, `/clips`,
`/runs/{uid}` and `/health`. Grafana stays declarative. Pointing Infinity
straight at Tiled is tried only as a throwaway diagnostic outside the
repository; it would still leave run state, overlap searches, joins and view
selection without a home.

**Images in the browser.** A 2D image panel loads its image from the browser,
not through Infinity's backend. The image route must be authenticated
(behind Grafana's sign-in or an equivalent same-origin proxy), must never put
a Tiled credential in the browser, and must not need Grafana's HTML sanitising
turned off globally. This is a separate spike gate (step 1.3).

Design to test (review 03 §5): a **same-origin reverse proxy** in front of
Grafana also serves `/run-images/...`, forwarding only GET requests for that
path to the adapter's image endpoint, after checking the browser's Grafana
session (an internal sub-request to Grafana's `/api/user`). The image sits in
a built-in Text panel in HTML mode (`<img src="/run-images/${run:percentencode}/image?...">`)
with sanitising left on. Responses are privately cached and authorisation is
rechecked on every request. Consequence: **operator screens sign in** with a
Grafana Viewer account; anonymous access would not pass the session check.
Closed processing outputs never change, so their image URLs can be cached;
images that can change get a revision in their URL.

The adapter renders the PNG itself from a typed binary slice
(`format=application/octet-stream&slice=…`). Tiled's own PNG output is an
8-bit preview scaled between the 1st and 99th percentiles, with no log scale,
colour map or handling of constant or non-finite data.

**Tiled credential.** The lab server is multi-user, so the adapter gets an API
key with only `read:metadata` and `read:data` scopes, sent as
`Authorization: Apikey …`, never in a URL. (A single-user server's shared key
always carries write rights and cannot be narrowed, so it would not be
acceptable.)

**Layout module facts** (Bluesky 1.14.6 `bluesky.callbacks.tiled_writer`,
Tiled 0.2.8 and 0.2.18 alike; review 02 §1):

| Item | Where |
|---|---|
| Run | `ROOT/{start.uid}`, spec `BlueskyRun` 3.0 |
| Start, stop documents | run metadata keys `start`, `stop` (`stop` absent while open) |
| Stream | `ROOT/{uid}/{stream}`, specs `BlueskyEventStream` 3.0, `composite` |
| Descriptor information | stream metadata: `data_keys`, `hints`, `configuration`; later descriptors under `_config_updates` |
| Scalar table | `ROOT/{uid}/{stream}/internal`: `seq_num`, `time`, each field, `ts_{field}` |
| External array | `ROOT/{uid}/{stream}/{field}` |
| Event count, closed run | `stop.num_events[stream]` (producer-reported) |

Field lists come from stream metadata, not the plugin's `.descriptors`, which
exports the whole run. Table JSON is column-oriented
(`{"time": [...], ...}`); Arrow is the preferred table format.

### 6.1 Adapter endpoints

Times are epoch seconds (UTC), except Grafana time fields in `/clips/frame`.
Every endpoint validates its inputs (ordered finite times, known fields,
capped limits, decoded identities) and states truncation. Infinity queries
use its backend parser (JSONata) only, and pass times with its macros
`${__timeFrom:date:seconds}` / `${__timeTo:date:seconds}` (Grafana's
`${__from}`/`${__to}` are milliseconds).

| Endpoint | Returns |
|---|---|
| `GET /health` | last successful fetch, last attempt, data age, error category, versions |
| `GET /clips?from&to&page` | per run: `uid, start_uid, uid_mismatch, lane_key, lane, role, start, stop, status, last_event, possibly_stalled, stale, label`, plus `truncated` |
| `GET /clips/frame?from&to` | the timeline projection of section 8: final lane/sub-lane, `start_ms`, `end_ms`, `status`, hidden helpers. **Milliseconds**, because these are Grafana time fields; all other endpoints use seconds |
| `GET /runs/{uid}` | summary + flattened start/stop metadata |
| `GET /runs/{uid}/streams` | stream, event count (from the stop document when closed), first/last event time (from the `time` column, selected run only) |
| `GET /runs/{uid}/streams/{stream}/fields` | field, dtype, shape, units, hinted |
| `GET /runs/{uid}/views` | available views and the default, each with stream/fields, units, axes, availability |
| `GET /runs/{uid}/related` | processing runs of this measurement (latest attempt, latest usable, all), or the measurement(s) of this processing run |
| `GET /runs/{uid}/data?view&max_points` | table or time series for a view; only the needed columns are fetched, but all rows (Tiled cannot limit rows of a table), then decimated in the adapter; decimation policy stated |
| `GET /runs/{uid}/curve?view` | `x, y, y_uncertainty` with units, aligned, non-finite values reported |
| `GET /runs/{uid}/image?view&scale` | PNG, bounded size, log scale safe for zero/negative values |

**Caching.** Cache content of closed runs only where the contract makes it
final (measurement data once the stop document exists; processing outputs per
[processing-runs.md](processing-runs.md)); otherwise short TTLs. `/related`
and health are never cached long. Cache keys include catalogue root, run
identity and view/rendering settings.

## 7. Views per instrument

The **view table** is the only place with instrument knowledge. For a run it
matches instrument, role (acquisition/processing), processing kind and the
streams present, in a fixed priority order, and yields the available views
plus a default. Endpoints only resolve what the table says.

| Match | View | Grafana panel (to confirm in the spike) |
|---|---|---|
| MOUSE measurement | readings: `baseline`, `snapshot`, `mouse_eiger_measure` | Table |
| MOUSE configuration / vacuum run | summary + baseline | Table |
| MOUSE processing run, usable | corrected 1D I(Q), log–log; uncertainty as I−σ and I+σ bounding curves plus a table (XY chart has no error bars) | XY chart, log scale on both X and Y fields |
| MOUSE processing run, usable | corrected 2D image | Text panel with an `<img>` via the image route (section 6) |
| anything else (fallback) | hinted numeric scalars against time; else all numeric scalars; else a bounded table; else "no plottable data" | Time series / Table |
| electrochemistry (later) | voltage and current against time | Time series; XY chart |

The table also holds each instrument's possibly-stalled threshold.

## 8. Dashboard, version 1

Built-in panels plus pinned, published plugins (Grafana 13.3.1, Infinity
4.1.6; review 03). Everything below is still subject to the panel and image
gates (step 1.2, 1.3): review 03 checked source and documentation, not a
running Grafana.

- **Bottom: timeline.** Built-in **State timeline**, one series per lane or
  sub-lane, merging of equal values off, "connect null values" off, colour
  by status through value mappings, open runs as their own state.
  - **Frame shape** (from `/clips/frame`): per lane, `start`, `stop`
    (Grafana time fields, milliseconds; named so they sort in this order,
    because Infinity returns columns alphabetically and the first time field
    is taken as the start), `status` (the only visible field, named after the
    lane), and hidden helpers `uid`, `label`, `clip_from_ms`, `clip_to_ms`
    (true extent, for zoom). Helpers are hidden by field overrides, not
    removed, so links keep them. Line width 1, so touching clips of equal
    status show a boundary.
  - **Overlaps:** a State timeline series cannot show two clips at once. The
    adapter assigns sub-lanes: sorted by (start, uid), each clip takes the
    lowest sub-lane that is free, touching clips may share. No clip is ever
    dropped. Sub-lane numbers are per window, not a property of a run.
  - **Getting several lanes into one panel:** Infinity returns one frame per
    query. First choice: the adapter returns one long table with a
    `lane` column already final (lanes and sub-lanes allocated), and a single
    **Partition by values** transformation on `lane` splits it into frames.
    That transformation only splits; it does not group or reshape. Fallback
    if it fails the gate: a fixed number of lane-slot queries
    (`/clips/frame?slot=k`), with empty slots hidden.
  - **Short clips:** a 3-second run in a 12-hour window is a fraction of a
    pixel. A companion **runs table** next to the timeline lists the window's
    runs for precise selection; zoom shows them properly.
- **Selection:** two tooltip data links, "Select run"
  (`?var-run=${__data.fields["uid"]}&${__url_time_range}`, keeping relative
  ranges like `now-12h`) and "Zoom to run" (`from`/`to` from
  `clip_from_ms`/`clip_to_ms`, with a finite end for open runs and padding
  for zero-length runs). Opening in the same tab; whether navigation avoids
  a full reload is a gate check, and one-click selection is not promised.
  The hidden variable `run` survives refreshes and stays selected when the
  run leaves the window.
- **Shared crosshair:** a hover guide aligned in time across the timeline and
  time-series panels. Not a persistent or draggable playhead, and not
  meaningful on I(Q) plots.
- **Top left:** the runs table, run summary, streams and channels, health
  with data age, failed and aborted runs with reasons, and the queue-status
  panel. Variable pickers in the variable bar. Optional, tried in the panel
  gate: the published **Business Variable** panel (5.2.0) to put the pickers
  inside the top-left panel, matching the NLE layout. Adopted if it works
  with the pinned Grafana.
- **Top right:** a fixed **Table** fed by the run's default view (MOUSE
  readings, or the generic fallback), plus a time series for time-series
  views. Grafana can show and hide panels on a variable only in its "Auto
  grid" layout, which may not keep the NLE geometry. So switching between view
  kinds (table, time series, curve, image) is decided in step 6: either an
  Auto grid viewer proven to keep the layout, or small detail dashboards per
  view kind, linked from the overview. Dashboards test view kinds, never
  instrument names.
- A header line: "Recorded runs from Tiled" and data age.
- The queue-status panel (section 5.5), top left, for each instrument with a
  configured RE Manager.

## 9. Steps

1. **Spike (time-boxed; three gates in parallel).**
   0. Pins (tested by review 02 on SQLite): Python 3.14, `tiled==0.2.18`,
      `bluesky==1.14.6`, `bluesky-tiled-plugins==2.0.2`, `event-model==1.23.1`,
      `ophyd==1.11.0`; Grafana OSS **13.3.1** with Infinity **4.1.6**
      (optional: Business Variable 5.2.0). Still to check: MOUSE's Tiled 0.2.8
      writer client against the central server's version.
      **Gates 1.1–1.3 need Docker running**; reviews 02 and 03 could not run
      containers, so no gate has passed yet.
   1. **Data gate:** a minimal fixture catalogue on PostgreSQL written by a real
      RunEngine through `TiledWriter` (two instruments, open and closed runs,
      runs crossing the window edges), plus processing runs composed with
      `event_model`. Prove layout access, the window union of section 5.4 with
      paging, open-run discovery and reverse `derived_from` lookup.
      Review 02 verified all of this on SQLite only (Docker was not running);
      the PostgreSQL run, including query plans and latency, is still open.
   2. **Panel gate:** State timeline fed from a static file of synthetic clip
      rows. Acceptance cases: two touching successful runs with different UIDs;
      a real gap; runs on two lanes with unequal times; a run crossing the
      window edge; an open run; overlapping processing attempts; a run of a few
      seconds in a 12-hour window. Each link must select the exact UID, keep
      the range and survive refresh. Also: the Partition-by-values route
      (or the lane-slot fallback) from section 8; sub-lanes for overlaps;
      relative ranges kept by links; same-tab navigation without a full
      reload; the `run` variable surviving refresh; a benchmark of 20 lanes
      and 2,000 clips at 5-second refresh (payload size, query and render
      time, click response); and Business Variable in the top-left panel.
      No run may be silently dropped; short clips may be widened for
      display but keep their true times.
   3. **Image gate:** the reverse-proxy design of section 6 with a Text
      panel. Test signed-in, signed-out, expired-session and direct-URL
      requests, and that no Tiled credential reaches the browser.
2. **Scaffold and demo stack** (section 10), keeping the gate fixtures.
3. **Vertical slice.** First, before the search code (review 04, findings
   4, 10, 11): complete the window fixtures with an acquisition failure, a
   start-UID mismatch, a MOUSE stopped-success run with a synthetic
   completion marker, a success followed by a failed retry (coherent
   `request_id`/`attempt`), a stop-time tie broken by UID, and a
   partial-output failure; PostgreSQL search tests that fail, not skip,
   when PostgreSQL is missing; query plans re-measured on 0.2.21 (new
   metadata index); an isolated Tiled 0.2.8 client write/read check against
   0.2.21. Then: layout module, run state (section 5), `/clips`,
   `/runs/{uid}`, `/health`, `/runs/{uid}/views` with the fallback and the
   MOUSE readings table; the version 1 dashboard.
   Then the queue-status panel (section 5.5) for MOUSE's RE Manager, tested
   against a local RE Manager in the demo stack (the sibling repositories
   already start one for their tests).
4. **Operator trial** on the central Tiled with a read-only credential, after
   `facility`/`instrument` are set in `mouse_bluesky`, during a lab session
   (section 10.2). Measure run counts, refresh latency and what operators
   miss. The schema capture can run in an earlier lab session, before step 3
   finishes, to correct the fixtures early.
5. **Processing contract** ([processing-runs.md](processing-runs.md)): agreed
   with MoDaCor from step 1 onwards; MoDaCor builds the publisher and watcher.
6. **Corrected views:** processing lane, related runs, 1D curve, 2D image;
   first against synthetic processing runs, then MoDaCor's real ones.
7. **Electrochemistry**, once it writes to Tiled: view-table entries and
   fixtures; no timeline changes expected.
8. **Customisation:** presets (from `CONFIG_SPEC.md`), lanes per event stream,
   then a custom panel only if version 1 falls short. Tiled's SQL catalogue
   does not support `Regex` queries, so preset regex filters run in the
   adapter; other predicates map to `Eq`, `In`, `NotIn` and `Comparison`, with
   explicit tests for missing fields (SQLite and PostgreSQL differ there).

## 10. Repository and deployment

Tooling as in `electrochemistry_bluesky` / `keithley2460_ophyd`; agent
instructions in the style of `MoDaCor` (see `AGENTS.md`):

- `src/event_timeline_tiled/`, setuptools, `uv` with committed `uv.lock`,
  ruff (E, F, I, B, UP; 100 columns), `mypy --strict`, pytest, MkDocs
  (readthedocs theme), Conventional Commits, python-semantic-release,
  BSD-3-Clause, `BAMresearch/event_timeline_tiled`.
- Python: declare only what the pinned Tiled stack supports. Start CI on one
  Python version on Linux; widen the matrix after the first vertical slice.
- CI: lint, types, strict docs build, dashboard/provisioning checks, tests;
  the PostgreSQL search-contract tests are **required**, not optional.
- `deploy/compose.yaml` (Docker Compose; Podman only after its DNS set-up is tested, review 04):
  - `grafana`: image built from `grafana/grafana:13.3.1` (digest recorded),
    with Infinity 4.1.6 (and Business Variable, if adopted) installed at
    build time by Grafana's plugin CLI, so nothing downloads at start-up.
    File provisioning from `grafana/`, mounted read-only: data source and
    dashboards with stable UIDs, `editable: false`, `allowUiUpdates: false`,
    Infinity `allowedHosts` limited to the adapter's URL. Grafana's database
    in its own volume; secrets from the environment. Dashboards using the
    newer layout (Auto grid) are exported from the pinned Grafana with their
    full resource wrapper;
  - `proxy`: the same-origin reverse proxy for Grafana and `/run-images/`
    (section 6);
  - `adapter`: private network only; Tiled URL and read-only key from the
    environment;
  - profile `demo`: `tiled` + `postgres` + seeding job, disposable;
  - profile `standalone` (later, step 7): persistent `tiled` + `postgres` for
    a self-contained deployment such as the electrochemistry beamline.

### 10.1 Working without the lab server

The lab Tiled is out of reach from home and from the beamline, so the demo
stack must stand in for it completely:

- Tiled in **multi-user mode** like the lab server, on PostgreSQL, with a
  seeding principal that writes and a separate API key scoped to
  `read:metadata` and `read:data` that the adapter uses, so the read-only
  credential path is exercised every day, not first in the lab.
- A local RE Manager for the queue-status panel.
- The seeding job writes all fixture cases of section 11, including open
  runs whose events keep arriving, so "open", "possibly stalled" and refresh
  behaviour can be watched live. Live runs go through a RunEngine too: MOUSE
  measurements in one process, the generic operando run in a second.
- A storage directory readable by the demo Tiled server, standing in for
  MoDaCor's output directory.

Nothing in CI or local development may need the lab network.

### 10.2 Lab sessions

Work that needs the lab Tiled is batched and prepared in advance:

- the compatibility check of MOUSE's Tiled 0.2.8 writer client against the
  lab server's version;
- query plans and latency of the window searches on the lab PostgreSQL;
- a **schema capture**: a script that records the start-document keys,
  stream names and descriptor `data_keys` (names, dtypes, shapes, units;
  no values) of recent MOUSE runs, to check plan section 3.2 and update the
  fixtures. It prints nothing identifying and is reviewed before its output
  leaves the lab;
- the operator trial (step 4);
- before operators open the timeline from other machines: a TLS terminator
  in front of the proxy, Grafana's public URL and `cookie_secure`, and
  Viewer accounts (review 04, finding 13);
- before upgrading an existing Tiled server across 0.2.19: its catalogue
  migrations (access blobs to tags, new metadata index); the demo's fresh
  databases do not test that.

## 11. Testing

- Unit: status mapping, possibly-stalled, window union and de-duplication,
  lane keys and fallback, view resolution and fallback chain, decimation,
  curve alignment, image scaling, input validation.
- Integration, against a seeded Tiled: acquisition runs written by a real
  RunEngine with `ophyd.sim` devices through `TiledWriter`; processing runs
  composed with `event_model` through `TiledWriter` per the contract. Cases:
  MOUSE-like measurement with `baseline`, `snapshot` and three
  `mouse_eiger_measure` events; one interrupted with fewer events; a stopped
  (`success`) run; a long quiet open run; an aborted run; a run without
  `instrument`; a `start.uid` mismatch; a second generic instrument; processing
  runs that succeed, fail, are open, reprocess, and arrive a week after their
  measurement. No hand-made imitation Tiled trees.
- Search contract on PostgreSQL (required) and SQLite.
- Dashboards: every variable used exists; every query targets a known
  endpoint; optional compose smoke test through Grafana's HTTP API.
- Tests never contact production services or hardware. No real sample names,
  proposals, user names, hosts or paths in fixtures.

## 12. Not in the prototype

Writing to production Tiled; starting acquisition or processing; any queue
control (adding, removing, pausing, resuming); ZMQ live documents; editing
presets in the browser; alerting; raw MOUSE detector frames.

## 13. Later

- Queued items as future clips on each instrument's lane, built on the queue
  status source (section 5.5).
- Raw MOUSE frames, once `mouse_bluesky` registers Eiger files with Tiled.
- "Awaiting processing", once the processing side publishes eligibility.
- Persistent playhead and selection outline; a custom timeline panel.
- Following open runs through Tiled's streaming support (WebSockets) as a
  nudge to the polling path; it needs a streaming cache (Redis) configured on
  the Tiled server and does not replace searches.

## 14. Decided (maintainer, 2026-11-15)

- Basic timeline with available tools first; customisation afterwards.
- MOUSE first; electrochemistry later without redesign; one central Tiled on
  PostgreSQL when both run.
- Views: MOUSE 2D, 1D or tabular; electrochemistry 1D (later).
- Ship our own Grafana container.
- First users: operators following the queue.
- Name: `event_timeline_tiled`.
- Start documents carry `facility: "BAM"` and `instrument`.
- MOUSE 1D curves and corrected 2D data come from MoDaCor processing.
- Each processing is a Bluesky-style run of its own, linked by `derived_from`.
- Processing is started by a RunEngine callback or a Tiled watcher; the plan
  recommends the watcher (see [processing-runs.md](processing-runs.md)).

- Operators follow the queue with `qserver status` only today; the dashboard
  adds a read-only queue-status panel (section 5.5).
- Experiments can last up to 14 days (operando battery experiments); the
  open-run horizon defaults to 21 days.

- The adapter runs on the instrument's local network, where MOUSE's RE
  Manager (0MQ, port 60615) is reachable. Off-site development uses the demo
  stack's own RE Manager.
- Processing arrays are HDF5 files registered through stream resources
  ([processing-runs.md](processing-runs.md) §1.1), per review 02.

- The lab Tiled server is multi-user, so the adapter gets a scoped read-only
  key. It can read MoDaCor's output directory.
- The lab Tiled server is reachable only from the lab network: not from home
  and not from the electrochemistry beamline. Development therefore runs
  entirely against the demo stack (section 10.1); anything that needs the lab
  server is batched into lab sessions (section 10.2).

- Electrochemistry at the beamline gets a separate, self-contained stack
  with its own Tiled. The compose file therefore gets a third profile,
  `standalone`: a persistent (not disposable) Tiled on PostgreSQL alongside
  Grafana, proxy and adapter. Built when electrochemistry writes to Tiled
  (step 7).
- Grafana 13.3.1 and Infinity 4.1.6, per review 03.
- Operator screens sign in with a Grafana Viewer account (the image route
  needs a session); no anonymous access.

- Spike passed (2026-11-15): `/clips/frame` uses `start`/`stop` time fields,
  one Partition-by-values transformation, Business Variable panel adopted,
  default Grafana plugin installation and auto-update turned off
  ([spike-results.md](spike-results.md)).

- Step 2 (scaffold and demo stack) built 2026-11-16; Tiled pinned at 0.2.21
  (decision D-002). Review: [brief 04](reviews/brief-04-scaffold.md).
  Lab-server account set-up: [Tiled access](../technical/tiled-access.md).

- Review 04 folded in 2026-11-16
  ([disposition](reviews/disposition-04.md)): health validates Tiled's
  responses, settings reject ambiguous URLs and roots, `ett-check-access`
  replaces the curl checks, live demo runs come from a RunEngine, keys in
  separate volumes, long-lived demo services as uid 10001, Docker only
  (decisions D-004, D-005).

- Step 3 in progress (2026-11-16): adapter endpoints and the version 1
  timeline dashboard built and checked on the demo stack. Found on the way:
  Tiled's `links.next` drops search filters (§5.4); SQL-backed event tables
  return rows in storage order, so the layout module sorts by `seq_num`;
  Infinity types auto-detected time columns as numbers, so time-series data
  is long format with Infinity's `timeseries` query format (no dashboard
  transformation). Still to do in step 3: the 0.2.8 client check, query
  plans on 0.2.21, and the queue-status panel.

## 15. Open questions

None at the moment. Next: step 3, starting with the fixture and test items
listed there.
