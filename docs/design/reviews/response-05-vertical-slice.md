# Review 05: step 3 vertical slice

Reviewed 2026-11-16 at `5ad05fb`. **Verdict:** the vertical slice works end to
end, and its main architectural boundaries hold, but I would fix the two
operator-visible blockers below before the lab trial: missing data can look
like a complete window, and failed or partially failed sources can look
healthy. All 142 tests, including PostgreSQL and the real RE Manager, pass;
the isolated demo smoke test and Tiled 0.2.8 client check also pass. Those
checks do not cover several important failure paths. Additional probes
reproduced incorrect freshness extents, suppressed activity errors, duplicated
readings with incorrect units, and errors on the dashboard's initial empty
selection. This is a useful prototype foundation, with specific correctness
work remaining before operators can rely on what it omits or marks fresh.

## Findings

1. **Blocker — source failures are not reliably visible on the operator dashboard.**
   `grafana/dashboards/timeline.json:211–255`;
   `grafana/dashboards/health.json:45`;
   `src/event_timeline_tiled/adapter/app.py:127–143`;
   `src/event_timeline_tiled/clips.py:136–146`.
   The timeline's Data age query selects only `data_age_s`. A root which has
   never succeeded contributes `null`, so another root's recent success can
   leave a green age while an entire instrument is absent. A filtered window
   search can also fail while the independent, unfiltered root probe succeeds:
   `/health` then returns `status=ok` and a separate `last_error`, but neither
   provisioned dashboard selects `last_error`. Cached closed clips retain their
   outcome colour and their `stale` helper is hidden. Together these paths fail
   the plan's distinction between an empty result and unavailable data. This
   conclusion follows from the actual query columns and Python branches; the
   null-only root is especially important because there need be no stale clip
   to draw. **Change:** expose and display the window-fetch status of every
   configured root, including never-successful roots and partial failures.
   Keep live probe status, recorded run status and activity separate. Add an
   explicit warning on the Timeline page whenever its data is incomplete or
   stale, rather than relying on a numeric age or navigation to Health. Test a
   missing second root and a failing filtered search with a successful probe.

2. **Blocker — truncation disappears before the timeline reaches Grafana.**
   `src/event_timeline_tiled/adapter/app.py:192–199,349–372`;
   `src/event_timeline_tiled/layout.py:130–151`;
   `src/event_timeline_tiled/tiled_http.py:194–199`;
   `grafana/dashboards/timeline.json:292,676,813`.
   `/clips/frame` discards `WindowResult.truncated` and `unplaceable`. The runs
   tables select only `clips`, and the data table selects only `rows`, losing
   both kinds of truncation information. Above the per-root cap, the window
   union's final ascending sort and slice can discard open runs even after
   fetching them; the operator sees no indication that runs are missing. The
   individual search cap also returns `truncated=True` at exactly the limit
   even when `links.next` is null: a two-item terminal page with
   `max_items=2` reproduced this false positive. Tiled 0.2.21's
   `CatalogContainerAdapter.items_page` already uses a look-ahead row to decide
   whether another page exists. **Change:** distinguish exactly-at-cap from
   more-than-cap, define which runs survive a cap, and provision a visible
   completeness/truncation query alongside the frame and data panels. Keep
   existing frame consumers compatible; an additive companion endpoint is
   sufficient. Cover exact-cap, cap-plus-one, combined open/closed overflow and
   the dashboard warning, rather than only asserting the adapter's boolean.

3. **Should fix — one failed root freezes healthy roots' open clips.**
   `src/event_timeline_tiled/timeline.py:69–104`;
   `src/event_timeline_tiled/clips.py:179–182`.
   `open_until` is the minimum last success across roots, and the frame applies
   that one timestamp to every open clip. In a two-root probe, both initially
   succeeded at 1000; at 1100 root A failed and root B succeeded. Both clips
   ended at 1000, although B was explicitly `stale=False`. A failing root can
   keep a healthy instrument's clip frozen indefinitely, including when the
   failed root has no overlapping cached runs. **Change:** retain the successful
   fetch time per root, and project each clip using its own source's timestamp.
   Test that an outage stops only that source's open clips from growing.

4. **Should fix — failed activity reads are silently treated as usable activity.**
   `src/event_timeline_tiled/timeline.py:107–129`;
   `src/event_timeline_tiled/layout.py:209–223`.
   `last_event()` swallows every `SourceError`, returns the old value or `None`,
   and records neither the failure nor its attempt time. A successful metadata
   search therefore produces fresh clips and healthy source state even when
   event tables are forbidden or unavailable. With two open runs, two window
   requests one second apart made four failed activity reads, all clips stayed
   `stale=False`, and both root errors stayed null. The fallback can turn a
   data-access problem into a possibly-stalled inference, and the promised
   60-second cadence applies only to successful reads. **Change:** cache attempt
   time and activity-read health independently of the last successful value;
   throttle failures too, report unavailable/stale activity explicitly, and do
   not present an inference based on a failed observation as current. Include
   concurrent requests in the cadence test: the present lock does not cover the
   check-and-fetch operation. Keep the recorded outcome independent.

5. **Should fix — stale records inherit successes from windows which never refreshed them.**
   `src/event_timeline_tiled/timeline.py:78–92,132–144`.
   The stale cache accumulates every record ever seen, but has only one root
   timestamp. A probe cached an open run at 1000, successfully fetched a
   disjoint window at 2000, then failed while revisiting the original window.
   The old open record reappeared with `open_until=2000`, although it had last
   been observed at 1000 and could have closed in between. `_overlaps` also
   omits the open-run horizon, so a cached run can reappear after ageing beyond
   the documented 21-day search horizon. A failure resets the result's
   truncation to false even when the cached fetch was truncated. **Change:**
   cache the provenance and completeness of a successful window, or attach a
   last-observed time to each record and apply the complete current-window
   predicate during fallback. A stale open run may legitimately remain open
   during an outage; it must stop at its own last observation and remain
   explicitly uncertain. Test window changes, closure between observations,
   horizon crossing and fallback from a truncated fetch.

6. **Should fix — multi-stream tables duplicate shared fields and can mislabel units.**
   `src/event_timeline_tiled/views.py:175–181`;
   `src/event_timeline_tiled/adapter/app.py:352–368`.
   `_table_view` concatenates field names from all streams without
   de-duplication and combines units in a dictionary keyed only by field name.
   `_table` then reuses that field list and units dictionary for each stream.
   With one event in each of baseline and snapshot, both containing `shared`,
   the endpoint returned four rows instead of two. Assigning V to baseline and
   A to snapshot made both streams report V, because the later baseline entry
   overwrote the unit. The current fixtures use disjoint field names and miss
   this. **Change:** resolve fields and units by `(stream, field)` when producing
   table rows; preserve existing outward field names while adding any needed
   view-contract information. Test a shared field in two real writer-produced
   streams, including differing descriptor units and exactly-at-limit output.

7. **Should fix — closing between the two searches makes a run disappear for a refresh.**
   `src/event_timeline_tiled/layout.py:130–148`.
   The closed search runs first. An overlapping run which is still open during
   that search, but receives its stop before the open search, matches neither
   response. De-duplication cannot restore it. This is a normal acquisition
   transition, independent of whether paging parameters are preserved. In
   Tiled 0.2.21, each `items_page` executes its own database query/session;
   the two searches have no shared snapshot. **Change:** fetch open candidates
   first, then closed candidates, and prefer the closed record on duplicate
   keys. This prevents that hole for normal one-way open-to-closed transitions.
   Document that concurrent catalogue changes still do not provide snapshot
   isolation, and add a deterministic closure-between-searches regression.
   ID cursor paging itself avoids ordinary offset shifts under append-only
   insertion; it cannot guarantee a snapshot or capture every late commit or
   newly matching record behind an already traversed cursor.

8. **Should fix — the initial dashboard selection produces four panel errors.**
   `grafana/dashboards/timeline.json:26–33,537,589,671,737`;
   `deploy/smoke_test.py:150–174`.
   The hidden `run` variable defaults to an empty string, yet all detail panels
   immediately issue run-specific requests. Against the actual Grafana 13.3.1 /
   Infinity 4.1.6 stack, the Selected run, Streams, default data and time-series
   queries all returned errors when selection was empty. The smoke test passes
   because it substitutes a known seeded UID before querying every panel.
   **Change:** give empty selection an intentional “Select a run” state, using
   a declarative query arrangement and, if needed, an additive adapter selection
   endpoint. Preserve 404 for an actual unknown UID. Exercise empty selection
   through Grafana in the smoke test as well as selected MOUSE and generic runs.

9. **Should fix — the queue wrapper retains the control-capable API object.**
   `src/event_timeline_tiled/queues.py:46–58`;
   `tests/unit/test_queues.py:47–57`;
   `docs/technical/queues.md:5–8,23`.
   Bound methods retain their instance. Without issuing any control request,
   the probe confirmed `wrapper._status.__self__ is api` and that
   `wrapper._status.__self__.queue_start` is callable. `_queue_get` and `_close`
   retain it too. The test compares stored values by identity and examines
   public attribute names, so it passes despite the module's stronger claim
   that the object is dropped and only reads are reachable. This is an internal
   Python escape path, not a demonstrated HTTP endpoint for queue control;
   the adapter's normal paths do issue reads only. Checked with the installed
   bluesky-queueserver-api 0.0.14 implementation. **Change:** make the guarantee
   accurate: a narrow programming interface is not an introspection/security
   boundary. Add a transport-level assertion that the adapter emits only
   `status` and `queue_get`, and put any required enforcement in the HTTP
   server's scoped key or a genuinely isolated allow-list boundary. Do not
   replace bound methods with closures and claim that reflection is impossible.

10. **Should fix — different lane keys can still produce the same final lane.**
    `src/event_timeline_tiled/clips.py:33–36,57–72,184–192`.
    Facility prefixing does not make display names unique. For example,
    `(facility=A, instrument="X Y", station=None)` and
    `(facility=A, instrument="X", station="Y")` both become `A X Y`.
    An instrument name containing ` /2` can also collide with another lane's
    generated sub-lane. Since Grafana partitions on the final string, these
    independent allocations can land in one series with overlapping clips,
    defeating the no-dropped-runs guarantee. The slash-joined textual lane key
    is ambiguous for components containing slashes as well. **Change:** make
    final display names unique across all lanes and generated sub-lanes, with
    deterministic disambiguation, and use an unambiguous encoding for stable
    lane identity. Record any incompatible textual-key change in migrations.
    Test name collisions, missing facility versus a literal `-`, and sub-lane
    suffix collisions. The ordinary two-facility case already works.

11. **Should fix — zooming to a zero-length run has no valid time range.**
    `src/event_timeline_tiled/clips.py:180–205`;
    `grafana/dashboards/timeline.json:950–952`.
    Display widening does not pad the link helpers. A run with start and stop
    both 100 produces `clip_from_ms=clip_to_ms=100000`, and the Grafana link
    uses those equal values directly. Distinct sub-millisecond times can also
    round to the same millisecond. This contradicts plan §8's explicit padding
    requirement; the successful spike does not establish this new case.
    **Change:** keep the true extent available, add separate padded zoom bounds
    if necessary to preserve that contract, and make the link use a strictly
    positive finite interval. Cover equal times and rounding collisions.

12. **Should fix — the data limits do not fully bound returned points or avoid unnecessary work.**
    `src/event_timeline_tiled/adapter/app.py:349–381,390–410`;
    `src/event_timeline_tiled/layout.py:201–207,216–222`;
    `src/event_timeline_tiled/timeline.py:57,140–144`.
    A 20-event time series with `max_points=10` returned 11 points: every other
    event plus the final event. Each selected field multiplies the long-format
    row count. After reaching the table's 2000-row limit, only the innermost
    loop breaks; the code continues walking every remaining event and fetches
    subsequent streams. Activity reads unnecessarily download `seq_num` and
    sort all rows just to take the maximum time. The activity cache never
    evicts identities, and the nominal 50,000-record stale-cache pruning is
    not a hard bound when all retained records are recent. Whole-column reads
    are a known, documented Tiled 0.2.21 limitation, not a newly discovered
    regression; the additional scans and unbounded caches are avoidable.
    **Change:** define and enforce the point budget including the final point,
    stop table traversal when sufficient evidence of truncation is available,
    use a time-only unsorted activity read through the layout module, and bound
    both caches. Measure latency, bytes and peak memory on a representative
    14-day event count before offering high-cadence runs at automatic refresh.
    A per-request HTTP timeout alone is not an end-to-end work/memory budget.

13. **Suggestion — make flattened metadata bounded and consistently JSON-like.**
    `src/event_timeline_tiled/adapter/app.py:51–82,228–232,339–342`.
    Scalar non-finite floats become null, but lists containing dictionaries use
    `json.dumps` with its permissive NaN default, and nested lists without a
    dictionary use Python string representations. The probe produced embedded
    `[{"v": NaN}]` and a comma-joined representation for a nested array. These
    are strings in the outer response, so this is not evidence of invalid HTTP
    JSON; it is an inconsistent metadata contract. Flattening also has no depth,
    row or cell-length budget, and `/runs/{uid}` computes it even when a panel
    selects only `summary`. **Change:** normalize non-finite values recursively,
    serialize non-scalar lists consistently, cap depth/rows/value size with
    explicit truncation information, and consider an independently fetched
    metadata view. HTML in the current ordinary table cells is not a confirmed
    XSS finding: Grafana 13.3.1's
    [AutoCell](https://github.com/grafana/grafana/blob/v13.3.1/packages/grafana-ui/src/components/Table/TableNG/Cells/AutoCell.tsx)
    passes formatted strings as React children. Preserve that rendering path
    and test hostile labels; do not globally disable sanitising or add a
    blanket HTML-escaping pass that double-escapes text cells.

14. **Suggestion — finish the small operator-facing parts of the version-1 dashboard.**
    `grafana/dashboards/timeline.json:10,50,83–103,567–613`;
    `docs/user/index.md:54–55`;
    `docs/design/plan.md:226–250,393–414`.
    The endpoint has `running_since`, but the queue panel omits it; no panel
    queries the existing stream-fields endpoint; the page does not state its
    21-day open-run horizon; and refresh defaults to 30 seconds versus the
    plan's 5–10 seconds. These are smaller gaps in what “version 1 dashboard”
    promised, rather than reasons to implement corrected views early.
    **Change:** display the running-item start time and horizon, provide a
    channel/field inspection route from the selected stream, and either choose
    a measured refresh default or document the deliberate change. Add targeted
    checks for these affordances rather than expanding dashboard transformations.

## Conformance and version checks

Instrument-specific names, field choices and stall thresholds are confined to
`views.py` in the runtime adapter. The layout module owns run/stream/table
navigation and time-window predicates. Status mapping is centralized and
correct for `success`, `fail`, `abort`, unknown statuses and absent stop
documents. Activity uses a strict `age > threshold`, falls back to start time
when no event is known, and never labels an open run “running”. The default
thresholds are 1200 seconds for MOUSE and 1800 for the fallback. Neither
dashboard tests instrument names; panel requests use Infinity's backend
parser and only the final-lane partition transformation is present. The
runtime Tiled reader issues GETs. Queue-control calls are confined to the
explicit demo/test drivers. Related-run selection and corrected image/curve
endpoints remain step-6 work; their absence is not a step-3 finding.

For a static catalogue, the two window predicates correctly implement
`start < to` and `stop >= from`, with open runs restricted to
`to-horizon <= start < to`; the three stated window boundaries pass on both
SQLite and PostgreSQL. Explicit-null or malformed stop documents are not the
same as an absent stop key and are outside that normal writer contract.
Closed records with an unusable stop time must not be interpreted as evidence
of continuing acquisition: `frame()` currently substitutes the observation
time if such a record reaches it. Add malformed-document reporting when
hardening this tolerance rather than relying on that display fallback.

I checked installed **Tiled 0.2.21** source, specifically
[`pagination_links`](https://github.com/bluesky/tiled/blob/v0.2.21/tiled/server/core.py#L122)
and
[`CatalogContainerAdapter`](https://github.com/bluesky/tiled/blob/v0.2.21/tiled/catalog/adapter.py#L1964).
Repeating the original filters and requested fields while replacing only the
`page[...]` parameters is the right fix. It handles ID cursors and offset
fallback without following a foreign host/path, and retains repeated query
parameters. The server's default cursor is a node ID, not a time cursor or
snapshot token. Existing two-item-page contract tests prove retained filtering
on static PostgreSQL and SQLite catalogues. Findings 2 and 7 qualify the cap
and concurrency guarantees. The documented D-006 query-plan result should be
treated as the measured scale point, not proof of indexed time filtering at
arbitrary catalogue size; I did not independently rerun EXPLAIN.

For **Grafana 13.3.1**, the source of
[`prepareTimelineFields`](https://github.com/grafana/grafana/blob/v13.3.1/public/app/core/components/TimelineChart/utils.ts#L337)
confirms the first and second typed time fields become start and end. Explicit
`timestamp_epoch` columns for `start`/`stop` and numeric zoom helpers therefore
preserve the spike's field-order solution. `state` is correctly the visible
mapped value; the separate recorded `status` remains hidden. Sub-lanes are
allocated against widened display extents, so widening does not introduce
overlaps within an otherwise uniquely named lane. The frame still supports
the ordinary touching-run/gap/overlap design, subject to findings 3, 10 and 11.
The full browser click gate and its 20-lane benchmark were not rerun.

For **Infinity 4.1.6**, checked
[`jsonBackend.go`](https://github.com/grafana/grafana-infinity-datasource/blob/v4.1.6/pkg/infinity/jsonBackend.go),
[`postprocess.go`](https://github.com/grafana/grafana-infinity-datasource/blob/v4.1.6/pkg/infinity/postprocess.go)
and its pinned jsonframer 1.3.0/gframer 1.1.13 implementation. JSON rows pass
through maps, so Python dictionary insertion order is not a Grafana field-order
contract. The `timeseries` postprocessor invokes `LongToWide` when appropriate.
Real `/api/ds/query` probes with one field and two fields returned a real time
field and respectively one and two numeric series labelled by `field`.
The seconds macros worked through the real dashboard smoke queries; the
millisecond data/frame fields and second-based search endpoints agree with
`docs/technical/endpoints.md`. Selection links reference the hidden UID and
preserve the URL time range; their browser navigation/refresh behavior is
covered by the earlier spike, not newly established by these HTTP probes.

For **bluesky-queueserver-api 0.0.14**, checked the installed HTTP/0MQ
constructors, reload methods and `comm_base.py` key/header implementation.
The configured HTTP base URL and `set_authorization_key(api_key=...)` are
correct; the library sends `Authorization: ApiKey ...`. Checked released
**bluesky-httpserver 0.0.14**
[`core_api.py`](https://github.com/bluesky/bluesky-httpserver/blob/v0.0.14/bluesky_httpserver/routers/core_api.py)
and
[API-key instructions](https://github.com/bluesky/bluesky-httpserver/blob/v0.0.14/docs/source/usage.rst):
`read:status` and `read:queue` are the appropriate scopes, and explicit scopes
create a fixed subset. A single-user server key is not evidence of that
restriction. No live authenticated HTTP-server integration was run; add it
before relying on that deployment path. The documented 0MQ risk is accurate:
the unencrypted control port supplies no per-client read-only authorization.
For **bluesky-queueserver 0.0.26**, `plan_queue_ops.py:1695–1698` sets
`properties.time_start` when moving a plan to the running item; the real
manager test confirms its availability. It is the manager's start of the
current item execution, not necessarily a RunEngine start-document time or
continuous active time across a pause. Status and queue are separate reads,
so a transition can also yield a temporarily inconsistent pair; matching
`running_item_uid` is a possible later refinement.

## Validation and remaining trial checks

Ran `uv run --locked` checks: `ruff check .`, `ruff format --check .`,
`mypy src`, and `mkdocs build --strict --site-dir /tmp/ett-review-05-docs`;
all passed. Ran the complete suite with `ETT_REQUIRE_REDIS=1`: **142 passed**,
including SQLite, disposable PostgreSQL 16 and the real Redis/RE Manager tests.
Only existing dependency deprecation warnings were reported. The initial
duplicate test invocation was interrupted and superseded by that complete run.
Dashboard/provisioning checks are included in pytest.

On Docker Desktop 29.9.3 / Apple silicon, validated Compose with the demo
profile, then started the separate project `ett-review05` on host ports 18085
and 18086 with fresh generated secrets and separate volumes. Reused existing
local images; hashes confirmed that the installed runtime adapter modules
matched the reviewed source (the adapter image's unused demo `queue_driver.py`
differed). The existing smoke script passed all nine dashboard queries,
timeline clips, queue status, access checks and anonymous-access checks.
The existing old-client script passed its Tiled 0.2.8 / Bluesky 1.14.6 writes,
adapter status checks and 9/9 access checks against the disposable server.
A temporary runner supplied the separate project/settings to those scripts;
no repository configuration was edited. Additional Grafana HTTP queries
verified empty selection errors and one-/two-field time series. Temporary
Python probes exercised the failure, cap, shared-field, lane-collision, zoom,
metadata and point-budget cases reported above. Removed the entire review
stack with `down -v`. No production service or hardware was contacted.

The new fixture cases required before step 3 are present: acquisition failure,
UID mismatch, the stopped-success completion distinction, coherent retry
attempts, success followed by failure, stop-time ties and partial outputs.
Acquisition fixtures use RunEngine/ophyd through TiledWriter and processing
fixtures use event-model through TiledWriter. PostgreSQL absence fails rather
than skips; CI installs Redis and sets `ETT_REQUIRE_REDIS=1`. The Ubuntu job
structure, isolated test ports, Compose lifecycle and old-client invocation
look consistent with those requirements; I found no definite Ubuntu-specific
failure. I did not execute GitHub Actions or build fresh Linux amd64 images in
this review. Current coverage remains weak on failed activity reads, multiple
roots/windows, actual decimation and row limits, repeated fields, empty
selection, exact-cap and exact-horizon boundaries, and concurrent run closure.
Put the focused regressions beside the fixes; a larger passing happy-path
count would not establish those properties.

Before the operator trial, resolve the blockers, then check the applicable
correctness findings against captured lab descriptors. Set facility/instrument
in the instrument repository, confirm the lab server version and explicit
catalogue roots, run the read-only scope/data access check, and capture only
the already-planned anonymized schema information. Measure the lab window
queries and representative event-column costs, including source outages and
recovery, before choosing refresh settings. Verify TLS, public Grafana URL,
secure cookies and Viewer accounts before access from other machines, as
already required by plan §10.2. Any Tiled upgrade/migration or instrument-side
metadata changes are separate work in their owning systems. No lab-version
compatibility, real schema, lab query plan, live HTTP queue authorization,
fresh browser interaction gate or 14-day-load result is claimed here.

Only this response file was added; no implementation file was changed and no
commit was made. The pre-existing untracked `.vscode/` directory was left alone.
