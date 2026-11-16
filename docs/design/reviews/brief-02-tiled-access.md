# Codex brief 02: verify how Bluesky runs are read from Tiled

## Context

`~/Code/event_timeline_tiled` is a new prototype: a Grafana
timeline of Bluesky runs stored in Tiled, read through a small Python (FastAPI)
adapter that Grafana queries through its Infinity data source. The
plan is `docs/design/plan.md` (revision 5); sections 3, 5.4, 6 and 6.1 depend
on facts about Tiled that have not been checked.

The first data source is the MOUSE instrument, which writes runs with
`TiledWriter` (see
`~/Code/mouse_bluesky/qserver_profile/profile_mouse/startup/02_tiledwriter.py`).
Production uses a central Tiled with an SQL catalogue on PostgreSQL. A second
instrument (electrochemistry, a flyer using `collect_while_completing`) will
write to the same Tiled later.

MOUSE's environment pins, installed in
`~/Code/mouse_bluesky/.venv/lib/python*/site-packages/`:
tiled 0.2.8, bluesky-tiled-plugins 2.0.2, bluesky 1.14.6, event-model 1.23.1.
Use that installed source as the reference. MoDaCor's development environment
has tiled 0.2.18 (`~/Code/MoDaCor/.venv*/`); where 0.2.8 and 0.2.18
differ on anything below, say so, and propose one pinned combination for the
project. `~/Code/tiled` is an older checkout (April 2025); do not
rely on it without saying so. The processing-run contract under discussion is
`docs/design/processing-runs.md`.

## Task

Answer each question from the installed source (cite file and line), and where
possible confirm it with a throwaway script: write a few runs with a
`RunEngine`, `ophyd.sim` devices (including a 2D detector) and `TiledWriter`
into a temporary Tiled server, then read them back. If Docker is available,
also try a Tiled server on PostgreSQL. Keep scripts outside the repository;
do not add code to the repository.

1. Layout: what are the node paths of a run, its start and stop documents, each
   stream, a stream's descriptors, and a stream's scalar columns including
   `time` and `seq_num`? Use streams shaped like MOUSE's: `snapshot` (created
   with `bps.create`/`bps.save`) and `mouse_eiger_measure` (three
   `trigger_and_read` events of scalar ROI/stats values). MOUSE's Eiger frames
   are not in Tiled today; for later, how would ophyd v1 area-detector files
   registered through resource/datum documents appear via `TiledWriter` in
   these versions?
1a. Processing runs: each MoDaCor processing will be a Bluesky-style run of its
   own, with documents composed by `event_model` (no RunEngine) and written by
   `TiledWriter`. It must carry a corrected 1D curve (`Q`, `I`,
   `I_uncertainty`, about 100–2,000 points each) and a corrected 2D image
   (about 1–4 megapixels, float). Which ways work with these versions, and how
   does each read back: arrays as event data (does `TiledWriter` accept
   array-shaped fields of that size?), an HDF5 file written by MoDaCor and
   declared through stream resources/datums, or MoDaCor's `TiledSink`
   (`~/Code/MoDaCor/src/modacor/io/tiled/tiled_sink.py`, which uses
   `create_container` and `write_array`) writing into the run's container?
   Recommend one. Can a 2D array then be fetched over HTTP as PNG (with what
   scaling) or as a compact array an adapter can read cheaply?
1b. Linking: start documents will carry `derived_from` as a list of raw run
   UIDs. On PostgreSQL, can the server find "runs whose `derived_from`
   contains UID X", and "finished measurement runs in a time window with no
   processing run pointing at them" (or what is the cheapest way to answer the
   latter)? Would a scalar `derived_from` search much better than a list?
2. Search: plan section 5.4 defines the runs in a window `[from, to)` as the
   de-duplicated union of four searches (start in window; stop in window;
   closed runs spanning the window; open runs with `start.time < to` within an
   open-run horizon). Can each be done server-side and fully paged on
   PostgreSQL, and how is "no stop document" expressed as a query? Can the
   server filter runs on `start.time` / `stop.time` ranges and on runs with no
   stop document? Does `bluesky_tiled_plugins.queries.TimeRange`
   exist in this version, and what does it compile to? Which `tiled.queries`
   work on nested metadata keys (Eq, In, NotIn, Comparison, Regex, Contains,
   FullText) on PostgreSQL versus SQLite?
3. HTTP: which REST endpoints and `format=` values return a stream's columns as
   a table (CSV, JSON, Arrow); can a subset of columns be requested; what are
   the page-size limits on `/search`, and can the search response include the
   start/stop metadata so one request lists all clips in a window?
4. Cost: the cheapest way to get a stream's first and last event time and its
   event count without reading the whole table.
5. Running runs: how does a run look while being written (no stop document,
   partly written tables), and how quickly do new events become visible with
   `batch_size=1`? Is Tiled's streaming/websocket support useful here?
6. Instruments: if two instruments write to one Tiled, how could a reader tell
   their runs apart (a start-document key, the container they are written
   under, anything `TiledWriter` records)? Can `TiledWriter` write under a
   sub-container per instrument?
7. Auth: how does a client pass an API key (header, query parameter), and can a
   key be limited to read-only access?

## Response

Write to `docs/design/reviews/response-02-tiled-access.md`:

- One section per question: the answer, evidence (file:line in the installed
  packages, or a few lines of script output), and confidence (verified by
  running / read in source / unverified).
- A short list of corrections to `docs/design/plan.md` that follow from the answers.
