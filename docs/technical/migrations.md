# Contract migrations

Incompatible changes to the adapter's endpoints, the view table, presets or
the processing-run schema are listed here, newest first, with what users of
the old form must change. Until v1.0 they are released as minor versions.

## Unreleased: adapter endpoints after review 05

The endpoints are new in this release, so no deployed consumer is affected.
Recorded because the meaning of these fields changed during review:

- `lane_key` parts are percent-encoded and a literal `-` is `%2D`; keys
  without `/`, `%` or a literal `-` part are unchanged.
- `lane` and the frame's `lane` are unique per lane key and sub-lane; a
  name already taken gets ` (2)`, ` (3)`.
- `/runs/{uid}/data?max_points` bounds the rows returned over all fields,
  not the events per field.
- Metadata lists of non-string scalars are joined as JSON (`true`, `null`),
  not as Python text (`True`, `None`).
- `/runs/{uid}/views` `fields` lists each field once; per-stream fields and
  units are in the new `columns`.
- `truncated` of a search is true only if more runs matched than the cap,
  not when exactly the cap matched.
- In the frame, an open run ends at its own `observed_at`, not at the
  oldest successful fetch over all roots.
- Open runs whose events could not be read have `activity_error`, no age
  and `possibly_stalled: false`, and the frame state `activity unknown`.

After the electrochemistry trial (2026-11-16):

- `/selection/...` with a `uid` this Tiled does not have answers with a
  message (`found: false`) instead of `404`; `/selection/run` has the new
  field `found`. `/runs/{uid}` is unchanged.
- A stream without a table yet (events still in `TiledWriter`'s batch) has
  no rows: `events: 0`, no activity error, instead of `502`/`activity_error:
  not_found`.
- In the frame, sub-lanes are allocated by true extent, and a short clip is
  widened only up to the next clip in its sub-lane: runs queued back to
  back no longer move to a sub-lane at coarse zoom.
- The generic fallback views of a run without a `primary` stream use the
  first stream with hinted numeric fields, and `baseline` only when there
  is no other stream; before, they used the first stream by name. View
  names are unchanged.
- View table: new entry `electrochemistry-k2460` (labels, stall threshold;
  its views are the fallback's).

After the first look at the lab Tiled (2026-11-17):

- `/selection/run` has the new fields `metadata` and `metadata_truncated`,
  the same as `/runs/{uid}` (empty when nothing is selected or the run is
  not found), for the dashboard's *Run metadata* panel. Additive: existing
  fields are unchanged.
