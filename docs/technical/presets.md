# Presets

A preset says which runs the timeline shows, how they are grouped into lanes
and how clips are labelled. This page is the reference for writing one. The
design and its reasons are in [presets and layout](../design/presets.md).

**Status:** presets decide the adapter's lanes (`preset=<id>` on the
endpoints, [endpoints](endpoints.md)) and are chosen with the *Preset* picker
on the Timeline and Overview dashboards. A lane can be unfolded into its
streams there.

## Files

One preset per file, `*.yaml` or `*.yml`, in the directory `ETT_PRESETS_DIR`
names (in the stack: `deploy/presets/`, or the host directory `ETT_PRESETS`
in `deploy/.env` names). `ETT_DEFAULT_PRESET` chooses the preset used when a
request names none. Budgets: `ETT_MAX_LANES` (100), `ETT_MAX_SUBLANES` (200)
and `ETT_MAX_PLACEMENTS` (20,000) per frame; more is left out and reported.
Unfolding a lane: `ETT_UNFOLD_MAX_RUNS` (50), `ETT_UNFOLD_MAX_STREAMS` (16),
`ETT_UNFOLD_MAX_EVENTS` (200,000), `ETT_UNFOLD_TIME_S` (10) and
`ETT_UNFOLD_REVALIDATE_S` (0: finished runs are not re-read). All files are
loaded at start; one bad file stops the adapter with its file name, line,
column and the path of the problem (`tracks[1].where.op`).
The package ships one preset, `instruments` (one lane per instrument and
role): its id is reserved, so copy it under another id to change it.
`deploy/presets/electrochemistry.yaml` is a deployment's preset: one lane
per electrochemistry station, with all its runs.

## Example

```yaml
version: 1
id: beamline-by-sample        # lowercase letters, digits, '-'
title: Beamline by sample
roots: [acquisition]          # optional: some of the configured catalogue roots
scope:                        # optional: only these runs are fetched from Tiled
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

A run appears in every track it matches: a failed measurement is in both
tracks above.

## Keys

| Key | Required | Meaning |
|---|---|---|
| `version` | yes | `1` |
| `id`, `title` | yes | identity (1–64 of `a-z`, `0-9`, `-`, starting with a letter) and display name |
| `tracks` | yes | 1–32 tracks; each needs `id` and `name` |
| `roots` | no | a non-empty subset of `ETT_CATALOG_ROOTS` |
| `scope` | no | a list of `eq`/`in` predicates on `start.<key>` with text, number or boolean operands (one type per `in` list); all must hold |
| `tracks[].where` | no | a predicate (below); default: every run |
| `tracks[].require_streams` | no | stream names the run must all have |
| `tracks[].group_by` | no | `field` or `fields` (a list), optional `name` (a template using only these fields), `fallback` (shown for a missing value; default `(unknown)`), `invalid` (`exclude`, the default, or `missing`: what a list or mapping value means) |
| `tracks[].label` | no | a template for clip labels; default: the instrument's label |
| `tracks[].lane_prefix` | no | `true` (default): lane names start with the track's name |
| `lanes.order` | no | track ids shown first, in this order; the rest follow in file order |
| `lanes.min_lanes` | no | 0–20 rows the timeline always has room for |
| `lanes.max_lanes` | no | a lower lane budget than the deployment's |

Unknown keys are errors, and so is `null` for a key: leave the key out.

## Field paths

`uid` (the run's key in Tiled), `root` (its catalogue root), `role`
(`acquisition`, `processing`), `status` (`success`, `failed`, `aborted`,
`unknown`, `open`: note `failed`, while `stop.exit_status` says `fail`),
`streams` (a list of stream names), and keys of the documents:
`start.plan_name`, `stop.exit_status`, `start.md.sample`. Paths go through
mappings only: no list indices. Missing and `null` are the same.

## Predicates

A predicate is one of: `{field, op, value}`, `{all: [...]}`, `{any: [...]}`,
`{not: ...}`. Empty `all` is true, empty `any` false.

| `op` | `value` | True when |
|---|---|---|
| `eq`, `neq` | anything but `null` | equal (`neq`: not equal; missing is `neq` everything) |
| `in`, `nin` | a list | the whole value equals a member (`nin`: equals none) |
| `gt`, `gte`, `lt`, `lte` | a number, or `{relative: '-24h'}` | both are finite numbers and compare so |
| `exists`, `missing` | (none) | present and not `null` / absent or `null` |
| `not_empty` | (none) | a non-empty text or list |
| `contains`, `starts_with`, `ends_with` | text | the value is text and contains, starts, ends with it |
| `regex` | text | the value is text and the pattern matches somewhere in it (Python `re.search`) |

Equality is typed: `1` equals `1.0`, `true` never equals `1`, `"1"` never
equals `1`; lists and mappings compare element by element. Relative times
(`s`, `m`, `h`, `d`) count from the moment of the request.

## Templates

Labels and lane names are a text or a list of texts. `{path}` inserts a
value, `{path|text}` inserts `text` when the value is missing or empty, and
`{{`/`}}` are literal braces; other `{`, `}` and `|` are errors. In a list,
each part is trimmed, empty parts are dropped and the rest joined with ` · `:

```yaml
name: ['{start.instrument|(unknown)}', '{start.station}', '{role}']
# MOUSE · acquisition     (no station)
# MOUSE · S1 · processing
```

## YAML

Only JSON's scalar types are recognised: lowercase `true`, `false`, `null`
and numbers like `12`, `-3`, `1.5`, `1e5`. Everything else is text, so `no`,
`on`, `yes` and `2026-11-18` stay text. Refused, with a hint to quote them:
`~`, numbers with leading zeros (`012`), `.nan`, `.inf`, and forms such as
`+1`, `.5`, `0x1F`, `1_000`. Refused outright: duplicate keys, several
documents, tags (`!!str`), anchors, aliases and merge keys (`<<`). Limits:
256 KiB per file, 64 files, nesting 32 deep, 10,000 YAML nodes, 1,000
predicate nodes, 1,024 characters per regular expression.
