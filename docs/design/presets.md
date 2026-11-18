# Presets and layout (step 8): design

Status: draft 4, 2026-11-18: after Codex review 07
([response](reviews/response-07-presets.md),
[disposition](reviews/disposition-07.md)), two maintainer decisions (§15:
today's timeline becomes an ordinary preset; one lane can be unfolded into
its streams) and follow-up review 08
([response](reviews/response-08-presets-follow-up.md),
[disposition](reviews/disposition-08.md)). Built: presets, lanes and endpoints
(parts 1–2), the dashboard picker, links and unfolding (part 3), and the
first layout variant, the *Overview* dashboard (part 4). Plan §9 step 8:
let a deployment choose which runs the timeline shows, how they are grouped
into lanes and labelled, and how the dashboard is laid out, without code
changes. Starting point: the earlier configuration contract,
`bluesky-web-plotting/design_documentation/CONFIG_SPEC.md` v1 (plan §2). The
maintainer's decisions are in §15; what is deferred is in §14.

Two things are versioned separately: the **preset format** (the YAML files a
deployment writes; §3–§6) and the **endpoint contract** (`endpoints.md`; §10).

## 1. What carries over from CONFIG_SPEC v1

| CONFIG_SPEC v1 | Here |
|---|---|
| `version`, unknown operators rejected | Kept, stricter: unknown keys are rejected at every level (CONFIG_SPEC ignored unknown top-level keys) |
| `sources.live` (ZeroMQ) | Dropped (plan §2) |
| `sources.tiled` (URI, root, polling, lookback) | Deployment settings (`ETT_*`), never in a preset. A preset may narrow the configured roots (`roots`), never add any |
| `timeline` (window, track order, minimum clip duration) | `lanes.order`, `lanes.min_lanes`, `lanes.max_lanes`; the time range stays a dashboard setting |
| `tracks`: `where`, `group_by`, `label`, `require_streams` | Kept, with typed rules (§4–§6); `group_by` may name several fields |
| `label: {title: {template}, subtitle: …}` | One template, a string or a list of parts (§6.4); CONFIG_SPEC label objects do not load |
| Bare predicate arrays in examples (`where: [...]`) | Rejected: a predicate is a leaf, `all`, `any` or `not` (CONFIG_SPEC's grammar, not its examples) |
| Field paths over `uid`, `start`, `stop`, `streams` | Kept, plus `root`, `role` and `status`; `uid` is the Tiled run key (§5.1) |
| Predicate operators and relative times | Kept, with a truth table (§5) |
| `on_select`, `panels` (Dash panes) | Replaced by the view table and generated dashboards (§11) |
| Selection and cursor as runtime state | Already so: dashboard variables and Grafana's shared crosshair |

## 2. The registry

- Preset files are YAML (§3), one preset per file, in `ETT_PRESETS_DIR`. The
  adapter loads all of them at start, as one registry, or refuses to start
  with a message naming the file, line, column and schema path (as invalid
  settings do now). A preset's identity is its `id`, not its file name;
  duplicate ids across files are errors.
- **`instruments`** is an ordinary preset file shipped inside the package
  (§4.1; package data, loaded from the installed package, not the checkout),
  read by the same loader and validator as deployment files. Its id is
  reserved: deployments add presets, they do not replace it (a copy under
  another id can change anything). There is one lane mechanism: today's fixed
  lane key and naming code (`clips.lane_key`, `lane_names`) is replaced by
  presets. Nobody uses today's lane names and order yet (maintainer,
  2026-11-18), so they change where the format gives a simpler rule (§4.1);
  every changed endpoint meaning is recorded in `migrations.md` when built.
- **The default preset** is `ETT_DEFAULT_PRESET` (default `instruments`):
  used when a request has no `preset`, listed first by `/presets` (then the
  others by title), and so the picker's initial value (§11).
- The registry has a revision (a digest of the loaded files), reported with
  results for diagnostics and part of every cache key that depends on it.
- An unknown or empty `preset` argument is a `422`, never a silent fall-back
  to the default.

## 3. Files: a YAML subset

The loader accepts a subset of YAML with JSON's scalar types, so that a
preset means what its author wrote (PyYAML 6.0.3's `safe_load` alone turns
`no` into false, `2026-11-18` into a date and `012` into ten):

- Only lowercase `true`, `false`, `null` and JSON number syntax are typed;
  everything else is a string (`no`, `on`, `off`, `yes`, dates stay text).
- Rejected, with a hint to quote: bare `~`, numbers with leading zeros,
  `.nan`/`.inf` spellings.
- Rejected outright: duplicate keys, non-string keys, several documents,
  explicit tags, anchors, aliases and merge keys.
- Limits checked while parsing, before validation: 256 KiB per file,
  nesting depth 32, 10,000 nodes, 32 tracks, 1,000 predicate nodes, 1,024
  characters per regular expression, 64 files. Proposed budgets, documented
  and tested; not capacity claims.
- A dedicated loader class; PyYAML's global resolvers are not changed.

## 4. Preset format v1

```yaml
version: 1
id: beamline-by-sample
title: Beamline by sample
roots: [acquisition]          # optional: a subset of ETT_CATALOG_ROOTS
scope:                        # optional: candidates fetched from Tiled (§7)
  - {field: start.beamline, op: in, value: [beamline-a]}
tracks:
  - id: measurements
    name: Measurements
    where:
      all:
        - {field: role, op: eq, value: acquisition}
        - {field: start.plan_name, op: regex, value: '^measure_'}
    require_streams: [primary]
    group_by: {field: start.sample_id, fallback: '(no sample)'}
    label: ['{start.sample_id}', '{start.plan_name}']
  - id: failures
    name: Failed or aborted
    where: {field: status, op: in, value: [failed, aborted]}
    group_by:
      fields: [start.beamline, start.station]
lanes:
  order: [measurements, failures]
  min_lanes: 4
  max_lanes: 100
```

- **Required:** `version` (the integer `1`; `true` or `1.0` are rejected),
  `id`, `title`, `tracks` (non-empty). Each track: `id`, `name`.
- **Identities** (`id` of presets and tracks): 1–64 characters, a lowercase
  letter, then lowercase letters, digits or hyphens. Titles and names are
  display text.
- **Optional, with defaults:** `roots` = all configured roots (a root not
  configured is an error); `scope` = none; `where` = always true;
  `require_streams` = `[]`; `group_by` = one lane per track; `label` = the
  view table's label; `lanes.order` = file order; `lanes.min_lanes` = 0;
  `lanes.max_lanes` = the deployment's budget (it can only be lowered). An
  explicit `null` is not the same as an omitted key: it is an error.
- **`group_by`:** exactly one of `field` or `fields` (an ordered tuple), and
  optionally: `name`, a template for the lane's name (§6.4) using only the
  group's own fields; `fallback`, the text shown for a missing component
  (default `(unknown)`); and `invalid`, what a list or mapping value of a
  component means: `exclude` (default: the run is left out of the track, and
  reported) or `missing` (it counts as missing, and is reported).
- **`lane_prefix`** (per track, default `true`): lane names start with the
  track's `name`. A preset with one track usually turns it off.
- **`lanes.order`:** a partial order of track ids; unknown or repeated ids are
  errors; tracks not listed follow in file order.
- A track's runs: those in the preset's roots that pass `scope`, `where` and
  `require_streams`. A run appears in every track it matches (§15).
- Changing the meaning of any v1 key needs `version: 2` and a migration note;
  adding an optional key must leave v1 files meaning what they meant.

### 4.1 The default preset

Shipped as `src/event_timeline_tiled/presets/instruments.yaml`:

```yaml
version: 1
id: instruments
title: Instruments
tracks:
  - id: runs
    name: Runs
    lane_prefix: false
    group_by:
      fields: [root, start.facility, start.instrument, start.station, role]
      name: ['{start.instrument|(unknown)}', '{start.station}', '{role}']
      invalid: missing
```

One lane per catalogue root, facility, instrument, station and role. Groups
sort component by component (§6.2), so within one root, facility,
instrument and station the acquisition lane comes right before the
processing lane, without special code (not across roots: acquisition and
processing in different roots are different lanes). What changes from
today, on purpose:

| Today | With the default preset |
|---|---|
| `MOUSE`, `MOUSE · processing` | `MOUSE · acquisition`, `MOUSE · processing` |
| Runs without an instrument: a lane named after their catalogue root, `(unknown)` at the server root | `(unknown) · acquisition`; told apart across roots by ` (2)`, the tooltip naming the root |
| One lane (and trend peers) for an instrument across all configured roots | One lane per root: the root is part of the key |
| The facility added to a name only when two instruments share it | The facility is in the key, not the name; equal names get ` (2)`, the tooltip naming the facility |
| A list or mapping as `instrument`, `facility` or `station`: treated as missing | The same (`invalid: missing`), now also reported |
| `/clips` `lane_key`: `facility/instrument/station/role`; run summaries' `lane` from it | §10 |

## 5. Evaluation

### 5.1 Context and field paths

| Path | Value |
|---|---|
| `uid` | the run's key in Tiled (as `/clips`), not `start.uid` |
| `root` | the catalogue root the run was found under (`""` at the server root) |
| `start.<key>…`, `stop.<key>…` | the documents; `stop` is missing while open |
| `role` | `acquisition` or `processing` (`run_state.role_of`) |
| `status` | `success`, `failed`, `aborted`, `unknown`, `open` (`run_state.status_of`; note `failed`/`aborted`, while `stop.exit_status` says `fail`/`abort`) |
| `streams` | the run's stream names (§5.4), sorted, as a list |

Paths are dot-separated keys through mappings only: no list indices, no
traversal through lists. A key that itself contains a dot cannot be named in
v1 (a later, additive path form can). Every path is checked at load time
against these roots.

### 5.2 Predicates

A predicate is exactly one of: a leaf `{field, op, value?}`, `{all: [...]}`,
`{any: [...]}` or `{not: P}`; extra keys are errors. Empty `all` is true,
empty `any` false.

Values are compared by type category: **numbers** (integers and floats
together: `1` equals `1.0`; finite only), **booleans** (never numbers),
**strings**, **null**, **lists** and **mappings** (equal element by element,
recursively, by the same rules). Missing and `null` are the same to
predicates.

| Operator | Value | True when |
|---|---|---|
| `eq`, `neq` | any non-null JSON | equal by the rules above; `neq` is `not eq`, so missing is `neq` anything |
| `in`, `nin` | a list of non-null values | the **whole** value equals a member (no element overlap); empty list: `in` false, `nin` true |
| `gt`, `gte`, `lt`, `lte` | a finite number or a relative time | both are finite numbers (booleans excluded); otherwise false |
| `exists` / `missing` | none | present and not null / absent or null |
| `not_empty` | none | a non-empty string or list (`0`, `false`, `{}` are not) |
| `contains`, `starts_with`, `ends_with` | a string | the value is a string with that substring, prefix, suffix |
| `regex` | a string | the value is a string and the pattern matches somewhere (`re.search`, no flags) |

A `null` operand is rejected at load time, with advice to use `missing`.
`not` simply negates, so `not {field: stop.exit_status, op: eq, value:
success}` is true for open runs.

### 5.3 Relative times and regular expressions

- Relative operands: `{relative: "-24h"}`, an integer and one of `s`, `m`,
  `h`, `d`; only with the ordering operators; never in `scope`.
- One `evaluated_at` (UTC epoch seconds) per request resolves every relative
  operand, and predicates are re-evaluated on cached records, never cached
  as results. The open-run horizon stays anchored to the window's end.
  Responses report `evaluated_at` and the registry revision. A relative
  predicate can change what a historical range shows on refresh; absolute
  times are reproducible.
- Patterns are compiled at load time; an invalid pattern stops the load.
  Preset files are trusted deployment configuration: the length cap is not
  a bound on matching time, and patterns never come from the browser.

### 5.4 Streams

`streams` are the names of the run's streams as created by descriptors,
whether or not they have events, a table or a plottable view.

- Closed run: the keys of `stop.num_events`. Checked (review 07) for
  Bluesky 1.14.6's RunEngine through `TiledWriter` on Tiled 0.2.21: a stream
  without events is listed with `0`. Another writer may not provide it.
- Open run, or a closed run without a usable `num_events`: the stream list
  from Tiled, read at most once per activity interval and cached with the
  run's activity, shared by every track and panel.
- Unknown streams (not yet created, or not readable now) fail
  `require_streams` (CONFIG_SPEC §12's strict choice) and are reported; a
  failed read is never taken as "no such stream". `require_streams` is
  all-of; an empty list passes.

## 6. Lanes and placements

### 6.1 Runs and placements

A **run** is a unique `(root, uid)`. A **placement** is a run in one logical
lane: one per matching `(track, group)`. Counts of runs (`/clips/status.runs`,
activity errors, trend reads) stay counts of unique runs; placements and
lanes get counts of their own. Activity and streams are read once per run,
not per placement.

### 6.2 Logical lanes

- **Key:** preset id, track id and the typed group value (or tuple), with a
  distinct "missing" marker for absent, `null` or empty-string components.
  `fallback` only changes how a missing component is shown: a missing value
  and the literal text `(unknown)` stay different lanes.
- **Group values:** strings, numbers (normalised as in §5.2: `1` and `1.0`
  are one group), booleans. A list or mapping follows `group_by.invalid`
  (§4); it is never fanned out (unfolding is a separate feature, §6.5).
- **Order:** tracks by `lanes.order`; groups within a track component by
  component (tuples lexicographically), each component ordered by type
  category (booleans, numbers, strings) and then value, missing last.
  Stable across refreshes and independent of other lanes.
- **Base name:** `<track name> · <group name>` (without the track name if
  `lane_prefix` is false). The group name is `group_by.name` rendered from
  the lane's canonical group components (never a run's raw values), or else
  the components joined with ` · ` (`fallback` for a missing one); if that
  is empty too, `(unknown)`. The base name depends on the key alone.
- **Final names:** one allocator gives every row of a frame (logical lanes,
  stream rows of an unfolded lane, overlap sub-lanes, padding) a unique
  display name, reserving names as they are before adding ` (2)`, ` /2`
  suffixes. Grafana partitions by that name, so two rows never share one.
  Suffixes depend on what else is in the window; links and variables always
  use ids, never names. A base name is cut to 180 characters (ending in `…`)
  so suffixes fit within 200.
- **Sub-lanes:** as today, per logical lane: true-extent overlap allocation
  (ties by start, root, uid), short-clip widening.

### 6.3 Budgets

Deployment settings bound tracks, logical lanes, final sub-lanes and
placements per request; `lanes.max_lanes` can lower the lane budget. Going
over is a reported failure (`/clips/status` says so and advises narrowing the
grouping), never silent truncation and never a frame marked complete. Folding
the excess into "other" is deferred (§14). `min_lanes` counts final
sub-lanes; the query parameter (0–20) overrides the preset's value.

### 6.4 Templates: labels and lane names

A clip `label` and a lane `group_by.name` use one template language:

- A template is a string or a list of strings (**parts**). A list is
  rendered part by part; each part is trimmed, empty parts are dropped, and
  the rest are joined with ` · `. That is the only clean-up: values are
  inserted as they are, so a ` · ` inside metadata stays as written.
- A part is literal text with placeholders: `{path}` or `{path|default}`.
  Paths as §5.1, checked at load time; a lane name may only use the group's
  own fields. `default` is literal text used when the value is missing,
  `null`, empty or only white space; it may not contain `{`, `}` or `|`
  (rejected at load time). `{{` and `}}` are literal braces; any other `{`,
  `}` or `|` outside a placeholder is rejected. No expressions,
  conversions or attribute access.
- Values: strings as they are; numbers and booleans as JSON; a lane name
  uses the canonical component (so `1` and `1.0` render alike); anything
  else in a label is deterministic JSON. A missing value without a default
  renders as nothing.
- At most 200 characters for a label (cut, ending in `…`); lane names as
  §6.2. An empty label falls back to the view table's label; an empty lane
  name as §6.2.
- A template only changes labels in that preset's lanes, not run details.
  Labels and names are plain text in responses: escaped at an HTML sink,
  percent-encoded in URLs, never interpreted again.

### 6.5 Unfolding a lane into its streams

One logical lane at a time can be **unfolded**: its runs are then drawn once
per stream, on rows per stream, each from the stream's first to its last
event. This shows when each stream recorded within a run: for MOUSE, the
baseline at the start and end and the detector's measurement span; for
electrochemistry, the readout against the sequence events. The span is an
event-time span (the earliest to the latest event), not a duty cycle: a
baseline with readings at the start and the end draws as one bar between
them.

**Request.** `unfold=<lane id>` on `/clips/frame` and `/clips/status`, which
the dashboard passes to both, with the same `preset` and range. Absent or
empty means folded. A lane id that is not in this preset's window is a
notice in the status with the ordinary frame, not an error.

**Rows.** Only that logical lane changes; the same run's placements in other
tracks stay folded. Its runs (the latest `ETT_UNFOLD_MAX_RUNS` in the window,
default 50) are replaced by stream rows, grouped by stream name in name
order, each with its own overlap sub-lanes (§6.2). The lane's older runs stay
on one folded row, `<lane> · earlier runs`, so nothing disappears; the status
says how many. A run whose streams are all empty or unreadable keeps a folded
bar on that row too. Every row counts against the frame's budgets (§6.3);
`min_lanes` padding comes after.

**Geometry.** A stream row's `start` and `stop` are the stream's earliest
and latest finite event time, also for an open run (never extended to "now"
or to the observation time); a single event is a zero-length span, widened
for visibility only. Overlaps are allocated on the true extents. Rows keep
the run's `uid`, `root`, status, activity and freshness as separate fields,
add `stream` and the true stream extent, and keep the run's own extent for
"Zoom to run"; the tooltip says the outcome is the run's.

**Streams and errors.** Every stream of the run is a candidate, including
streams with only external fields (processing results): their `internal`
table holds `seq_num` and `time` (review 08 read them). A stream declared
with zero events has no row and is counted as empty; one whose table is not
there yet (open run, before its first flush) is pending; one that fails or
lacks rows that its stop document reports is unreadable. Empty, pending,
invalid and unreadable are counted separately and never drawn as measured
spans.

**Cost.** Tiled 0.2.21 cannot select rows or compute a minimum and maximum
(review 08), so each read transfers a stream's whole `time` column and the
server's work cannot be bounded by the adapter, only refused. The adapter
bounds what it does: at most `ETT_UNFOLD_MAX_RUNS` runs, `ETT_UNFOLD_MAX_STREAMS`
streams per run (default 16), `ETT_UNFOLD_MAX_EVENTS` events per stream (default
200,000; a closed run's `stop.num_events` refuses a larger stream before
reading it, and an open stream's read stops at that size) and a time budget
per request; reads of one run are shared between concurrent requests (frame
and status) and throttled per run, so refreshes and several viewers cannot
multiply them. What is refused is reported as such. A cheap minimum/maximum
read upstream in Tiled would remove the limit; until then it is stated.

**Caching.** An open run's extents are re-read at most once per activity
interval. When a run is first seen closed, a fresh complete read replaces
any open observation, whatever its age; only that read is kept as final,
keyed by `(root, uid)` and the stop document, and only if each stream's rows
match its count in `stop.num_events`. Errors and incomplete reads are not
kept; they are retried under the throttle, and the last good extents are
shown as stale meanwhile. This assumes, as checked for Bluesky 1.14.6's
`TiledWriter`, that a run's data is complete when its stop document is
written; another producer that backfills later needs a revalidation interval
(a setting). The cache is bounded in entries and size.

**Status.** `/clips/status` with `unfold` reports the lane (or that it is not
in the window), runs unfolded and left folded, streams drawn, empty,
pending, invalid, unreadable and refused, stale extents, and which budget
applied; these are completeness issues, not run failures, and never change
a run's status or `/health`.

Unfolding several lanes, or every lane (a per-stream preset), stays
deferred: its cost grows with the whole window (§14).

## 7. Scope: what Tiled filters

All of `scope`, `where` and `require_streams` are evaluated in the adapter;
that evaluation is authoritative. `scope` is also sent to Tiled, to shrink
the window searches, as a **candidate superset** that the adapter re-checks:

- Allowed in `scope`: `eq` and `in` on `start.<key>`, with non-null scalar
  operands; an `in` list of one type (integers and floats count as one).
  Objects, lists, nested lists, `null` and relative operands are rejected.
- Sent as Tiled `Eq`/`In` only where a superset is proven: Tiled 0.2.21 on
  SQLite and PostgreSQL (review 07's probes: SQLite's `Eq(1)` also matches
  `true` and `1.0`, PostgreSQL's matches `1.0`; neither matches a missing
  key; scalar operands do not match values inside lists). Elsewhere (Tiled
  0.2.7, untested servers) the window is searched without them. The choice
  lives in `layout.py`, by server version.
- `neq`, `nin`, ordering operators and `regex` are never sent to Tiled.

Search caps apply to candidates: if a root's candidates are truncated, the
result is incomplete even when few runs match, and "latest" means latest
among the candidates read (§9).

## 8. Caches and source health

- Window coverage and freshness are tracked per root, window and normalised
  scope (and roots), so preset B's success never certifies preset A's runs,
  and a stale fall-back re-applies A's own scope and tracks to A's own
  records, with their observation times.
- One observation per run, shared by every preset, track, panel and the frame
  and status of one refresh: activity, stream names, stream extents (§6.5),
  their times and errors. Collecting extents of external-only streams does
  not change which events define activity (today's rule stays).
- Source health stays separate from membership: a run that matches no track
  is not a failure.

## 9. Counts and truncation

`/clips/status` keeps `runs` as unique runs and adds candidate, matched-run,
placement and lane counts, and states which cap applied: the root search
(`ETT_MAX_RUNS`), a lane or placement budget, or the trend's read cap.

## 10. Endpoints

- **`preset`** (default `ETT_DEFAULT_PRESET`) on `/clips`, `/clips/frame`,
  `/clips/status`, `/runs/{uid}/trend`, `/selection/trend` and the new
  `/selection/runs`; **`unfold`** on `/clips/frame` and `/clips/status`.
  Neither on `/health`, `/queues`, `/presets`, or a run's details, streams,
  fields, data, related runs, curve or image: a failures-only preset must not
  hide the selected measurement's result.
- **Run identity:** a run is `(root, uid)`. Every row that can be selected
  carries `root`, and run endpoints and `/selection/...` take an optional
  `root`. Without it (old bookmarks, direct links), the run is looked up as
  now; if the same key exists under two configured roots, the answer says it
  is ambiguous instead of picking one.
- **`/clips`** keeps one row per run, with `placements`: preset, track, lane
  id, lane name, label. `lane_key` and `lane` become the run's first
  placement in `lanes.order` (its id and name); `label` stays the view
  table's run label. Recorded in `migrations.md`.
- **Run summaries** (`/runs/{uid}`, `/selection/run`): `lane` is the run's
  base lane name in the default preset (not a window's disambiguated name).
- **`/clips/frame`** keeps its final `lane` column and fields and adds hidden
  helpers: `lane_id`, `track`, `root`, and for an unfolded lane `stream` and
  the true stream extent (§6.5).
- **`/selection/runs?preset&lane&root&uid&from&to&limit`**: the latest runs on
  the selected lane, each once, newest first; with no lane, the preset's runs
  in the window. With a run but no lane (a direct link), its first lane in
  `lanes.order`, said in the notice; a lane not in this preset is a notice,
  never another lane. Unfolding does not change it.
- **The trend** (`/runs/{uid}/trend`, `/selection/trend`) takes `lane`: its
  "runs like it" are that lane's runs (not a sub-lane or stream row), with
  today's checks of role, plan, view, stream and units, each peer read once;
  without `lane`, the run's first lane in `lanes.order`, said in the notice.
  With the default preset this differs from today where roots differ (§4.1).
- Recorded in `endpoints.md` and `migrations.md` when built.

## 11. Dashboards

- **Picker:** an Infinity query variable (`queryType: infinity`, backend
  parser, table format, `meta: {textField: title, valueField: id}`) on
  `/presets`; single value, no All or custom values, URL-synced, refreshed
  on load, with no fixed initial value, so it starts at `/presets`' first
  row, the deployment's default (§2) (Infinity 4.1.6, Grafana 13.3.1;
  review 07).
- **Hidden, URL-synced variables:** `run`, `root` (the run's catalogue root),
  `lane` (the clicked logical lane), `unfold` and `stream`. On a preset
  change the lane is re-resolved or reported, never silently replaced.
- **Links:** the generator builds every navigation from one transition table,
  giving each variable exactly once, either kept (`${var:queryparam}`) or
  replaced from the clicked row (`${__data.fields["lane_id"]:percentencode}`,
  never a display name), with the target dashboard's UID as a parameter:

  | Action | Run, root, lane | Stream | Unfold | Time range |
  |---|---|---|---|---|
  | Unfold lane into streams | kept | kept | the clicked lane | kept |
  | Fold (on a clip, and in the dashboard header) | kept | kept | cleared | kept |
  | Select run (a run or stream bar) | the clicked run and its lane | the clicked stream, else cleared | kept | kept |
  | Zoom to run | the clicked run and its lane | as Select | kept | the run's extent, padded |
  | A stream in the streams table | kept | the clicked stream | kept | kept |

  A data link cannot be hidden per row in Grafana 13.3.1 (review 08), so a
  clip always offers both Unfold and Fold; both are idempotent. The header's
  Fold link works when an unfolded lane draws nothing. Helper columns
  (`lane_id`, `track`, `root`, `stream`, extents) are declared and hidden, so
  they never become timeline rows.
- **Queries:** the frame and Data status get the same `preset`, range and
  `unfold`; lane-dependent queries get `preset` and `lane`; run detail
  queries get `uid` and `root` only.
- **Layouts:** the current Timeline stays as the full view. The first
  variant (§15) is static rows built by the generator:
  1. queue (several managers, unreachable states) and data status, with
     the help line, the Health link and Fold;
  2. the timeline;
  3. the trend of the selected lane (title naming the lane);
  4. three columns: the latest runs on the selected lane (`/selection/runs`,
     the window's runs before a selection); the selected run's streams; its
     summary (state, duration, reason, activity) above its metadata;
  5. the selected run's time series beside its corrected curve, each with a
     notice when it has none.

  Fixed panels leave empty space when they have nothing to show; failure
  reasons, images and source health remain in the full Timeline.
- Grafana 13.3.1's dynamic dashboards (tabs, auto grid, show/hide rules) are
  generally available and can be file-provisioned in the Kubernetes-style
  resource format (review 07; this corrects the earlier note with D-009).
  The first variant stays static by choice, not necessity; a dynamic variant
  would need its own provisioning and smoke tests.
- Tested on the pinned stack in a browser: URL round trips of every
  transition, clearing `unfold` and `stream`, special characters in ids, and
  the variant's own dashboard UID.

## 12. Code

`presets.py`: the loader (YAML subset, limits), the validated model (attrs
with validators), predicate evaluation, templates and placements.
`layout.py`: Tiled queries, including `scope` compilation by server version,
stream names, and a stream-time read for unfolding (the `time` column only,
no sorting, any stream including external-only ones, within the event
budget); `StreamInfo.has_table` is renamed for what it means (the stream has
scalar fields), since it says nothing about whether a table exists.
`run_state.py`: role and status. `timeline.py`: observations and coverage per
scope, and stream extents for unfolding. `clips.py`: display extents and
sub-lanes. `presets/`: the default preset file (§4.1); `clips.lane_key`
and `lane_names` go. The adapter assembles responses; the generator lays out
dashboards. No code tests an instrument name; presets name metadata and
streams as data. Nothing here writes to Tiled or controls a queue.

## 13. Tests

The YAML subset (every scalar of §3, nested values, duplicates, limits,
line/column errors); validation (required keys, ids, `null`, reserved and
duplicate ids, roots); the truth table of §5.2 including `not` over missing;
relative times against one `evaluated_at`; streams for open, closed,
zero-event, external-only and unreadable cases; lane keys (missing versus
`(unknown)`, `1` versus `"1"`), names (templates with defaults, collapsed
separators, ` (2)`), order and budgets; the default preset's lanes for the
cases today's code handles (missing instruments, several roots, `-` in
values, equal names in two facilities, processing below acquisition,
sub-lanes, padding, list-valued identity fields), compared with §4.1's
table; templates (defaults containing separators, `|` and braces rejected,
` · ` inside values kept, all-empty names, `1` and `1.0` in one group);
final-name allocation (a logical lane named like another lane's stream row,
` /2` against a stream named `x /2`); unfolding (extents from unsorted event
times, zero-event, pending, external-only and unreadable streams, an open
run's span not extended, the folded remainder row, an all-empty lane, a run
in two tracks unfolded in one, overlaps in one stream, each budget, the
open-to-closed transition within one activity interval, frame and status
sharing reads); the same run key in two roots (selection, Zoom and stream
navigation from the second root); the default preset loaded from an
installed wheel;
`scope` pushdown as a superset on SQLite and PostgreSQL for the operand
matrix of review 07, and a selective track beyond the candidate cap; cache
coverage (fetch A, fetch B, fail A); the picker, lane and links in the
generated JSON and in a browser on the demo stack, under Docker and Podman.

## 14. Deferred

Unfolding several lanes at once, or a per-stream preset for every lane
(plan §9 step 8's "lanes per event stream" beyond one lane; §6.5); list or
mapping group values; field paths for keys containing dots; folding excess
groups into "other"; any-of stream requirements; presets edited in the
browser; a dynamic-dashboard variant; generalising the processing-role
classifier and the view table for other facilities (separate contracts).

## 15. Decisions

Maintainer, 2026-11-18:

1. Preset files are YAML (the subset of §3).
2. The preset is chosen on the dashboard, by a variable.
3. A run appears in every track it matches.
4. First dashboard variant: generic to specific (§11).

After review 07: the format stays a draft until built and tested; review 07's
proposed format is adopted with the changes recorded in the disposition.

Maintainer, 2026-11-18, after review 07:

5. Today's lane names and order may change: nobody uses them yet. So
   `instruments` is an ordinary preset file (§4.1), not a built-in strategy,
   and there is one lane mechanism. (This reverses the disposition of review
   07's finding 2, whose premise was that today's output must be kept.)
6. One lane can be unfolded into a sub-lane per stream (§6.5), in step 8.

After review 08 (disposition 08): all findings accepted; the design is draft
4. Its main additions: unfolding's budgets, geometry, caching and status;
runs identified by root and uid in selections; one template language with
parts; one allocator for final names; `group_by.invalid`; a deployment
default preset.
