# Disposition of Codex review 02 (Tiled access)

Review: [response-02-tiled-access.md](response-02-tiled-access.md). Result:
plan revision 6, [processing-runs.md](../processing-runs.md) §1.1–1.2,
`AGENTS.md` (layout module rule). Everything was verified on SQLite and by
SQL compilation for PostgreSQL; no PostgreSQL server was run (Docker was not
running), so the PostgreSQL data gate stays open.

| Correction | Disposition | Where |
|---|---|---|
| 1. Window union missed `stop == to` | Accepted: two-query union (closed overlap; open within horizon), horizon anchored to window end, boundary fixtures | plan §5.4 |
| 2. Processing array representation | Accepted: HDF5 via stream resource/datum with the tested parameters; writer import specified; plugin writer noted as possible later option after an upstream fix | processing-runs §1.1 |
| 3. Exact `derived_from` lookup | Accepted: JSONB containment via `Eq` on PostgreSQL, Python fallback on SQLite, adapter-side anti-join, never `Contains` | plan §5.4; processing-runs §1.2; AGENTS |
| 4. No row slicing or cheap extents | Accepted: counts from `stop.num_events`; time column read for the selected run; last-event checks only for open runs at a slower cadence | plan §5.2, §6.1 |
| 5. Tiled PNG is a percentile preview | Accepted: adapter renders its own PNG from typed binary slices | plan §6 |
| 6. Pins, scoped credentials, PostgreSQL gate | Accepted: pins recorded; scoped multi-user key required; single-user server key not acceptable for production; PostgreSQL run and 0.2.8-client compatibility check still open | plan §6, §9 step 1, §15 |

Also taken from the review: layout table (plan §6); search page size and
`links.next` paging; `Regex` unsupported in SQL catalogues (affects presets,
plan §9 step 8); per-root searches; streaming needs a Redis cache on the
server (plan §13).

Follow-up for a later brief: does Tiled record a node update time for a
stream's `internal` table that could stand in for "last event time" on open
runs, avoiding full `time`-column reads?
