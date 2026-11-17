# Disposition of Codex review 05 (step 3 vertical slice)

Review: [response-05-vertical-slice.md](response-05-vertical-slice.md).
Verdict: works end to end and the boundaries hold, but missing or stale data
could look complete. All findings accepted. Each has a regression test that
fails on the code reviewed (`5ad05fb`) and passes now. Re-run on 2026-11-16:
183 tests, including PostgreSQL and the real RE Manager on Redis; ruff, ruff
format, `mypy --strict`, `mkdocs build --strict`; on the rebuilt demo stack,
the smoke test (every panel query with no run, a MOUSE run and the generic
run selected) and the old-client check. Screenshots in headless Chromium:
no panel errors for any of the three selections, and with Tiled stopped,
*Data status* turned red (`stale`) and the open runs grey.

| Finding | Disposition | Where |
|---|---|---|
| 1. Source failures not visible | Accepted. New `/clips/status`: per root, whether its window search succeeded **now** (never-read roots included), truncation, failed activity reads and unplaceable runs, as `complete` / `partial` / `stale` / `incomplete` with one line per problem. The Timeline page shows it as *Data status* in place of *Data age*. The Health dashboard now shows the window fetch's error and data age next to the live probe. Closed stale clips keep their recorded colour (the outcome is still true); *Data status* says the root is stale | `adapter/app.py`, `timeline.py`; `timeline.json`, `health.json`; `tests/unit/test_search.py` (failing filtered search behind a good probe, missing second root), `test_grafana_provisioning.py` |
| 2. Truncation lost | Accepted. Searches ask for one item more than the cap, so exactly the cap is not truncation. Above the cap, open runs are kept first, then closed runs in Tiled's order. `/clips/frame` stays as it was; the companion `/clips/status` carries truncation, and `/clips` reports it per root. Table views say "only the first 2000 rows are shown" in a new `notice`, shown on the line above *Run data* | `tiled_http.py`, `layout.py`, `adapter/app.py`; `test_search.py` (exact cap, cap + 1, paging stops at cap + 1, open runs survive), `test_endpoints.py` (exact table limit, real fixture) |
| 3. One failed root freezes the others | Accepted. Each clip carries `observed_at`, the last successful fetch of its own record; open clips end there. `open_until` is gone | `clips.py`, `timeline.py`; `test_timeline.py`, `test_clips.py` |
| 4. Failed activity reads treated as activity | Accepted. The activity cache records each attempt and its error; failures are throttled like successes, and concurrent requests for one run wait for a single read. A failed read gives `activity_error`, no age, `possibly_stalled: false`, the frame state `activity unknown` (light blue) and a `partial` line in *Data status*. The recorded status is untouched | `run_state.py`, `timeline.py`, `clips.py`; `test_timeline.py` (failure, throttling, recovery, eight concurrent requests → one read) |
| 5. Stale records inherit later successes | Accepted. The stale cache keeps each run with the time it was observed. The fallback applies the full window rule, including the open-run horizon. A failed root's truncation is unknown (`null`), never "false" | `timeline.py`; `test_timeline.py` (disjoint window, horizon crossing, truncated fallback) |
| 6. Shared fields duplicated, units mislabelled | Accepted. Views carry `columns` (stream, field, units); tables read each stream's own fields and units. `fields` lists each name once; `units` keeps the first stream's units. New writer-produced fixture `shared_field`: `shared` in V in `snapshot` and in A in `mouse_eiger_measure` | `views.py`, `adapter/app.py`, `testing/fixtures.py`; `test_endpoints.py` |
| 7. A run closing between the searches disappears | Accepted. The open search runs first and a closed record wins on duplicate keys. Documented that the two searches are not one snapshot | `layout.py`; plan §5.4; `test_search.py` (the run closes between the two searches) |
| 8. Empty selection errors | Accepted. New `/selection/run`, `/streams`, `/fields`, `/data` with `uid` (and `stream`) as query parameters that may be empty. *Selected run* then says "Select a run …", the other panels are empty, and an unknown run is still a 404. All selected-run panels use them, and a provisioning test enforces that. The smoke test runs every panel query with no run, a MOUSE run and the generic run selected | `adapter/app.py`; `timeline.json`; `deploy/smoke_test.py`; `test_endpoints.py` |
| 9. Wrapper keeps the API object | Accepted as stated: the wrapper narrows the interface the adapter uses and is not a sandbox. The docstring, the test and `queues.md` now say so. New tests record every request at the transport and allow only `status` and `queue_get`: 0MQ against the real RE Manager, and HTTP through `bluesky-queueserver-api` 0.0.14's request layer, also checking that the key is sent. Enforcement remains the HTTP server's scoped key | `queues.py`; `test_queues.py`, `test_queue_manager.py`; `queues.md` |
| 10. Lane names can collide | Accepted. Lane names are unique across lanes and sub-lanes, in lane order (` (2)` for a taken name), whatever the input order. `lane_key` parts are percent-encoded, and a literal `-` is `%2D`. Recorded in migrations | `clips.py`; `test_clips.py`; `migrations.md` |
| 11. Zero-length zoom | Accepted. New `zoom_from_ms`/`zoom_to_ms`: the true extent with 5 %, at least 5 s, on either side. "Zoom to run" uses them, and `clip_*` keep the true extent. Also from the review's notes: a closed run without a usable stop time is drawn at its start, not up to now | `clips.py`; `timeline.json`; `test_clips.py`, `test_grafana_provisioning.py` |
| 12. Limits not enforced | Accepted. `max_points` bounds the rows over all fields (first and last event kept; more fields than points are cut, with `truncated`). Tables stop reading at the first row beyond the limit, and skip later streams when the stop document gives their counts. Activity reads fetch only the `time` column, unsorted. The activity cache is LRU-bounded (10,000), and the stale cache keeps the 45,000 most recently observed runs once it passes 50,000. Latency and memory on a 14-day event count: step 4 item | `adapter/app.py`, `layout.py`, `timeline.py`; `test_data_views.py` |
| 13. Metadata flattening | Accepted. Non-finite numbers become `null` at every depth; non-scalar lists are strict JSON; scalar lists are joined with JSON for non-strings. Limits: 8 levels, 2000 rows and 2000 characters per value, with `metadata_truncated`. `/selection/run` returns no metadata. Markup is returned as text and Grafana renders it as text (fixture sample ID `<b>S-shared</b>`); no escaping pass | `adapter/app.py`; `test_data_views.py`, `test_endpoints.py`; `migrations.md` |
| 14. Small version-1 gaps | Accepted. Queue shows *running_since*; *Streams* links each stream to a collapsed *Stream fields* row; *Data status* states the 21-day horizon. Refresh stays 30 s until step 4 measures the lab Tiled (decision D-007) | `timeline.json`; `user/index.md`; `decisions.md` |

Also from the conformance notes: *running since* is documented as the RE
Manager's start of the current item, not the run's start, and the status
and queue reads can briefly disagree (`queues.md`). D-006 now calls its
query-plan result one measured scale point. The data endpoints reuse a view's
rows for 5 seconds, because the notice line and the table read the same view.

Not verified: GitHub Actions itself, fresh Linux amd64 images, a live Bluesky
HTTP Server with a scoped key, the browser click gate (select and zoom
links) after the link change, and anything on the lab Tiled (step 4).
