# Presets and layout (step 8): design

Status: draft 2, 2026-11-18, after Codex review 07
([response](reviews/response-07-presets.md),
[disposition](reviews/disposition-07.md)). Not yet built. Plan §9 step 8:
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
| `label: {title: {template}, subtitle: …}` | One template string (§6.4); CONFIG_SPEC label objects do not load |
| Bare predicate arrays in examples (`where: [...]`) | Rejected: a predicate is a leaf, `all`, `any` or `not` (CONFIG_SPEC's grammar, not its examples) |
| Field paths over `uid`, `start`, `stop`, `streams` | Kept, plus `role` and `status`; `uid` is the Tiled run key (§5.1) |
| Predicate operators and relative times | Kept, with a truth table (§5) |
| `on_select`, `panels` (Dash panes) | Replaced by the view table and generated dashboards (§11) |
| Selection and cursor as runtime state | Already so: dashboard variables and Grafana's shared crosshair |

## 2. The registry

- Preset files are YAML (§3), one preset per file, in `ETT_PRESETS_DIR`. The
  adapter loads all of them at start, as one registry, or refuses to start
  with a message naming the file, line, column and schema path (as invalid
  settings do now). A preset's identity is its `id`, not its file name;
  duplicate ids across files are errors.
- **`instruments`** is built in and reserved: it is today's timeline,
  implemented by today's code (`clips.lane_key`, `lane_names`, the lane
  order with each instrument's processing lane next to it, the root and
  `(unknown)` fallbacks, name disambiguation). No file may use that id, and it
  is not described as YAML tracks: the general format cannot express its
  composite key, fallbacks and ordering, and pretending would change today's
  names and order. Without `preset`, every endpoint behaves exactly as now.
- `/presets` lists `{id, title}` rows, `instruments` first, then by title. The
  registry has a revision (a digest of the loaded files), reported with
  results for diagnostics and part of every cache key that depends on it.
- An unknown or empty `preset` argument is a `422`, never a silent fall-back
  to `instruments`.

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
    label: '{start.sample_id} · {start.plan_name}'
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
  an optional `fallback` (display only; default `(unknown)`).
- **`lanes.order`:** a partial order of track ids; unknown or repeated ids are
  errors; tracks not listed follow in file order.
- A track's runs: those in the preset's roots that pass `scope`, `where` and
  `require_streams`. A run appears in every track it matches (§15).
- Changing the meaning of any v1 key needs `version: 2` and a migration note;
  adding an optional key must leave v1 files meaning what they meant.

## 5. Evaluation

### 5.1 Context and field paths

| Path | Value |
|---|---|
| `uid` | the run's key in Tiled (as `/clips`), not `start.uid` |
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
- **Group values:** strings, numbers (normalised as in §5.2), booleans.
  A list or mapping is not a v1 group value: the run is left out of that
  track and reported (a per-stream fan-out is deferred, §14).
- **Name:** `<track name> · <group>` (tuple components joined with ` · `),
  made unique as today (` (2)`), separate from the key.
- **Order:** tracks by `lanes.order`; groups within a track by type, then
  value, missing last. Stable across refreshes and independent of other
  lanes.
- **Sub-lanes:** as today, per logical lane: true-extent overlap allocation,
  short-clip widening.

### 6.3 Budgets

Deployment settings bound tracks, logical lanes, final sub-lanes and
placements per request; `lanes.max_lanes` can lower the lane budget. Going
over is a reported failure (`/clips/status` says so and advises narrowing the
grouping), never silent truncation and never a frame marked complete. Folding
the excess into "other" is deferred (§14). `min_lanes` counts final
sub-lanes; the query parameter (0–20) overrides the preset's value.

### 6.4 Labels

A label template is text with `{path}` placeholders (paths as §5.1, checked
at load time) and `{{`/`}}` for literal braces; no expressions, conversions
or attribute access. Missing or `null` gives empty text, other non-strings
deterministic JSON; at most 200 characters; an empty result falls back to the
view table's label. A template only changes labels in that preset's lanes,
not run details. Labels and names are plain text in responses: escaped at an
HTML sink, percent-encoded in URLs, never interpreted again.

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
- Per-run observations (activity, streams, errors) are shared by all presets.
- Source health stays separate from membership: a run that matches no track
  is not a failure.

## 9. Counts and truncation

`/clips/status` keeps `runs` as unique runs and adds candidate, matched-run,
placement and lane counts, and states which cap applied: the root search
(`ETT_MAX_RUNS`), a lane or placement budget, or the trend's read cap.

## 10. Endpoints

- `preset` (default `instruments`) on `/clips`, `/clips/frame`,
  `/clips/status`, `/runs/{uid}/trend`, `/selection/trend` and the new
  `/selection/runs`. Never on `/health`, `/queues`, `/presets`, or a run's
  details, streams, fields, data, related runs, curve or image: a
  failures-only preset must not hide the selected measurement's result.
- `/clips` keeps one row per run with today's fields and meanings (`lane_key`
  stays `facility/instrument/station/role`), plus `placements`: preset,
  track, lane key, lane name, label.
- `/clips/frame` keeps its final `lane` column and fields and adds hidden
  placement helpers (`lane_id`, `track`).
- `/selection/runs?preset&lane&uid&from&to&limit`: the latest runs on the
  selected lane, each once, newest first; with no lane, the preset's runs in
  the window. With a run but no lane (a direct link), its first lane in
  preset order, said in the notice; a lane that does not exist in this
  preset gives a notice, not another lane.
- The trend's "runs like it": the selected lane's runs (not sub-lane), with
  today's checks of role, plan, view, stream and units; each peer read once;
  no lane means no peer trend, with a notice. Without `preset`, as today.
- Recorded in `endpoints.md` and `migrations.md` when built.

## 11. Dashboards

- **Picker:** an Infinity query variable (`queryType: infinity`, backend
  parser, table format, `meta: {textField: title, valueField: id}`) on
  `/presets`; single value, no All or custom values, URL-synced, initial
  value `instruments`, refreshed on load (Infinity 4.1.6, Grafana 13.3.1;
  review 07).
- **Selection context:** a hidden, URL-synced `lane` variable next to `run`;
  "Select run" and "Zoom to run" set both. On a preset change the lane is
  re-resolved or reported, never silently replaced.
- **Links** carry `${preset:queryparam}` and the lane, and take the target
  dashboard's UID as a parameter in the generator (a variant cannot reuse
  links hard-coded to `ett-timeline`), without duplicating `var-run`.
- **Layouts:** the current Timeline stays as the full view. The first
  variant (§15) is static rows built by the generator:
  1. queue (several managers, unreachable states) and data status, with
     the help line and the Health link;
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

## 12. Code

`presets.py`: the loader (YAML subset, limits), the validated model (attrs
with validators), predicate evaluation and placements. `layout.py`: Tiled
queries, including `scope` compilation by server version, and stream names.
`run_state.py`: role and status. `timeline.py`: observations and coverage per
scope. `clips.py`: display extents and sub-lanes, and the built-in
`instruments` strategy. The adapter assembles responses; the generator lays
out dashboards. No code tests an instrument name; presets name metadata and
streams as data. Nothing here writes to Tiled or controls a queue.

## 13. Tests

The YAML subset (every scalar of §3, nested values, duplicates, limits,
line/column errors); validation (required keys, ids, `null`, reserved and
duplicate ids, roots); the truth table of §5.2 including `not` over missing;
relative times against one `evaluated_at`; streams for open, closed,
zero-event, external-only and unreadable cases; lane keys (missing versus
`(unknown)`, `1` versus `"1"`), names, order and budgets; `instruments`
reproducing today's `/clips`, frame and names exactly (missing instruments,
roots, `-`, colliding facilities, processing adjacency, sub-lanes, padding);
`scope` pushdown as a superset on SQLite and PostgreSQL for the operand
matrix of review 07, and a selective track beyond the candidate cap; cache
coverage (fetch A, fetch B, fail A); the picker, lane and links in the
generated JSON and in a browser on the demo stack, under Docker and Podman.

## 14. Deferred

Per-stream lane fan-out (plan §9 step 8's "lanes per event stream"); list or
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
