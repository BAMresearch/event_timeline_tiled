# Disposition of Codex review 06 (work since review 05, and step 6)

Review: [response-06-since-review-05.md](response-06-since-review-05.md).
Verdict: the boundaries hold and the normal paths work, but a warm trend
cache could mislabel units, a refreshing dashboard kept a superseded image,
and a capped related-run search could still name a "latest" result. All
three blockers and nine of the twelve other findings are fixed; finding 9 is
deferred to step 4 and findings 14 and 15 are documented, with upstream
reports still to be filed. The step 6 code reviewed was the working tree on
top of `1e59e21`, not separate commits (the brief was wrong about that).

Re-run on 2026-11-17: the full suite on SQLite and PostgreSQL; ruff, ruff
format, `mypy src`, `mkdocs build --strict`; on the rebuilt demo stack, the
smoke test (every panel query for the three selections, and the corrected
image through the signed-in route). In headless Chromium with auto-refresh,
the image was requested three times in 30 s with a new URL each time (once
before the fix), and the *Corrected data* row showed no panel errors.

| Finding | Disposition | Where |
|---|---|---|
| 1. Trend cache mixes units | Accepted, fixed. The cache key holds each field with the selected run's units of it, so pieces filtered for V are never reused for mV. Regression: a V run then an mV run of the same plan, compared with a cold app; it fails with the old key | `adapter/app.py`; `testing/fixtures.py` (`unit_run`); `tests/contract/test_review_06_cases.py` |
| 2. Image not refreshed | Accepted, fixed. The image URL carries `t=${__to}`, which changes on every refresh with a relative range, so the browser asks again; the adapter ignores the parameter. Checked in the browser (above). A request per refresh costs one PNG download per viewer; the adapter keeps rendered PNGs of finished results | `timeline.json`; `test_grafana_provisioning.py`; `endpoints.md` |
| 3. Capped search claims the latest | Accepted, fixed. If a related search reaches its cap, nothing is marked latest, `default` is `null`, rows are unranked, the notice says so, and `/curve` and `/image` do not follow the measurement. Regression with the cap lowered below the three processing runs of a fixture measurement | `adapter/app.py`; `test_review_06_cases.py` |
| 4. Missing tables read as empty | Accepted, fixed. A missing table is "no events yet" only for an open run or a stream whose stop document reports no events; otherwise it is an error (`unreadable` in a trend, `502` elsewhere) | `layout.py`; `tests/unit/test_layout_tables.py`; `migrations.md` |
| 5. `Like` not lossless | Accepted, fixed. `Like` only for UIDs of letters, digits, `.`, `~`, `-` (UUIDs), whose candidates are exactly the matches; any other UID is found by scanning runs with `start.derived_from` (capped at 10,000, reported as truncated), checked exactly. Truncation is reported when the exact matches exceed the cap. D-008 revised. Regression: `parent_a` beside `parentXa` with a cap of one, and `párent` | `layout.py`; `decisions.md`; `test_review_06_cases.py` |
| 6. Malformed processing runs | Accepted, fixed. `related.problems()` checks schema version (only 1 is read), product, pipeline name, start UID and time, attempt, `request_id`, `derived_from`, and for finished runs stop time, `exit_status` and `outputs`. Such runs are listed with `problems` (new field, shown in *Related runs*) and never latest or usable; a processing run's own notice names them | `related.py`, `adapter/app.py`; `timeline.json`; `tests/unit/test_related.py` |
| 7. Families merged by label | Accepted, fixed. Families are keyed by `(product, pipeline.name)`; the label is only for display | `related.py`; `test_related.py` |
| 8. Trend cache not bounded | Accepted, fixed. A piece larger than the whole budget is not kept; each entry counts at least one value; at most 5000 entries. No direct test (the cache is internal to the app); covered by review of the code and the unchanged trend tests | `adapter/app.py`; `endpoints.md` |
| 9. Trend work not bounded | Deferred to step 4. Tiled cannot select rows, so the cost follows the events read. Documented in `endpoints.md`; step 4 measures a 14-day run on the lab Tiled before choosing input budgets, a shared executor, or reuse between panels | `endpoints.md`; plan §9 step 4 |
| 10. Render memory | Accepted, fixed. Reads are strided (ceiling) to at most `min(2 × size, 2048)` per side, about four million values, and at most two images render at once | `adapter/app.py`; `endpoints.md` |
| 11. Metadata cut silently | Accepted, fixed. Depth cuts set `metadata_truncated`, and a final `(cut)` row says so in the table. Also found: JSON text escaped `…` and non-ASCII as `\u…`; now written as is | `adapter/app.py`; `tests/unit/test_data_views.py`; `migrations.md` |
| 12. Invalid uncertainties | Accepted, fixed. A negative or non-finite uncertainty gives no bounds and is counted (`uncertainty_invalid`); bounds that are not finite are left out. Regression with uncertainties −1, NaN and ∞ | `adapter/app.py`; `testing/fixtures.py` (`uncertainty`); `test_review_06_cases.py` |
| 13. Source not identifiable | Accepted in part. The curve notice names the result by label, family and UID prefix, and `source.family` is new. The image has a caption pointing to that line; both follow the same rule and refresh together, but they are still two requests, so a result published between them can differ for one refresh. Binding both to one resolved result needs the dashboard to pass a result UID: step 8 | `adapter/app.py`; `timeline.json` |
| 14. DuckDB not covered | Accepted as a documented limitation: the retry does not make DuckDB tables reliable under concurrent requests (`testing.md`, `endpoints.md`). No DuckDB test in the suite. Upstream report to Tiled about the shared connection: to file, with the maintainer's agreement | `testing.md`, `endpoints.md` |
| 15. Missing file is 500, not 410 | Accepted. The comment is corrected and a regression deletes a result's HDF5 file: the curve says it could not be read (`HTTP 500`), the image gives `502`. Upstream report (the HDF5 adapter's `FileNotFoundError` is not translated): to file, with the maintainer's agreement | `tiled_http.py`; `test_review_06_cases.py` |

From the review's other conclusions: the trend module no longer claims a
line is never drawn across runs (runs under 2 ms apart and overlapping runs
are the exceptions).
