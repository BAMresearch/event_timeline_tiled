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
- New endpoints `/runs/{uid}/trend` and `/selection/trend`: the run's time
  series over the runs like it in a window. Additive.
- View table: MOUSE measurements gain the time series views `hinted` and
  `numeric` of `mouse_eiger_measure` (same meaning as the fallback's views of
  those names). Their default view is still `readings`; `kind=timeseries`
  and the trend now find a view for them instead of none.
- `/clips/frame` has the new parameter `min_lanes` (default 0: unchanged).
  With it, the frame may end with empty lanes whose fields are `null`
  except `lane`, `start` and `stop`; consumers that pass it must not expect
  a `uid` on every row.

Step 6, corrected views (2026-11-17):

- New endpoints `/runs/{uid}/related`, `/runs/{uid}/curve`,
  `/runs/{uid}/image` (also `/run-images/{uid}/image`), `/selection/related`
  and `/selection/curve`. Additive.
- New view kinds `curve` and `image`, and the view names `corrected_curve`
  and `corrected_image`. View table: MOUSE processing runs gain these
  views, so their default view changes from `summary` (under the fallback's
  unavailable `no_data`) to `corrected_curve`, and `/runs/{uid}/data`
  without `view` returns curve rows (`x`, `y`, `y_lower`, `y_upper`) for
  them. Consumers that expected metadata rows from a processing run's
  default view must ask for `view=summary`. Processing runs that published
  no output are unchanged.
- `/runs/{uid}/data?kind=` accepts `curve` and `image`.

After review 06 (2026-11-17):

- Metadata rows (`/runs/{uid}`, `/selection/run`, the `summary` view) end in
  a `(cut)` row whenever `metadata_truncated` is true, and depth cuts now set
  `metadata_truncated` too. Consumers that count rows must skip that row.
- A finished run whose stop document reports events for a stream whose table
  Tiled does not have is an error (`502`, or `unreadable` in a trend), no
  longer an empty stream.
- `/related`: new row field `problems`; with more processing runs than the
  cap, nothing is marked latest and `default` is `null`. `/curve`: new fields
  `source.family` and `uncertainty_invalid`.

Queue progress (2026-11-18):

- `/queues` has the new fields `run_active`, `run_done`, `run_total`,
  `run_progress`, `run_text`, `run_since` and `run_from_start`. Additive.

Presets, step 8 part 2 (2026-11-18): the fixed lane key is replaced by
presets (docs/design/presets.md); nobody used the old names yet.

- Lane names come from the default preset `instruments`: `MOUSE` is now
  `MOUSE · acquisition`, `MOUSE · processing` stays, `generic A` is
  `generic · A · acquisition`; runs without an instrument are
  `(unknown) · acquisition` instead of a lane named after their catalogue
  root. The facility is no longer added to a name; equal names get ` (2)`.
- Each catalogue root has its own lanes (the root is part of the key), so an
  instrument's lane, and its trend's peers, no longer span roots.
- `/clips`: `lane_key` is the run's first placement's lane id
  (`<preset>.<track>.<digest>`), no longer `facility/instrument/station/role`;
  new `placements`, `preset`, `preset_revision`, `evaluated_at`; with a
  preset, only the runs it places are listed.
- `/clips/frame`: one row per placement; new hidden fields `lane_id`,
  `track`, `root`; `min_lanes` defaults to the preset's.
- `/clips/status`: new counts (`matched_runs`, `placements`, `lanes`, …)
  and issues for budgets.
- Run summaries' `lane` is the run's base lane name in the default preset.
- The trend's "runs like it" are a preset lane's runs (`lane`, else the run's
  first lane).
- Run lookups take `root`; a key under several roots is `409` instead of the
  first root's run.
- New: `/presets`, `/selection/runs`, `preset` and `lane` parameters, and the
  settings `ETT_PRESETS_DIR`, `ETT_DEFAULT_PRESET`, `ETT_MAX_LANES`,
  `ETT_MAX_SUBLANES`, `ETT_MAX_PLACEMENTS`.
