# Review 06: work since review 05 and corrected views

The architecture still respects the main read-only, instrument and layout
boundaries, and the normal paths work: all 253 tests and the isolated demo
smoke test passed. I would nevertheless fix the trend cache before the
operator trial and hold step 6 for the three blockers below: a warm cache
can mislabel physical units, a refreshing dashboard can retain a superseded
image, and an incomplete related-run search can claim an older result is
the latest. The remaining findings concern completeness, malformed inputs,
resource limits and missing regression coverage. These are independent
observations, including reproductions outside the passing suite.

Reviewed on 2026-11-17. The merged range is `9c88fc7..1e59e21`. Contrary to
the brief's description of subsequent commits, both `HEAD` and the branch
`step-6-corrected-views` point to `1e59e21`; step 6 is the supplied working
tree, including its untracked modules and tests. Line numbers below refer
to that working tree. No implementation file was changed and no commit was
made.

## Findings

1. **Blocker — the trend cache can plot values in the wrong units
   (merged).** `src/event_timeline_tiled/adapter/app.py:709`, `:740`.

   A piece depends on the selected view's units: `_read_trend_piece` replaces
   fields with null when another run uses different units. Its cache key
   includes field names, but omits those units. Selecting another run with
   the same fields and different units reuses the first selection's filtering
   decisions and values under the new legend.

   Evidence: two real RunEngine/TiledWriter runs of the same plan, stream
   and field, one in V and one in mV, on Tiled **0.2.21**. After requesting
   the V trend, the mV trend returned values `[2, null, null]`; a fresh app's
   mV request returned `[null, null, 2]`. Both reported one run lacking
   fields. The warm response therefore plotted the V run as mV and hid the
   mV run.

   Proposed change: cache each run's own data and units, applying compatibility
   filtering after lookup, or include the selected units and all other
   filtering inputs in the key. Add a regression that switches selections in
   both orders and compares warm and cold results.

2. **Blocker — dashboard refresh does not refresh the corrected image
   (step 6).** `grafana/dashboards/timeline.json:1833`;
   `src/event_timeline_tiled/adapter/app.py:1091`.

   The image URL depends only on the selection and size. HTTP `no-cache`
   requires revalidation when a request occurs; it does not make an existing
   `<img>` request itself again. Grafana **13.3.1**'s default
   [Text panel](https://github.com/grafana/grafana/blob/v13.3.1/public/app/plugins/panel/text/v1/TextPanel.tsx)
   updates processed content only when the interpolated HTML changes.

   Evidence: headless Chromium on the rebuilt isolated stack, with a 5 s
   refresh, made one image request and retained the same image node over
   16 s. In a second probe I published a newer valid correction with a
   different image shape into that disposable catalogue. After another
   12 s the browser still showed the original 512×512 image and had made
   only one image request. An explicit conditional GET through nginx returned
   **200 with a different ETag**. The adapter found the new result; the
   dashboard did not request it. A previously missing image can likewise
   remain broken after processing finishes.

   Proposed change: make the image source change on refresh, for example
   using a verified refresh-varying built-in value in the declarative Text
   content. Preserve revalidation for measurement URLs and immutable caching
   for explicit result URLs. Test publication after the dashboard is already
   open, including an initial 404, without changing the selection or reloading
   the page.

3. **Blocker — a truncated related search still produces an authoritative
   “latest” result (step 6).**
   `src/event_timeline_tiled/adapter/app.py:949`, `:953`, `:964`;
   `src/event_timeline_tiled/layout.py:215`.

   Related searches retain the first 500 candidates per root in the reader's
   default order. `corrected()` discards the truncation flag and chooses a
   default from that incomplete set. `/related` marks the body truncated,
   but still marks rows “latest” and its notice does not explain the limit.
   Curve/image following can consequently miss newer results indefinitely,
   while saying the displayed result is the latest usable one. Conditional
   image requests can keep returning 304 for that wrong selection.

   Evidence: on a disposable Tiled **0.2.21**, two valid corrections of a
   measurement, with `RELATED_MAX_RUNS` lowered to one in the probe process.
   `/related` reported truncation and selected the older correction;
   `/curve` served it with `truncated: false` and the notice “the latest
   usable result of the selected measurement”. The same logic applies at
   the production cap of 500, across families as well as attempts.

   Proposed change: separate bounded listing from complete result selection.
   Use a search/scan strategy that proves the winners, or refuse to make an
   authoritative default when the search is incomplete. Carry discovery
   completeness and an explicit notice through both corrected endpoints and
   the dashboard. Test cap + 1 with the newest usable result beyond the cap.

4. **Should fix — missing tables of finished runs become successful empty
   reads (merged).** `src/event_timeline_tiled/layout.py:338`.

   The first-batch fix catches every `NOT_FOUND`, regardless of whether the
   run is open. For a finished run that reports events, a missing table is
   missing data, not “none written yet”. The trend receives an empty piece
   rather than `unreadable`, can cache it permanently, and silently loses
   that run's events. Stream counts can simultaneously show the positive
   stop-document count and no first/last event.

   Evidence: a focused reader-error probe with a closed record reporting
   four primary events and a 404 returned empty `seq_num`, `time` and value
   columns. `_read_trend_piece` has no subsequent check for this inconsistency.
   This is adapter logic, independent of a Tiled version.

   Proposed change: tolerate a missing table only when it is legitimately
   unwritten, such as an open run or an explicit zero event count. Report an
   unreadable/inconsistent finished stream otherwise. Cover both the current
   open-before-first-batch case and a closed stream with missing data.

5. **Should fix — D-008's candidate query is not lossless for all supported
   provenance UIDs (step 6).** `src/event_timeline_tiled/layout.py:216`;
   `docs/design/decisions.md:19`.

   JSON quoting is not SQL LIKE escaping. `_` remains a wildcard, and the
   representation of Unicode/backslashes differs between JSON serialization
   and database extraction. Exact membership removes false positives only
   after the candidate cap; it cannot recover excluded or unreturned runs.

   Evidence on Tiled **0.2.21**, using valid event-model processing runs
   written through TiledWriter: `derived_from: ["párent"]` was not found on
   SQLite. `parent_a` was found without a cap, but with one candidate allowed,
   earlier `parentXa`/`parentZa` candidates consumed the cap and returned no
   exact match. `_` is allowed by the adapter's UID path contract. PostgreSQL
   **16** read-only SQL probes confirmed the wildcard match, and false
   negatives for the JSON-escaped Unicode and backslash patterns. Unicode and
   backslash examples concern start-document/provenance UIDs, which can
   differ from path keys. Ordinary quoted UUIDs do match PostgreSQL JSON text
   despite spaces after commas. The
   [0.2.7 source](https://github.com/bluesky/tiled/blob/v0.2.7/tiled/catalog/adapter.py)
   uses the same `as_string().like()` query construction; it was not tested
   as a running server.

   Proposed change: use a backend-aware exact membership strategy inside
   `layout.py`, or a demonstrably lossless candidate fallback with proper
   pattern/serialization handling. Apply caps without claiming complete
   membership after post-filter losses. Add SQLite/PostgreSQL cases for
   underscores, Unicode, quotes, backslashes and false candidates before the
   cap, and qualify D-008's compatibility claim.

6. **Should fix — malformed/unsupported processing records are not diagnosed
   and can become defaults (step 6).**
   `src/event_timeline_tiled/related.py:41`, `:71`;
   `src/event_timeline_tiled/adapter/app.py:902`.

   There is no processing-schema validation or version handling. `usable()`
   checks success and product membership only; `families()` treats any stop
   mapping as finished. Missing pipeline identity, missing/non-finite stop
   time, missing UID or an unsupported schema version produce no validation
   diagnostic. Bad parent entries are filtered out, and a non-list parent
   value becomes an ordinary zero-parent processing run. This falls short of
   processing-runs.md §1.2's requirement to report malformed runs.

   Evidence: a focused record probe with only `start.product` and
   `stop.exit_status/outputs` was accepted as usable and as latest usable,
   despite lacking start/stop times and UIDs, pipeline and schema version.
   The existing tests exercise valid result families and tolerate missing
   display keys, but do not assert a malformed-record diagnostic.

   Proposed change: validate the processing contract at the interpretation
   boundary; retain discoverable invalid records with reasons and report
   unsupported versions explicitly. Do not promote records lacking the
   required ordering/identity facts to authoritative defaults. Add diagnostic
   fields additively and document them in the endpoint contract/migrations.

7. **Should fix — result families use a lossy display string as identity
   (step 6).** `src/event_timeline_tiled/related.py:30`, `:68`.

   Grouping by `"product · pipeline.name"` merges distinct valid families
   when either component contains the separator. Missing pipeline names also
   collide with literal product strings. One family's attempt/result can
   then replace another's in the operator's marks and default selection.

   Evidence: a pure-function probe with `(product="a · b", pipeline="c")`
   and `(product="a", pipeline="b · c")` produced one family. The contract
   imposes no restriction excluding these strings.

   Proposed change: group by a tuple of validated components and format the
   family label separately. Add a collision regression; expose an unambiguous
   family identifier if consumers need one.

8. **Should fix — the trend cache is not bounded by two million values
   (merged).** `src/event_timeline_tiled/adapter/app.py:719`.

   Eviction stops when one entry remains, even if that single piece exceeds
   the entire budget. An arbitrarily large finished run is retained forever
   until displaced. Empty pieces cost zero in the accounting and have no
   separate entry-count cap, so value accounting alone does not bound their
   overhead either.

   Evidence: a small closure probe lowered the budget to three values and
   cached a two-event, one-field piece: four values remained in one entry.
   This exercises the actual cache code without allocating a large run.

   Proposed change: do not admit an oversized piece; enforce both the value
   budget and an entry-count limit, including empty pieces. Test an oversized
   singleton, zero-length pieces, eviction/accounting, and concurrent reads.
   The existing striped locks and shared lock do protect normal finished-run
   cache mutations; open records bypass the cache, which is appropriate.

9. **Should fix — output limits do not bound trend work or peak memory
   (merged).** `src/event_timeline_tiled/adapter/app.py:742`, `:782`;
   `src/event_timeline_tiled/trend.py:50`;
   `src/event_timeline_tiled/tiled_http.py:234`.

   Every chosen stream's whole event columns are downloaded, decoded and
   sorted before decimation. All pieces remain alive for the request even
   after cache eviction. `_pick` additionally allocates a tuple for every
   source event, rebuilding that list when the row-budget loop increases k.
   Four workers are created per request, not shared across the app. Concurrent
   axes/viewers multiply active reads and allocations; the striped locks
   cover finished pieces, not an overall work budget.

   Evidence: the **0.2.21** table path uses whole-column JSON reads. With E
   events and F requested fields across the 200 runs, the request needs at
   least O(E × F) data and O(sum(n log n)) sorting, plus O(E) selection tuples,
   even for `max_points=10`. Fourteen days at 1 Hz is 1,209,600 events in one
   run; the 200-run cap does not cap events in long overlapping runs. The
   selected time-series dashboard also performs seven independent window
   evaluations per refresh: four timeline/table/status queries and three
   trend queries. At the default 10,000-run root cap, each of the two searches
   can require 34 pages, before stream reads. SQL scan cost depends on total
   catalogue size; the measured small-catalogue timing is not a worst-case
   bound.

   Proposed change: introduce explicit input/work and in-flight budgets,
   share a bounded executor/admission limit, compute decimation indices
   without materializing all event tuples, and reuse window/trend work
   between panels. Until upstream supplies row-range/aggregate reads, refuse
   or clearly report oversized inputs rather than promising bounded cost
   from a small output alone. Benchmark a synthetic 14-day event count.

10. **Should fix — the array byte bound permits much larger render
    allocations (step 6).** `src/event_timeline_tiled/layout.py:262`, `:273`;
    `src/event_timeline_tiled/adapter/app.py:1105`;
    `src/event_timeline_tiled/images.py:74`.

    The 64 MiB check measures the incoming dtype, then converts the entire
    slice to float64. Rendering allocates padded float64 data and reduction
    temporaries. The floor-based server stride also leaves dimensions just
    below `4 × size` effectively unstrided. Concurrent cache misses can render
    the same large image independently.

    Evidence by allocation arithmetic, without an exhaustion test: a valid
    8191×8191 uint8 result at `size=2048` has `step=1` and a 67,092,481-byte
    payload, below the bound. Its float64 array alone is about 511.88 MiB;
    the padded 8192×8192 allocation is another 512 MiB, and `np.where` and
    `np.nanmean` add temporaries. Thus the accepted input can require well
    over 1 GiB in one request. Typed strided reads themselves worked on Tiled
    **0.2.21**; this is the adapter's allocation policy.

    Proposed change: bound element count and expanded working memory, use a
    ceiling stride to enforce a chosen read dimension, reduce without large
    padding/copy temporaries, and bound concurrent renders. Add arithmetic
    tests for narrow dtypes and threshold dimensions; a test should not need
    to allocate the dangerous array.

11. **Should fix — metadata can be cut without reporting truncation
    (merged panel; inherited formatter).**
    `src/event_timeline_tiled/adapter/app.py:113`, `:159`;
    `grafana/dashboards/timeline.json:2425`.

    `_plain` replaces deep content with an ellipsis without setting
    `_Rows.truncated`. A deep-only document therefore reports
    `metadata_truncated: false` despite losing values. Moreover, the new
    metadata table reads only `metadata`, so even true truncation is not
    displayed to the operator. JSON/list text is fully constructed before
    the character cut, and dotted keys have no length bound; the output's
    string-value limit is not a general work/payload bound.

    Evidence: a small nested mapping returned a value containing `"…"` with
    `truncated: false`. Existing tests include a separate overlong string,
    which makes the same test pass without detecting this error. Markup
    rendering is otherwise safe in the configured plain-text table: Grafana
    **13.3.1**'s
    [AutoCell](https://github.com/grafana/grafana/blob/v13.3.1/packages/grafana-ui/src/components/Table/TableNG/Cells/AutoCell.tsx)
    renders a string as a React child, not HTML.

    Proposed change: propagate every depth/length cut to the flag and show a
    truncation notice beside the metadata table. Add key/text construction
    budgets if these are intended as resource bounds. Test deep-only content
    without another truncation trigger, plus large nested lists and keys.

12. **Should fix — uncertainty bounds can be invalid or break JSON
    serialization (step 6).**
    `src/event_timeline_tiled/adapter/app.py:1004`;
    `src/event_timeline_tiled/views.py:315`.

    A finite sigma is used without checking its sign, units, or whether the
    arithmetic remains finite. Negative sigma reverses the bounds and can
    give a non-positive upper bound on log axes. Large finite float64 values
    can overflow `y + sigma` to infinity; unlike x/y, that result is neither
    removed nor converted to null. Non-finite sigma silently removes both
    bounds without an uncertainty-specific count/notice.

    Evidence: a focused call to the actual curve closure with finite
    `y = sigma = 1e308` produced `y_upper: inf`, which failed strict JSON
    serialization. The endpoint catches `SourceError`, not this serialization
    failure. Negative and non-finite uncertainties are absent from the
    contract tests.

    Proposed change: validate uncertainty compatibility and non-negativity;
    check both computed bounds for positivity/finiteness, null invalid bounds
    and report why. Add counts additively to the contract and tests for
    negative/NaN/infinite sigma, overflow, and non-positive lower bounds.

13. **Should fix — the visible result provenance is not unambiguous
    (step 6).** `src/event_timeline_tiled/adapter/app.py:961`;
    `grafana/dashboards/timeline.json:1833`.

    The curve API returns an exact source UID, but its displayed notice uses
    only `label(run)`, which for processing is product and attempt. Different
    requests/pipelines can share that label. The image has no source caption
    of its own and no image-specific notice; its alt text points at the curve
    notice. Curve and image resolve their targets in separate requests, so
    publication between them can select different runs even after fixing
    refresh. The operator cannot always identify the actual image's source.

    Evidence: `label()` omits UID, request ID and pipeline; `_source`'s UID is
    not selected by the notice query, and `run_image` does not expose its
    resolved target to the Text panel. The publication probe in finding 2
    demonstrated the stronger case of an old image remaining alongside
    refreshed data.

    Proposed change: resolve and visibly identify the result UID/family used
    for each corrected product, ideally binding both products to one resolved
    immutable result selection. Keep direct inspection of open/failed
    processing runs, but label their status and source beside the image too.

14. **Should fix — replacing DuckDB fixtures removes coverage of a still
    failing supported storage path (merged).** `tests/conftest.py:65`;
    `src/event_timeline_tiled/adapter/app.py:784`.

    SQLite and PostgreSQL do not test DuckDB's shared-connection behavior.
    Retries are sequential only within one trend request; another request's
    reads can overlap them. Moving the default fixtures avoids the failures
    without proving that existing DuckDB catalogues can be read reliably.

    Evidence: a separate disposable SQLite catalogue with DuckDB table
    storage on Tiled **0.2.21**, six real electrochemistry fixture runs, and
    four concurrent trend requests for the two axes. Of three cold-app rounds,
    two produced responses still missing runs after the retry: one had
    `unreadable: 1`, another `unreadable: 2` and only four plotted runs.
    Upstream errors included “unsuccessful or closed pending query result”.
    Tiled 0.2.21's `SQLStorage._connection_pool` uses `StaticPool` for DuckDB.
    The adapter does report the unreadable count, so this is not a claim of
    concealed errors.

    Proposed change: retain the stable default fixtures, but add an explicit
    concurrent DuckDB compatibility test and document this limitation. Pursue
    connection isolation upstream as required by repository policy. Do not
    describe a request-local retry as guaranteed recovery, or claim production
    DuckDB compatibility from SQLite-only tests.

15. **Suggestion — pin down the missing-file error claim with a real
    registered-array regression (step 6).**
    `src/event_timeline_tiled/tiled_http.py:98`;
    `tests/contract/test_corrected.py:148`.

    Mapping an actual 410 to `NOT_FOUND` is sensible, but the comment's
    assumption that a missing registered file yields that status is not
    generally true for the HDF5 path reviewed here.

    Evidence: on Tiled **0.2.21**, I wrote a valid processing fixture, read
    its array successfully, removed only that disposable fixture's HDF5 file,
    and requested it again. Tiled raised an uncaught `FileNotFoundError` while
    constructing the HDF5 adapter and returned **500**; the reader reported
    `error: HTTP 500`. Tiled's server maps `BrokenLink` to 410, but this path
    did not raise it. The current endpoint tests cover runs without an image,
    not a registered image whose file has disappeared.

    Proposed change: record the observed limitation, add a real missing-file
    test and report the exception translation upstream. Preserve generic 500
    handling in the adapter; do not reinterpret every upstream 500 as missing
    data or expose its traceback/body.

## Other conclusions against the brief

- **Conformance:** instrument names remain in the view table, fixture/demo
  code and documentation; no instrument-dependent endpoint/dashboard branch
  was found. Bluesky tree/query choices remain in `layout.py`, with HTTP
  protocol mechanics in `tiled_http.py`. Window metadata is reused through
  `WindowResult.records`, and parent measurements use a paged UID search.
  No new Tiled writes, queue controls, credential-bearing dashboard queries,
  or prohibited dashboard transformations were found. Endpoint additions and
  the changed processing default are recorded in migrations. Retain that
  contract discipline when implementing the proposed diagnostic fields.
- **Trend selection:** lane, role and `plan_name` comparisons implement the
  documented policy, including missing `plan_name` matching missing and a
  selected run outside the window defining the template without being added
  to it. Each candidate resolves its own view and stream; incompatible views
  and fields are counted. This is a reasonable prototype grouping policy,
  not proof of the same experimental protocol or sample. The cache in finding
  1 defeats the otherwise correct units check. The 200-run cap is applied
  before view eligibility, so “latest 200” means lane/role/plan candidates,
  not the latest 200 plottable runs; the notice reports excluded views.
- **Points and gaps:** endpoint field limits make the row budget strict,
  including gap rows. Decimation can remove entire short runs; `plotted`
  counts pieces that actually emitted rows, not every candidate. The
  non-finite count covers examined kept events/values, not every source
  value. A distinct midpoint null survives Infinity **4.1.6**'s
  [LongToWide conversion](https://github.com/grafana/grafana-infinity-datasource/blob/v4.1.6/pkg/infinity/postprocess.go)
  with null fill: an actual Grafana query returned `[1, null, 2]`. The trend
  panel does not enable null spanning. There is intentionally no gap when
  rounded timestamps leave no intervening millisecond, and overlapping runs
  interleave as documented. Thus the broad “never drawn across runs” wording
  in `trend.py` is stronger than the actual guarantee. If that guarantee is
  essential, represent separate run segments rather than relying solely on
  midpoint timestamps.
- **Concurrency:** HTTPX **0.28.1** explicitly documents its synchronous
  `Client` as shareable between threads in the installed `_client.py`.
  Sharing it is not itself a defect. Finished records use striped cache
  locks; open records bypass the cache. A read that starts from an open
  record remains uncached if the run finishes during the read, which is safe.
  This does not solve the aggregate resource and DuckDB issues above.
- **Padding/rows:** the no-break-space names check the actual lane-name set;
  allocation increases the number of spaces, so generated blanks remain
  distinct. Null state and UID give no real clip/link to click. Padding is
  added after real sub-lane allocation and does not enter `/clips/status`
  counts. In browser testing the initially collapsed Stream fields row made
  no fields query. The smoke test explicitly walks its nested panels anyway.
- **Views:** MOUSE's `hinted` and `numeric` keep their fallback meanings,
  scoped to the detector stream. The one-stream rule is appropriate for these
  time-series semantics. Configuration and activity rules precede the general
  measurement rule; the measurement default remains `readings`, and numeric
  time series take over when no hinted fields exist. No additional MOUSE
  contract regression was found.
- **Ordering/rendering:** valid related fixtures exercise successful results,
  later failures, open attempts, partial outputs and stop-time UID ties; the
  normal ordering matches §1.2. A Tiled **0.2.21** float32 result of shape
  `(1, 16, 16)` read with `0,::3,::3` produced a `(6, 6)` float64 array as
  expected. Renderer tests cover finite block means, log/linear masking,
  percentiles, constant images, transparency and row-zero-at-top orientation.
  No separate algorithmic error was found in those ordinary cases.
- **Image headers and authentication:** final explicit-result images use
  private caching; followed measurement images revalidate, with ETags keyed
  by resolved root/result/view/scale/size. The isolated proxy returned 304
  for an unchanged followed image and 200 with a new ETag after publication.
  nginx authenticates first and strips Cookie and Authorization before
  forwarding. The smoke test verified session login, signed-in PNG access and
  anonymous refusal. The sanitized `<img>` rendered successfully. An empty
  selection produces `/run-images//image?...`, a 404 with alt text rather
  than an empty-selection image endpoint.
- **XY chart:** no mapping/axis defect was found. Grafana **13.3.1**'s
  [prepSeries](https://github.com/grafana/grafana/blob/v13.3.1/public/app/plugins/panel/xychart/utils.ts)
  honors the x-by-name matcher in auto mode and maps the other numeric fields
  as y series. Its
  [scatter configuration](https://github.com/grafana/grafana/blob/v13.3.1/public/app/plugins/panel/xychart/scatter.ts)
  reads scale distribution from both x and y fields, so the shared log
  defaults apply to both axes. The actual panel loaded without browser
  errors. The ordinary dropped x/y counts and non-positive lower-bound
  notices match the code; finding 12 concerns the untested uncertainty cases.

## Checks run and limits

All Python checks used `uv run --locked --offline`, with
`UV_CACHE_DIR=/private/tmp/ett-review06-uv` because the default cache was
inaccessible inside the sandbox:

- `ruff check`: passed.
- `ruff format --check`: 51 files already formatted.
- `mypy src`: passed, 20 source files.
- `pytest -q`: **253 passed**, four dependency deprecation warnings, 75.31 s.
  This included required PostgreSQL tests using a disposable container, the
  dashboard checks and the local RE Manager tests.
- `mkdocs build --strict --site-dir /private/tmp/ett-review06-docs`: passed.
- Rebuilt and started a separate demo stack, Compose project `ett-review06`,
  with generated temporary secrets and ports 18086/18087. Ran the supplied
  smoke test against its settings by substituting the env-file/project paths
  in memory: **all checks passed**, including all 19 queries for each of the
  three selections, the signed-in image route and refused writes using the
  adapter key on disposable Tiled.
- Headless Chromium against that stack: image rendering and refresh,
  publication after page load, proxy ETag/304 behavior, the collapsed row,
  browser errors, and an Infinity query preserving a null gap. Additional
  fixture writes were confined to that disposable stack.
- Separate disposable RunEngine/TiledWriter fixtures for the units-cache,
  related-search cap, UID-pattern, strided-array, missing-file and concurrent
  DuckDB probes; small pure-function/closure probes for family collisions,
  malformed records, metadata depth, cache accounting and uncertainty
  overflow. Reduced caps were process-local assignments, not source edits.
- Inspected installed Tiled **0.2.21**, Bluesky **1.14.6**, event-model
  **1.23.1** and HTTPX **0.28.1** code as relevant; checked tagged Grafana
  **13.3.1**, Infinity **4.1.6** and Tiled **0.2.7** source for the claims
  above. Removed the demo stack with `down -v` and checked that its labelled
  containers and volumes were gone.

No lab service or hardware was contacted. I did not verify real MOUSE or
MoDaCor output, run a Tiled 0.2.7 server, benchmark a large 14-day catalogue,
or deliberately exhaust memory. I did not repeat the old-client compatibility
job or verify CI itself. Browser coverage did not include a real expired
Grafana session, logout with an already cached explicit-result image, or a
visual pixel-level assertion of every XY null-bound/gap case. The passing
suite/smoke test should not be read as coverage of the reproduced findings.
