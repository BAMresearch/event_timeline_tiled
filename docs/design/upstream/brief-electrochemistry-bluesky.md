# Brief: electrochemistry-bluesky runs on the event timeline

For: whoever works on
[electrochemistry_bluesky](https://github.com/BAMresearch/electrochemistry_bluesky)
(a person or a coding agent). Self-contained; the changes requested here
are all in that repository or upstream of it. Nothing here asks for
changes to event-timeline-tiled, which tolerates runs without any of this.

## Context

event-timeline-tiled shows Bluesky runs from Tiled as a Grafana timeline:
one lane per instrument, each run a block coloured by its recorded outcome,
and for open runs whether they are still producing events ("possibly
stalled" when no event has reached Tiled for a while). It reads Tiled only.

On 2026-11-16 we ran electrochemistry-bluesky (source as of `b9f9bd6`;
Tiled 0.2.21, bluesky-tiled-plugins 2.0.13, bluesky 1.16.0,
bluesky-queueserver 0.0.26) on the simulator through its own RE Manager,
with a session that has `[tiled] local = true`, `sim_speed = 10`. We queued
seven template rows (rest, bipolar pulse, pulse train, SOC positioning,
cycling, two pulse trains) and aborted an eighth. The timeline read that
local store. Outcomes, queue status and data were right. Four problems
came from how runs reach Tiled; they are below, most important first.

## 1. Rows reach Tiled only per 1000 rows: add a timed flush

**Seen.** `tiled_store.open_tiled_store` creates
`TiledWriter(client, batch_size=BATCH_ROWS)` with `BATCH_ROWS = 1000`.
In bluesky-tiled-plugins 2.0.13, `_RunWriter.event` caches rows per stream
and writes them only when the cache reaches `batch_size`, or at the stop
document (`event_page` is unpacked into single events first, so it batches
the same way). There is no time-based flush and no public flush method.
During a run, Tiled therefore has nothing of a stream until its first 1000
rows, and then lags by up to 1000 rows. A stream's table does not even
exist until the first batch.

**Why it matters.** Any live reader of Tiled sees stale data. For the
timeline, a slow run looks dead: at one record per minute (a long cycling
step), rows reach Tiled about every 17 hours, so a healthy run is shown as
"possibly stalled" after the stall threshold (30 minutes by default). The
viewer notebook's live view reads the HDF5 file and is not affected.

**Requested.** Event rows of an open run reach Tiled within a bounded
time of being read: a timed flush, e.g. `flush_s` in the session's
`[tiled]` table (suggested default 10 s; it must stay well below the
stall threshold the timeline will use for this instrument). Keep the
batch size for throughput at high rates. Constraints that stay as they
are: Tiled never delays, stops or fails a run (the writer thread and the
backup spill of `SafeTiledWriter`); the stop document still writes the
rest.

The clean place is bluesky-tiled-plugins: a flush interval or a public
`flush()` on `TiledWriter`. Check the newest release first; if it has
none, propose it upstream. An interim in this repository may rely on
`TiledWriter` internals only if the dependency stays pinned to the
version it was written against and a test fails when that version
changes. Choose and record the approach.

**Test.** With the simulator and a local store: during a run slower than
`batch_size` rows per `flush_s`, rows appear in Tiled within `flush_s`
plus one write; at the stop document all rows are there; a Tiled that is
down still never affects the run.

## 2. Start documents say which instrument wrote them

**Seen.** Start documents carry `plan_name: run_sequence`, `scan_id`,
`measurement_id`, `sample_id` and the sequence, but no `facility`,
`instrument` or `station`. The timeline groups runs into lanes by those
three keys, so every run landed in a lane "(unknown)", got the generic
views, and was labelled "run_sequence · 8" instead of its run-sheet
name.

**Requested.**

- Write `facility`, `instrument` and (if there can be more than one set-up
  of the same instrument) `station` into every start document, from the
  session file, since the session already fixes the set-up. Plain strings;
  the same values for every run of a set-up. The names are yours to
  choose; tell us the `instrument` value.
- Hint the fields a reader should plot: in the `readout` descriptor,
  `hints` naming `voltage_v` and `current_a` (Bluesky's usual
  `hints[<object>]["fields"]`). Without hints, generic viewers plot every
  numeric field on one axis; in the trial `t_s` (seconds, up to 1000s)
  flattened voltage and current to a line.

With the `instrument` value, event-timeline-tiled will add a view-table
entry for it (voltage and current against time; labels from
`measurement_id` and `sample_id`; a stall threshold to match
`flush_s`). That entry is our work, not yours.

## 3. The local store fails concurrent reads

**Seen.** The local server stores tables in DuckDB
(`--write duckdb:///…/tables.duckdb`). Tiled 0.2.21 gives DuckDB storage a
SQLAlchemy `StaticPool`: one ADBC connection shared by every request
(`tiled/storage.py`, `SQLStorage.__post_init__` and `_connection_pool`).
Concurrent table reads then fail with HTTP 500 and, in `server.log`,
`Invalid Input Error: Attempting to execute an unsuccessful or closed
pending query result`. In the trial, 3 of 32 parallel reads failed, and
every dashboard load with a run selected lost some panels. Any reader is
affected: two notebooks, or a notebook while the writer flushes.

**Requested.** Make the local store safe for concurrent readers while
it is written. One option to evaluate: SQLite table storage
(`sqlite:///…`), which Tiled 0.2.21 pools with a `QueuePool`; check write
throughput and readers during writes. Otherwise report it to Tiled and
document the limit. An existing store keeps working, or there is a stated
way to move it.

**Test.** Many parallel full-table reads (for example 32 from 12 threads)
of finished runs while a run is being written: no errors.

## 4. Document that the local store has no read-only key

**Seen.** The local server runs in single-user mode: its one key
(`data_dir/tiled/api-key`) can write, and there is no `/auth/whoami` and
no scoped keys. The timeline's deployment expects a read-only key, and
its access check cannot pass against this store.

**Requested.** Say in the Tiled-store documentation that dashboards and
other shared readers should use a Tiled server with scoped keys (the
`[tiled] uri = …` mode), and that the local key must not be handed to
them. If you see a simple way to serve the local store read-only to
other readers during a session, propose it; it is not required.

## Out of scope

Anything in event-timeline-tiled. Since the trial it already reads a
stream without a table as "no events written yet" rather than an error,
keeps runs queued back to back in one lane, and shows a message for a
selected run that the server does not have.

## When done

Report the `facility`/`instrument`/`station` values, the flush setting and
its default, the approach for item 1 (upstream or interim, with versions),
and the decision on item 3, so that event-timeline-tiled can add its
view-table entry and rerun the trial.
