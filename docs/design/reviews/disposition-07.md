# Disposition of Codex review 07 (presets and layout design)

Review: [response-07-presets.md](response-07-presets.md). Verdict: the
architecture and the maintainer's four decisions stand; the format must not be
frozen yet. All findings are accepted and folded into
[presets.md](../presets.md) draft 2 (2026-11-18). Nothing is built yet, so
there are no regression tests here: §13 of the design lists the tests the
implementation must bring, including each case the review reproduced.

| Finding | Disposition | Where (presets.md) |
|---|---|---|
| 1. `Eq`/`In` are not the adapter's equality | Accepted. Adapter evaluation is authoritative, including `scope`. `scope` is sent to Tiled as a candidate superset only: non-null scalar operands, `in` lists of one type, `start.<key>` only; sent where proven (Tiled 0.2.21, SQLite and PostgreSQL, by the review's operand matrix), otherwise not sent. The draft's "tested on both" claim is replaced by that matrix | §7 |
| 2. The built-in cannot be YAML | Accepted at first: `instruments` as a reserved, built-in strategy implemented by today's code. **Reversed the same day** by the maintainer: nobody uses today's lane names and order yet, so `instruments` became an ordinary preset file, with the format extended to express it (`root`, lane-name templates with defaults, `lane_prefix`) and the changed names recorded; under follow-up review ([brief 08](brief-08-presets-follow-up.md)) | §2, §4.1, §13, §15 |
| 3. Membership needs a selection contract | Accepted. Runs and placements are separate; `/clips` keeps its rows and adds `placements` (which fields keep their meaning was revised by disposition 08, finding 10); the frame adds hidden `lane_id`/`track`; a URL-synced `lane` variable carries the clicked lane; `/selection/runs` lists a lane's runs once each; the trend's peers are the lane's runs with today's checks, each read once; direct links and preset changes are resolved or reported, never silently remapped | §6.1, §10, §11 |
| 4. Registry contract | Accepted: integer `version: 1`, slug ids, required keys and defaults, explicit `null` rejected, unknown keys rejected everywhere, reserved and duplicate ids rejected, identity by `id`, atomic load, `roots` defined (a subset only), `422` for unknown `preset`, `/presets` order, a registry revision | §2, §4 |
| 5. YAML loading | Accepted: a YAML subset with JSON scalars, a dedicated loader, rejected forms (duplicates, tags, anchors, aliases, merges, several documents, `~`, leading zeros, non-finite spellings), limits before validation, file/line/column/path in errors. Changes to the proposal: a 64-file limit added | §3 |
| 6. Truth table and paths | Accepted: the predicate grammar (no bare arrays), type categories (`1 == 1.0`, booleans apart, recursive typed equality), whole-value `in`, finite-number ordering, `not_empty`, `null` operands rejected, mapping-only dotted paths, `uid` as the Tiled key, `role`/`status` as `run_state` defines them | §5.1, §5.2 |
| 7. Relative times and regex | Accepted: one `evaluated_at` per request, re-evaluation on cached records, horizon still anchored to the window end, grammar and allowed operators, not in `scope`; patterns compiled at load, length cap stated as not a time bound, never from the browser | §5.3 |
| 8. Scope and stale caches | Accepted: coverage and freshness per root, window and normalised scope; stale fall-backs re-apply their own preset; observations shared per run; the fetch-A/fetch-B/fail-A test | §8, §13 |
| 9. Lane identity | Accepted: typed keys with a missing marker separate from `fallback` text; scalar group values only (lists and mappings reported, not fanned out); names separate from keys; multi-field `group_by` added | §4, §6.2 |
| 10. Order, padding, bounds | Accepted: `lanes.order` as a partial track order, typed group order, sub-lanes per logical lane, `min_lanes` counting sub-lanes with the query parameter taking precedence, deployment budgets with `max_lanes` only lowering them, overflow reported as a failure. Folding into "other" deferred | §6.2, §6.3, §14 |
| 11. Counts and truncation | Accepted: run counts stay unique-run counts; candidate, matched, placement and lane counts added; candidate truncation reported even when few runs match; which cap applied is named | §7, §9 |
| 12. Streams | Accepted, with the review's evidence recorded: `stop.num_events` lists zero-event streams for Bluesky 1.14.6 through `TiledWriter`. Streams of open runs (and closed runs without counts) are read and cached with the run's activity, once per run; unknown streams fail `require_streams` and are reported, never taken as absent | §5.4 |
| 13. Picker and links | Accepted: the Infinity variable as specified, URL sync, `instruments` initial value; links carry preset and lane and take the dashboard UID as a parameter; preset applies to lane endpoints and the trend only, never to run details or results. A browser navigation test on the demo stack is required | §10, §11, §13 |
| 14. "Experimental" premise outdated | Accepted. Grafana 13.3.1's new layouts are generally available and file-provisionable in the resource format. The first variant stays static by the maintainer's choice; the correction is recorded beside D-009 in `decisions.md`, not by rewriting it | §11; `decisions.md` |
| 15. Layout needs adapter data | Accepted: `/selection/runs` for the latest-runs column; the run summary above metadata; time series beside the corrected curve with notices; lane named in titles. An operator-chosen stream for the time series is not part of this variant (it needs its own contract through views) | §10, §11 |
| 16. Templates | Accepted: a restricted placeholder parser, literal braces, paths checked at load, JSON text for non-strings, a 200-character limit (added), fallback to the view table's label, escaping only at HTML and URL sinks | §6.4 |
| 17. Generality | Accepted: generic examples (beamline, station, sample) in the format; per-stream fan-out, dotted keys and list groups deferred and named; the processing-role classifier and view table noted as separate contracts to generalise later; module ownership as proposed | §4, §12, §14 |

The review's proposed format v1 is adopted with these changes: a 64-file
limit, a 200-character label limit, `/presets` ordered with `instruments`
first, and pushdown chosen by Tiled server version in `layout.py`.
