# Spike results (plan step 1)

Date: 2026-11-15. Throwaway set-up outside the repository: Docker Desktop
29.9, PostgreSQL 16.16, Tiled 0.2.18 (catalogue and event tables on
PostgreSQL, single-user key), Bluesky 1.14.6, bluesky-tiled-plugins 2.0.2,
event-model 1.23.1, ophyd 1.11.0, Python 3.14; Grafana OSS 13.3.1 with
Infinity 4.1.6 and Business Variable 5.2.0; nginx 1.27; headless Chromium
(Playwright) for browser checks. Fixture runs were written by a real
RunEngine through `bluesky.callbacks.tiled_writer.TiledWriter`, retimed onto
window-edge cases; processing runs were composed with `event_model` and carry
HDF5 arrays registered through stream resources (processing-runs.md §1.1).

## Gate 1.1: data (PostgreSQL) — pass

15 of 15 checks:

- The window union of plan §5.4 returns exactly the expected runs, including
  `stop == from`, `stop == to`, spanning runs, a run crossing `to`, open runs
  inside and 20 days back, and excludes `start == to`, runs before the window
  and an open run 22 days back.
- Paging follows `links.next` to every run without duplicates; `page[limit]=301`
  is rejected (422).
- `Eq('start.derived_from', [uid])` finds processing runs by JSONB containment,
  including a run with two parents and a processing run a week after its
  measurement; `In` with one-element lists batches lookups.
- Layout as review 02 describes: streams `baseline`, `snapshot`,
  `mouse_eiger_measure`; `data_keys`/`hints` in stream metadata;
  `stop.num_events`; no `stop` key on open runs; `internal` columns
  `seq_num, time, <fields>, ts_<fields>`.
- HDF5 processing arrays read back with the declared shapes and dtype
  (`(1, 2000)` float32, `(1, 1024, 1024)`), and as typed binary over HTTP,
  whole or sliced.

**Scale.** 5,000 run entries in one container, 12-hour window: about 300 ms
per window search from the client, including HTTP; the slowest SQL statement
took 2.5 ms. PostgreSQL uses the parent index and then filters every run under
the root on the start/stop times (linear: 4,927 rows filtered for 72 hits).
Fine for MOUSE for years; past roughly 100,000 runs under one root, a database
administrator could add an expression index on `metadata #> '{start,time}'`.

Not covered here: Tiled in multi-user mode with a scoped read-only key (goes
into the demo stack, plan §10.1); MOUSE's Tiled 0.2.8 client against the lab
server (lab session, plan §10.2).

## Gate 1.2: timeline panel — pass

40 of 41 click checks, plus the load benchmark.

- **Frame field names matter.** Infinity's backend parser returns columns in
  alphabetical order, and State timeline takes the first time field as the
  start. With `start_ms`/`end_ms`, clips were drawn from their end. The frame
  contract uses `start` and `stop`, which sort correctly.
- Lanes from one long table split by a single **Partition by values** on
  `lane` work; the lane-slot fallback is not needed. The display name comes
  from the partition label (`${__field.labels.lane}`).
- With merging off and **line width 1**, touching runs with equal status show
  a boundary; without a line they look like one block (they were still
  separately selectable).
- Clicks select the exact run in the middle and near both ends of every clip:
  touching runs, overlapping attempts on sub-lanes, an open run, runs crossing
  either window edge, unequal times on different lanes. A gap has nothing to
  select.
- Misses: the outermost pixel of a run clipped at the left edge, and a
  3-second run (1 pixel wide in 12 hours). The companion runs table is needed.
- "Select run" keeps relative ranges (`from=now-12h&to=now`), navigates
  without a full page reload, and the selection survives reload and refresh.
  "Zoom to run" carries the clip extent in milliseconds.
- Load, 20 lanes × 100 clips: 408 KiB from the adapter, 560 KiB through
  Grafana, backend query median 61 ms, first render 2.4 s, clicks 35–65 ms,
  9 of 10 sampled clips (3–8 pixels wide) hit. Auto-refresh every 5 s for 30 s:
  one query per cycle, main thread responsive (≤ 15 ms).

## Gate 1.3: image route — pass

6 of 6 checks, with the design of plan §6: nginx in front of Grafana; for
`/run-images/` it allows GET only, strips `Authorization` and `Cookie` before
forwarding, and checks the session with an internal sub-request to Grafana's
`/api/user`. A stand-in for the adapter's image endpoint fetched a strided
binary slice from Tiled with its server-side key and rendered a 512-pixel,
log-scaled PNG.

- Signed in through the proxy, the image loads in a Text panel (HTML mode,
  sanitising on); no credential in the image URL; private caching.
- Refused with 401: no session, a forged session cookie, Grafana basic-auth
  credentials, after sign-out, from a fresh browser. POST refused (403).
- The sanitiser removed an injected `<script>` and `onerror`, and also the
  `id` attribute of the image (use other attributes to address it).
- `Cache-Control` was set twice (stand-in and nginx); set it in one place.

## Business Variable panel (optional) — works

Buttons inside the panel set the dashboard variable (`var-view` in the URL)
and dependent panels update. The panel showed an unexplained error status,
probably because it had no data-source setting and Grafana ran it against the
default source; to be configured properly at scaffold time. Adopted for the
top-left selector.

## Findings for the scaffold

1. `/clips/frame` time fields are named `start` and `stop` (milliseconds).
2. State timeline: merging off, line width 1, hidden helpers by override,
   display name from the `lane` partition label.
3. Grafana installs and **updates its default plugins in the background at
   start-up** (seen: `prometheus 13.2.5 → 13.2.6`). The image build must turn
   off default plugin preinstallation and auto-update, so only the pinned
   plugins exist.
4. Business Variable 5.2.0 joins the pinned plugins; give its panel an
   explicit data-source setting.
5. One place sets `Cache-Control` for images.
6. Fixture seeding approach (real RunEngine documents retimed onto chosen
   intervals; `event_model` processing runs with HDF5 stream resources) carries
   over into the demo stack and tests.
