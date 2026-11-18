# Codex brief 07: review the presets and layout design (step 8)

## Context

`~/Code/event_timeline_tiled` is a prototype: a read-only Grafana timeline
of Bluesky runs in Tiled. Steps 1–3 and 6 are built and reviewed (reviews
01–06); step 4, the operator trial, waits on the lab. Step 8, customisation,
is designed but not built: `docs/design/presets.md`. It adapts the earlier
configuration contract of the Dash prototype,
`~/Code/bluesky-web-plotting/design_documentation/CONFIG_SPEC.md` (v1), to this
architecture: Grafana with the Infinity data source, every query to the
adapter, the adapter reading Tiled.

The preset format is a contract: every deployment writes its own preset
files, and later changes of meaning would silently change their timelines.
Review it before any code depends on it. Later the package is meant to serve
any user of Bluesky and Tiled (synchrotrons, neutron sources, labs; plan
§13), so the format should not bake in this lab's habits.

Maintainer decisions (2026-11-18, `presets.md` §9): YAML files; the preset
chosen by a dashboard variable; a run appears in every track it matches; a
first dashboard variant ordered from generic to specific (§7).

Read first: `AGENTS.md`; `docs/design/presets.md`; `docs/design/plan.md`
(§2, §5, §7, §8, §9 step 8, §13); `docs/design/decisions.md` (D-008, D-009,
D-010); `docs/technical/endpoints.md` (`/clips`, `/clips/frame`,
`/clips/status`, `/runs/{uid}/trend`); CONFIG_SPEC.md. Then the code that
presets would change: `src/event_timeline_tiled/clips.py` (lane keys, names,
sub-lanes, frame), `timeline.py` (window searches per root, activity of open
runs), `layout.py` (`window_runs`, query construction), `views.py` (labels,
the view table), `adapter/overview.py`, `adapter/trends.py`,
`adapter/selection.py`, and `grafana/generator/`.

## Task

Independent critical review of the design. Do not change any file except
your response file and do not commit. You may run the tests and small
throwaway probes (for example Tiled 0.2.21 queries on a disposable SQLite or
PostgreSQL catalogue, as `tests/conftest.py` builds them).

1. **The format as a contract.** Is preset format v1 complete enough to last
   (ids, titles, `scope`, `tracks`, `where`, `group_by`, `label`,
   `require_streams`, `lanes`), and strict enough (unknown keys rejected,
   `version`)? What will be hard to change later, and what should be
   versioned or reserved now? Is anything still specific to this lab or to
   CONFIG_SPEC's Dash UI?
2. **YAML.** Loading with a safe loader; YAML 1.1 surprises in values that
   predicates compare (`no`, `on`, `off`, `yes` as booleans; dates;
   numbers with leading zeros; `null`/`~`); duplicate keys; size limits.
   What must the loader or validator do so that a preset means what its
   author wrote?
3. **Predicate semantics.** The rules of `presets.md` §5: missing and `null`,
   no type coercion, ordering operators, string operators on non-strings,
   `in`/`nin` with lists, field paths through lists (`streams`), relative
   times (evaluated when? a cached window and a refresh), `regex` (search
   semantics, length cap, trusted input). Gaps or inconsistencies with
   CONFIG_SPEC §11?
4. **`scope` pushed to Tiled.** `Eq`/`In` on start-document keys sent with
   both window searches (§6). Do they keep exactly the runs the adapter's own
   `eq`/`in` would keep, on SQLite and PostgreSQL in Tiled 0.2.21 (and 0.2.7)?
   Check missing keys, `null`, numbers (`1` and `1.0`), booleans, strings
   that look like numbers, and list-valued start keys (PostgreSQL JSONB
   containment matches a scalar inside a nested array?). Is "evaluate
   everything in the adapter, push only `scope`" the right split?
5. **Lanes.** Track × group value as lanes: naming and uniqueness, `order`,
   `min_lanes`, sub-lanes per lane, and cardinality (a `group_by` on a sample
   ID can make hundreds of lanes: cap, fold into "other", or refuse?). The
   built-in preset must reproduce today's lanes exactly, including lane keys,
   the "(unknown)" fallback and name disambiguation: as YAML, or only in code?
6. **A run in several tracks.** What the frame, `/clips`, `/clips/status`
   counts, truncation, "Select run" and "Zoom to run" links, the trend's
   "runs like it" (same track and group?) and the runs tables should do when
   one run is in several lanes.
7. **`require_streams` and `streams`.** Is `stop.num_events` a complete list of
   a closed run's streams as written by `bluesky.callbacks.tiled_writer.TiledWriter`
   (Bluesky 1.14.6), including streams without events? Open runs before their
   first descriptor or batch (the "strict" choice of CONFIG_SPEC §12)?
8. **The dashboard.** A preset variable fed by `/presets` through an
   Infinity query variable (Infinity 4.1.6, Grafana 13.3.1): does it work with
   the backend parser, keep its value in links (Select run, Zoom to run,
   stream links) and in the URL, and stay declarative? Any panel query that
   must not take the preset?
9. **The first layout variant** (§7 and the maintainer's sketch): narrow
   queue and data-status row; timeline; trend; three columns (latest runs on
   the selected run's lane, falling back to the window's runs before a
   selection; streams; a run summary above the metadata); the run's time
   series beside its corrected curve. Gaps for operators, and anything
   Grafana 13.3.1 cannot do declaratively here?
10. **Fit with the boundaries.** AGENTS.md: instrument knowledge only in the
    view table (presets are deployment data; no code tests an instrument
    name), one module for the Bluesky-in-Tiled layout, run state defined
    once, escaping of metadata in labels, contracts versioned. Does the
    design keep them, and where would the code go?
11. **For other facilities.** What would stop a synchrotron, neutron source
    or another lab from describing their timeline with this format?

## Response

Write to `docs/design/reviews/response-07-presets.md`:

- A verdict in one paragraph.
- Findings as a numbered list, each with: severity (blocker / should fix /
  suggestion), the section of `presets.md` (or file and line), the problem,
  the evidence (and the version you checked, for Tiled, Bluesky, Grafana,
  Infinity or PostgreSQL claims), and the proposed change.
- A proposed preset format v1 if yours differs materially.
- What you ran, and what you could not verify.
