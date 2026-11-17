# Codex brief 05: review the step 3 vertical slice

## Context

`~/Code/event_timeline_tiled` is a prototype: a read-only Grafana
timeline of Bluesky runs in Tiled. Step 2 (scaffold, demo stack) was reviewed
in brief 04 ([disposition](disposition-04.md)). Step 3, the vertical slice, is
built on branch `step-3-vertical-slice` (commits after `68cc251`); step 4 is
an operator trial against the lab Tiled. Review step 3 as what operators will
use in that trial.

Read first: `AGENTS.md`, `docs/design/plan.md` (§3, §5, §6–§8, §9 step 3,
§14), `docs/design/decisions.md` (D-004 to D-006),
`docs/design/processing-runs.md` §1.2, `docs/technical/endpoints.md`,
`docs/technical/queues.md`, `docs/technical/testing.md`, `docs/user/index.md`.
Then the code:

- `src/event_timeline_tiled/`: `tiled_http.py` (requests, paging),
  `layout.py` (Bluesky-in-Tiled layout, window union), `run_state.py`,
  `views.py` (view table), `clips.py` (lanes, sub-lanes, frame),
  `timeline.py` (per-root fetch, stale data, last-event cache),
  `queues.py` (read-only RE Manager wrapper), `adapter/app.py` (endpoints),
  `settings.py`, `testing/fixtures.py` (`window_scenario`),
  `testing/queue_driver.py`
- `tests/` (`conftest.py`: SQLite and PostgreSQL servers; `contract/`,
  `unit/`)
- `grafana/dashboards/timeline.json`; `deploy/compose.yaml` (redis,
  re-manager, demo-queue), `deploy/queue/startup/`, `deploy/smoke_test.py`,
  `deploy/old_client_check.py`, `.github/workflows/ci.yml`

What was verified locally (2026-11-16, Docker Desktop, Apple silicon):
142 tests pass, including PostgreSQL (container started by the tests) and a
real RE Manager on Redis; ruff, ruff format, `mypy --strict` and
`mkdocs build --strict` are clean; on the demo stack the smoke test passes
(every timeline panel query through Grafana, `/queues`, access check) and
`deploy/old_client_check.py` passes (Tiled 0.2.8 client writing into 0.2.21);
the dashboard renders without panel errors in headless Chromium for a MOUSE
measurement and a generic time-series run. Findings made on the way are in
plan §14 and D-006: Tiled's `links.next` drops search filters; SQL-backed
event tables return rows in storage order; Infinity types auto-detected time
columns as numbers, hence long-format time series with Infinity's
`timeseries` format.

## Task

Independent critical review. Do not change any file except your response
file and do not commit. You may run the tests, linters, the docs build,
`docker compose -f deploy/compose.yaml --profile demo config`, and, if Docker
is available, a separate demo stack (own project name and ports, generated
secrets, removed with `down -v`), the smoke test and the old-client check.

1. **Conformance.** Instrument knowledge only in `views.py`; the
   Bluesky-in-Tiled layout and query choices only in `layout.py`; status,
   activity and source health kept apart; no `Contains` for UIDs; no
   per-run fetching for windows; dashboards free of instrument names and
   with only the `lane` partition transformation; queues read-only. Any
   leak, and anything step 3 of the plan required that is missing?
2. **Window search and paging.** Is the union in `Layout.window_runs` right
   at every boundary, and is the paging fix (repeat own parameters, take only
   `page[...]` from `links.next`) correct for Tiled 0.2.21, including cursor
   paging, concurrent inserts and `max_items` truncation? Check against the
   Tiled 0.2.21 source.
3. **Run state and stale data.** Status mapping, possibly-stalled rule and
   thresholds, the last-event cache (60 s), and `Timeline.window`'s stale
   behaviour when one root fails (cache of last good records, `open_until`).
   Can a run be shown wrongly (open drawn as closed, stale not marked, a
   closed run shown open forever)?
4. **Clips and frame.** Lane keys and names (fallbacks, facility
   disambiguation), sub-lane allocation on widened display extents, frame
   field order and units, `state` versus `status`. Do the spike's gate
   results (`docs/design/spike-results.md`) still hold for this frame?
5. **Endpoints.** Input validation, error mapping (404/422/502, no upstream
   bodies), `kind`/`view` selection, decimation, table row limit, the
   millisecond/second split, JSON safety of metadata flattening (non-finite
   numbers, large or nested values, HTML in values shown by Grafana tables).
   Anything that can be slow or unbounded on a 14-day open run or a large
   catalogue?
6. **Dashboard.** Data links (select, zoom), Infinity macros
   (`${__timeFrom:date:seconds}`), `${run:percentencode}` with an empty
   selection, column choices given Infinity's alphabetical columns, the
   time-series panel with Infinity's `timeseries` format (one and several
   fields). Checked against Grafana 13.3.1 and Infinity 4.1.6.
7. **Queue status.** Is `ReadOnlyQueue` actually read-only (what can still
   reach the API object), are the 0MQ and HTTP set-ups and the documented
   HTTP key handling right for `bluesky-queueserver-api` 0.0.14 and
   `bluesky-httpserver`, and is the 0MQ risk stated correctly? Is
   `running_since` from `running_item.properties.time_start` reliable?
8. **Tests and CI.** Do the fixtures (new cases in `window_scenario`) and
   tests cover what the plan lists for step 3? PostgreSQL tests fail rather
   than skip; the RE Manager tests are required in CI; will the CI jobs
   (Redis install, demo stack, smoke test, old-client check) work on
   GitHub's ubuntu runners?
9. **Readiness for the operator trial (step 4).** What would you change or
   check before pointing this at the lab Tiled with a read-only key?

## Response

Write to `docs/design/reviews/response-05-vertical-slice.md`:

- A verdict in one paragraph.
- Findings as a numbered list, each with: severity (blocker / should fix /
  suggestion), the file and line, the problem, the evidence (and the version
  you checked, for Tiled, Grafana, Infinity or queueserver claims), and the
  proposed change.
- What you ran, and what you could not verify.
