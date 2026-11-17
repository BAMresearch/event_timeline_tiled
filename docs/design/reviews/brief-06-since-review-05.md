# Codex brief 06: review the work since review 05, and step 6

## Context

`~/Code/event_timeline_tiled` is a prototype: a read-only Grafana timeline
of Bluesky runs in Tiled. Review 05 covered the step 3 vertical slice
([disposition](disposition-05.md)). Since then, commits `9c88fc7..1e59e21`
on `main` (PRs #1 and #2) added:

- fixes from the electrochemistry trial: a running run's streams before
  their first batch, runs queued back to back, stale selections
  (`e4c081f`);
- the electrochemistry-k2460 view-table entry (`9860e8a`);
- time-series fields in other units on a second axis (`f27b5fc`);
- a *Run metadata* panel: the selected run's start and stop documents,
  bounded (`118afa8`);
- test Tiled servers on SQLite tables only, no DuckDB (`61dcda2`);
- a **trend**: the selected run's time series over earlier runs like it,
  with gaps between runs (`/runs/{uid}/trend`, `/selection/trend`), and the
  timeline padded to at least four lanes (`/clips/frame?min_lanes`)
  (`d281d37`);
- the dashboard grouped into collapsible rows (`05d2275`);
- smoke-test fixes for panels with several queries (`48c9c52`, `7dcbe4f`);
- time-series views (`hinted`, `numeric`) for MOUSE measurements in the
  view table (`2575148`).

Then **step 6** against synthetic processing runs, on branch
`step-6-corrected-views` (commits after `1e59e21`): `/runs/{uid}/related`,
`/runs/{uid}/curve`, `/runs/{uid}/image` (also `/run-images/{uid}/image`
for the proxy) and their `/selection/...` forms; `related.py` (latest attempt
and latest usable result), `images.py` (PNG rendering), curve and image
views in `views.py`, array reads in `layout.py` and `tiled_http.py`, a
*Corrected data* row on the dashboard; decisions D-008 (`Like` for
`derived_from`) and D-009 (fixed panels per view kind).

Step 4 (operator trial) waits on `mouse_bluesky`'s deployment and a
read-only key (plan §15).

Read first: `AGENTS.md`, `docs/design/plan.md` (§5, §6.1, §7, §8, §9,
§14 progress notes, §15), `docs/design/decisions.md` (D-005 to D-007),
`docs/technical/endpoints.md`, `docs/technical/migrations.md`,
`docs/technical/testing.md`, `docs/user/index.md`,
`docs/design/processing-runs.md` (§1, §1.2). Then the code
(`git diff 9c88fc7 1e59e21`, then `git diff 1e59e21 step-6-corrected-views`):

- `src/event_timeline_tiled/`: `trend.py` (new), `adapter/app.py` (trend,
  selection metadata, `min_lanes`; step 6: related, curve, image),
  `views.py` (time-series, curve and image view specs), `clips.py`
  (padding), `timeline.py` (`WindowResult.records`), `layout.py`,
  `tiled_http.py`, `related.py` and `images.py` (new)
- `tests/`: `unit/test_trend.py`, `unit/test_views.py`,
  `unit/test_clips.py`, `unit/test_related.py`, `unit/test_images.py`,
  `contract/test_endpoints.py`, `contract/test_corrected.py`,
  `test_grafana_provisioning.py`, `conftest.py`
- `grafana/dashboards/timeline.json` (generated outside the repository;
  review the JSON as the source), `deploy/smoke_test.py`

What was verified (2026-11-17, Docker Desktop, Apple silicon): `pytest`
216 passed on SQLite and PostgreSQL; ruff, ruff format, `mypy src` and
`mkdocs build --strict` clean; CI green on `2575148` (lint, test, package,
and the demo stack with the smoke test). Headless Chromium screenshots of
the dashboard on the demo stack. An informal look at the lab Tiled (0.2.7,
single-user): window and trend timings are in plan §14; no panel errors.
Not verified: MOUSE runs that carry `instrument: "MOUSE"` (none exist yet).

## Task

Independent critical review. Do not change any file except your response
file and do not commit. You may run the tests, linters, the docs build and,
if Docker is available, a separate demo stack (own project name and ports,
generated secrets, removed with `down -v`) and the smoke test. Do not
contact the lab server.

1. **Conformance.** Instrument knowledge only in `views.py`; layout and
   query choices only in `layout.py`; no per-run fetching where a paged
   search returns metadata; dashboards free of instrument names and with
   only the `lane` partition; contracts changed only additively, with
   `migrations.md` updated. Any leak?
2. **Trend: which runs.** "Like the selected run" is the same lane key,
   role and `plan_name` within the window, plus the same view name and
   stream. Is that selection right and stable (open runs, the selected run
   outside the window, runs whose view resolves differently, missing
   `plan_name`)? Can a run be silently dropped, or a different run's data
   be plotted under the selected run's fields?
3. **Trend: points and gaps.** `trend.trend_rows`: global every-k
   decimation over all runs' events (always keeping the very last), gap
   rows (nulls) at the midpoint between runs, `max_points` counting gap
   rows, non-finite
   values. Is the bound strict, are gaps drawn as breaks by Grafana 13.3.1's
   time-series panel with Infinity 4.1.6's `timeseries` format, and do the
   reported counts (`runs`, `decimation`, `non_finite`) match what is drawn?
4. **Trend: cost and concurrency.** At most 200 runs, a 2,000,000-value
   cache of finished runs only, 4 workers, 64 striped locks so concurrent
   requests for the same run wait for one read, one sequential retry of
   unreadable runs. Is the cache bounded and correct (eviction, open runs
   never cached, a run finishing between reads), is the shared HTTP client
   safe across threads, and what does a 14-day window on a large catalogue
   cost in the worst case?
5. **Run metadata panel.** Bounding and truncation of nested or large
   values, non-finite numbers, HTML or markdown in values shown by Grafana's
   table, and the empty and not-found selections.
6. **Timeline padding and rows.** Padded lanes are named with no-break
   spaces and have null fields: can they collide with a real lane name,
   break data links, sub-lanes or the *Data status* counts? With v1
   collapsible rows, do panels in a collapsed row still query, and does the
   smoke test still cover every panel?
7. **View table.** The MOUSE `hinted` and `numeric` views mean the same as
   the fallback's views of the same names (view-table contract version 1)?
   Is the rule that a time series reads exactly one stream right, and does
   `resolve()` still prefer the right default for each MOUSE rule?
8. **Tests.** Do the fixtures (new electrochemistry runs with `plan_name`)
   and tests cover the cases above? Does moving test table storage from
   DuckDB to SQLite lose coverage of a storage a production server might
   use?
9. **Related runs (step 6).** Does `related.py` implement
   processing-runs.md §1.2 exactly (usable, latest attempt, latest usable,
   ties, open attempts, families, malformed runs reported not hidden)? Is
   D-008 right: `Like` on the JSON-quoted UID plus exact membership, on
   SQLite and PostgreSQL in Tiled 0.2.21 (and 0.2.7), with no false
   negatives (UID escaping, whitespace in PostgreSQL's JSON text) and a
   bounded cost? Is following a measurement to its latest usable result
   (`/curve`, `/image`) the right default, and is it always visible to the
   operator which run is shown?
10. **Arrays and rendering (step 6).** `Layout.result_info` /
    `result_array` (structure checks, dtype, strided slice syntax, the byte
    bound, 410 for a missing file) against Tiled 0.2.21's array endpoints;
    `images.render` (block means, log scale, percentiles, transparency,
    orientation). Can a large or malformed array exhaust memory or time?
11. **Image route and caching (step 6).** Headers (`Cache-Control`,
    `ETag`, `304`) for final, followed and open results, through the
    nginx route of plan §6 (`auth_request`, cookies stripped); can a stale
    image be shown after a newer result is published, or one run's image
    be served for another? The Text panel's `<img>` under Grafana 13.3.1's
    sanitiser, and the empty-selection URL.
12. **Curve panel (step 6).** The XY chart options (auto mapping with x by
    name, log scales on both axes, null bounds) for Grafana 13.3.1 with
    Infinity 4.1.6's column order; the curve's dropped points and notice.

## Response

Write to `docs/design/reviews/response-06-since-review-05.md`:

- A verdict in one paragraph.
- Findings as a numbered list, each with: severity (blocker / should fix /
  suggestion), the file and line, the problem, the evidence (and the version
  you checked, for Tiled, Grafana or Infinity claims), and the proposed
  change.
- What you ran, and what you could not verify.

Questions 1–8 concern the merged work up to `1e59e21`; 9–12 the step 6
branch.
