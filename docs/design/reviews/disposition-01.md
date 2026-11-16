# Disposition of Codex review 01

Review: [response-01-architecture.md](response-01-architecture.md) (of plan
revision 4). Result: plan revision 5, new
[processing-runs.md](../processing-runs.md), revised `AGENTS.md`.

Spot-checked before accepting: MOUSE `baseline` via `SupplementalData`
(`startup/04_baseline.py`); optional `snapshot`; `RE.stop()` records success
without `COMPLETE` (`plans/public.py` docstring); MoDaCor `mouse` source
profile requires a background (`server/source_profiles.py`); in-memory
sessions (`server/session_manager.py`).

| # | Finding | Disposition | Where |
|---|---|---|---|
| 1 | Adapter from the spike | Accepted. Thin adapter from the start; direct Infinity only as a throwaway diagnostic | plan §6, §9; AGENTS |
| 2 | State timeline: prove run identity and intervals | Accepted as panel-gate acceptance cases | plan §8, §9 step 1.2 |
| 3 | Window overlap discovery | Accepted: half-open window, four-part union, open-run horizon, window-independent reverse lookup, required PostgreSQL tests | plan §5.4, §10 |
| 4 | Recorded outcome vs inferred activity | Accepted: `open` status, last event, per-instrument stall threshold, stale open clips stop growing | plan §5.1–5.2 |
| 5 | Lane and run identity | Accepted: lane key (facility, instrument, station, role); station copied to processing runs; labelled fallback | plan §3.1 |
| 6 | Views as capabilities | Accepted: `/runs/{uid}/views`, priority matching, fallback chain | plan §6.1, §7 |
| 7 | MOUSE inventory | Accepted: baseline, optional snapshot, readings ≠ frames, stop vs `COMPLETE` | plan §3.2 |
| 8 | `TiledSink` is not a run publisher | Accepted: MoDaCor publisher requirement; one representation to be chosen after brief 02 | processing-runs §2 |
| 9 | Versioned processing schema | Accepted: schema v1 draft, usable-result definition, finality | processing-runs §1 |
| 10 | Result families and lineage | Accepted: `kind`, `product`, `inputs` vs `derived_from`, ordering, latest attempt vs latest usable | processing-runs §1; plan §4 |
| 11 | Eligibility and readiness | Accepted: rules in MoDaCor-examples; viewer shows "no result" instead of "awaiting" until eligibility is published | processing-runs §4; plan §4 |
| 12 | Watcher reliability is not automatic | Accepted: single owner, durable ledger, request IDs, claims, retries, reconciliation | processing-runs §3 |
| 13 | Atomic source binding + processing | Accepted: exclusive session first, job request later | processing-runs §5 |
| 14 | Watcher home | Accepted as proposed: separately launched runner in MoDaCor's server package, MOUSE rules in MoDaCor-examples | processing-runs §3 |
| 15 | Operator truth boundary, degraded display | Accepted: "recorded runs" header, queue-monitor link, last successful fetch, stale marking, failed/aborted table. Queue status stays out of v1 | plan §1, §5.3, §8 |
| 16 | Endpoint bounds and caching | Accepted: validation, paging, truncation flag, cache only final content | plan §6.1 |
| 17 | Browser image and credential route | Accepted as a separate spike gate | plan §6, §9 step 1.3; brief 03 |
| 18 | Carry over decisions, not old behaviour | Accepted: step reference fixed; hover crosshair for v1, persistent playhead deferred | plan §2, §13 |
| 19 | Reorder gates; early operator trial | Accepted: pinned versions, three parallel gates, vertical slice, trial before corrected views, contract work from step 1 | plan §9 |
| 20 | AGENTS conflicts | Accepted: disposable catalogues for tests and demo, processing fixtures via `event_model`, PostgreSQL tests required, no production contact | AGENTS |
