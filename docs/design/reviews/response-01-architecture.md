# Architecture review 01

The direction is sound: keep Grafana declarative, central Tiled read-only,
instrument choices in configuration, and processing outside the acquisition
worker. Proceed with the prototype, but introduce a thin adapter during the
spike and make faithful, selectable run intervals its first acceptance gate.
The main gaps are operational semantics rather than plotting: Tiled cannot
prove that an unstopped run is still executing, absence of a processing result
does not establish that processing is expected, and MoDaCor's existing array
sink does not implement the proposed processing-run lifecycle. Correct the
MOUSE stream description, agree the processing publication and retry contracts
early, and trial the basic status dashboard before investing in corrected images
or presets. None of this requires abandoning the maintainer's fixed choices.

Review scope: the brief names revision 2; the supplied plan is **revision 4,
2026-11-15** (`plan.md:3–5`), which is the version reviewed below. References
to the plan are relative to `docs/design/`. Sibling paths are relative to
`~/Code/`. Line references describe the files inspected on that date.
“Blocker” means a gate for the affected feature, not a reason to stop all work.

## Findings

1. **Should fix — §§4.1, 6: make the adapter the default from the spike.**
   **Problem:** the direct-first experiment mixes transport feasibility with
   domain logic already assigned to Python. A successful direct timeline would
   still need replacement for joins, source health, interval discovery and view
   selection. It also contradicts the current contributor rules.
   **Evidence (verified):** `plan.md:193–212` anticipates that replacement;
   `AGENTS.md:34–39` requires one layout module and every panel query to use the
   adapter. Those rules are useful boundaries, not requirements to build a large
   service immediately.
   **Proposed change:** start with only the layout reader, clip projection,
   `/clips`, `/runs/{uid}` and health. Keep a tightly bounded direct Infinity
   query as a diagnostic comparison. An option missing from the plan is to
   prove the panel first using synthetic interval rows, independently of Tiled,
   then connect the same schema through the thin adapter. Keep any necessary
   panel shaping in `/clips/frame`; avoid a bespoke data-source plugin or a
   second database until evidence warrants either.

2. **Blocker — §§4.2, 5, 6: prove run identity and interval fidelity in State timeline.**
   **Problem:** status regions are not automatically distinct, selectable run
   clips. Adjacent successful runs, gaps, zero-duration runs and overlapping
   processing attempts can break the intended representation. A graph with
   attractive status colours is insufficient proof.
   **Evidence (verified documentation; runtime unverified):** Grafana's
   [State timeline documentation](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/state-timeline/),
   labelled **v13.3** when inspected, describes entity states, optional end
   timestamps, null gaps and a setting that merges equal consecutive values.
   It does not establish this project's UID-link or overlap behaviour.
   `plan.md:265–271,296–299` assumes those behaviours without an acceptance case.
   **Proposed change:** on the chosen pinned Grafana/Infinity versions, test
   two touching successes with different UIDs, a real idle gap, unequal
   start/stop times on two lanes, a run crossing the window boundary, an open
   run, and concurrent/reprocessed results. Verify each link selects the exact
   UID, retains the range and filters, and survives refresh. Disable merging
   where appropriate. Define deterministic sublanes or another published panel
   for overlaps if necessary; never silently discard a run. Preserve actual
   timestamps even if a tiny clip needs visual expansion.

3. **Blocker — §§2, 4.4, 6, 8: specify discovery of intervals crossing the window.**
   **Problem:** a finite start-time lookback cannot guarantee inclusion of a
   run that began earlier and remains open, or finished inside the visible
   window. “Bounded by lookback” and the overlap predicate are different
   promises. Paging and historical browsing are also unspecified.
   **Evidence (verified):** `plan.md:62–64,259–260` leaves that tension;
   the old `bluesky-web-plotting/src/bluesky_web_plots/timeline/tiled_source.py:48–53`
   searches only starts after `now - lookback`, and `dataframe.py:42–44,158–160`
   excludes earlier starts again. These are not correct implementations to port
   for overlap semantics. Tiled **0.2.18**'s installed `queries.py:275–302,377–407`
   provides membership and presence queries, but that alone does not prove the
   required PostgreSQL searches or joins work efficiently.
   **Proposed change:** define a half-open view window and exact overlap rule,
   including open runs and boundary equality. Prove a server-filtered union of
   completed-overlap and missing-stop searches, with deduplication and complete
   pagination, on PostgreSQL. Distinguish the requested historical range from
   polling lookback. If discovery of arbitrarily old open runs is capped,
   disclose that limit. Also prove reverse `derived_from` lookup independently
   of the dashboard window: a later correction must remain discoverable from
   an older raw run. Make the PostgreSQL contract check required for this
   behaviour rather than relying only on optional CI.

4. **Should fix — §§2, 4.1, 4.4, 5: distinguish recorded outcome from activity inference.**
   **Problem:** no stop document can mean acquiring, paused, crashed, delayed
   ingestion or an incomplete historical run. Event silence does not establish
   a stall, especially for MOUSE's long exposures. Extending every such clip to
   now would imply more certainty than the data supports.
   **Evidence (verified):** the old `tiled_source.py:20–31,82–95` calls absent
   stops `incomplete`, whereas `plan.md:57–59,204–205,258` implies a live state.
   `mouse_bluesky/src/mouse_bluesky/plans/public.py:90,105–110` defaults the
   sample exposure to 600 seconds and documents pause/crash cases; `atomic.py:60–88`
   emits readings only after each of three acquisitions.
   **Proposed change:** define the stop-status mapping centrally
   (`success → success`, `fail → failed`, `abort → aborted`, other → unknown).
   Represent activity/freshness separately, with last observed event time and
   observation time. Label unstopped runs as open/unconfirmed; expose an aging
   or possibly-stalled indicator with a configurable threshold, not a proven
   execution state. Never let a stale source keep presenting growth as live
   evidence. Test long quiet acquisition, pause, missing stop and delayed stop.

5. **Should fix — §§3, 4.2–4.3: make lane and run identities explicit.**
   **Problem:** the prose uses instrument alone in §3, instrument plus station
   in §5, and omits station from processing inheritance. A fallback to “the
   Tiled path” may accidentally create one lane per run or imply an instrument
   identity absent from the data. Container keys and provenance UIDs also have
   different jobs when they mismatch.
   **Evidence (verified):** `plan.md:98–102,134–137,222,265–266`;
   MOUSE's writer subscribes at its connected root
   (`mouse_bluesky/qserver_profile/profile_mouse/startup/02_tiledwriter.py:26–30`),
   with no explicit per-instrument subtree there. Bluesky **1.14.6**'s installed
   `callbacks/tiled_writer.py:569–576` creates the run keyed by document UID.
   **Proposed change:** define a stable lane key from facility, instrument,
   station and acquisition/processing role, separate from its display label.
   Copy station into processing runs. Configure catalog roots and use the
   parent catalog path only as a labelled fallback; retain an unknown bucket
   when it provides no identity. Keep canonical source/root/container identity
   separate from `start.uid`, return the mismatch diagnostic, and specify how
   `derived_from` resolves against document UIDs. Do not silently rewrite links.

6. **Should fix — §§4.2–4.3: select capabilities within an instrument, not one view per instrument.**
   **Problem:** one `view` with one stream cannot describe both a readings table
   using several streams and a corrected run with both a curve and an image.
   Instrument alone also cannot distinguish measurements from configuration,
   vacuum and processing runs. A fallback relying solely on hints may be empty.
   **Evidence (verified):** `plan.md:227,236–245`; MOUSE has nonmeasurement
   activities (`plans/configure.py:344–353`, `plans/vacuum.py:113–145`) as well
   as the measurement stream. These are legitimate run types on the same
   instrument.
   **Proposed change:** return available views and a default view, with explicit
   stream/field selections, units, axes and availability. Keep instrument
   predicates, field priorities and multi-stream table choices in the view
   table; use a generic resolver in endpoints. Match against available streams
   and processing kind, with deterministic priority. Fall back to hinted
   numeric scalars, then available numeric scalars, then a bounded table or an
   explicit “no plottable data” state. Define unknown-instrument and missing-field
   tests. The named endpoints themselves are broadly well separated; they do
   not yet contain an instrument-specific branch that needs removing.

7. **Should fix — §3: correct and qualify the MOUSE inventory.**
   **Problem:** `snapshot` is described as unconditional and `baseline` is
   omitted; the listed measurement metadata is correct but incomplete. “Only
   ROI and statistics scalars” is stronger than the inspected source proves
   for every deployed device configuration.
   **Evidence (verified):** `mouse_bluesky/src/mouse_bluesky/plans/public.py:137–151`
   sets every listed key and also `entry_row_index` and `sampos`; `snapshot_signals`
   defaults empty at line 89 and gates the snapshot at lines 157–158.
   `startup/04_baseline.py:6–12` installs `SupplementalData` baseline signals;
   `plans/atomic.py:19–28,54–88` makes the three readings in the named stream.
   `devices/eiger.py:75–79,97–100,103–125` configures FileWriter, hints the ROI
   total, splits long exposures into detector frames and sets the output path.
   No external-asset emission is implemented in that class or these plans.
   **Proposed change:** describe baseline, optional snapshot, and three
   measurement events on normal completion; interrupted runs may have fewer.
   Confirm the separate configuration/vacuum runs as written in the plan.
   Retain the conclusion that the FileWriter frame series is not registered
   here, but distinguish that from a definitive inventory of all event fields:
   inspect a synthetic/approved descriptor to verify the latter. Do not treat
   three events as three detector frames. The identity gap is accurate for the
   supplied startups: MOUSE `startup/01_re.py:6` uses `RunEngine({})`;
   electrochemistry `src/electrochemistry_bluesky/queueserver.py:216–218` uses
   the same empty metadata and subscribes its run-file writer. These source
   checks do not establish what every historical deployed run contains.

8. **Blocker for corrected views — §3a: TiledSink is not a Bluesky run publisher.**
   **Problem:** writing corrected arrays through the existing sink does not
   satisfy “same clip model, same layout module.” Merely putting arbitrary
   arrays under a run container would introduce another storage layout.
   **Evidence (verified):** MoDaCor checkout **1.17.1**,
   `src/modacor/io/tiled/tiled_sink.py:60–104,106–146`, creates generic containers
   and arrays, exporting signal, weights and named uncertainties. It emits no
   start, descriptor, event or stop lifecycle. Its runtime sink builder already
   registers `tiled` (`io/runtime_support.py:169–175`), so that capability should
   be reused. Bluesky **1.14.6**'s `callbacks/tiled_writer.py:569–645` instead
   creates `BlueskyRun` and `BlueskyEventStream` specs version **3.0**, stream
   descriptor metadata and internal event tables.
   **Proposed change:** MoDaCor needs a generic publication component that
   composes a processing start at execution start, publishes declared outputs
   in the agreed writer-compatible representation, and finalizes stop only
   after publication succeeds. Choose one tested representation; do not leave
   three alternatives as an implementable contract. If extending TiledSink,
   preserve its existing generic export semantics. Include failed publication,
   failed processing and process death in the lifecycle tests, not only a
   successful synthetic curve. Keep this work in MoDaCor, not the viewer.

9. **Should fix — §3a: version the processing-run schema and define output semantics.**
   **Problem:** the metadata sketch leaves mandatory Bluesky fields implicit,
   names provenance fields only descriptively, omits station and does not say
   what a successful published result guarantees.
   **Evidence (verified):** `plan.md:134–146`; event-model **1.23.1**'s installed
   `schemas/run_stop.json:58–62` requires `exit_status`, `run_start`, `time` and
   `uid`. MoDaCor's `tiled_sink.py:122–138` has named uncertainty components and
   units, not a universal single `I_uncertainty` interpretation.
   **Proposed change:** document a versioned schema with start UID/time,
   stop UID/time/run_start, facility/instrument/station, processing kind,
   nonempty `derived_from`, exact pipeline identity/hash and software/session/
   execution ID keys. Use processing execution times for clip geometry and
   retain raw acquisition times as provenance. Specify stream/field names,
   array ranks and axes, units, uncertainty meaning, and publication readiness.
   Define no-output/no-op success and partial outputs explicitly. Missing
   outputs must not look like a usable correction just because execution
   succeeded. Version incompatible future contracts while tolerating older or
   malformed records in the viewer with diagnostics.

10. **Should fix — §3a, §4.2: define result families and lineage before “latest successful.”**
    **Problem:** latest by what timestamp, for which product and pipeline?
    A later failed reprocessing must remain visible even while an older
    success is the default plot. Multiple parents cannot all be “the raw run,”
    and generic derivation is not necessarily a correction.
    **Evidence (verified):** `plan.md:135,144–152,229` allows a list of parents
    but describes singular navigation. MoDaCor's
    `server/process_failure.py:177–219` can execute a full fallback with another
    runtime ID and retain recovery provenance; `docs/reference/server-api.md:608–617`
    documents that relationship.
    **Proposed change:** define product/pipeline family, completion-time ordering
    and a stable tie-break. Return both latest attempt and latest usable success.
    Separate the primary measured parent from background/calibration provenance
    and show a parent list for genuine multi-input results. Specify whether an
    automatic partial-to-full fallback is one published attempt with recovery
    details or two linked attempts. Use explicit processing kind to label a
    corrected lane; malformed or unrelated `derived_from` should not imply
    MoDaCor correction semantics.

11. **Should fix — §3a: define processing eligibility and file readiness.**
    **Problem:** “finished measurements without a successful processing run”
    is neither an eligibility rule nor a complete work description. It can
    retry failed work indefinitely, include intentionally stopped partial
    measurements, and label unrelated instruments as awaiting MoDaCor.
    **Evidence (verified):** MOUSE `plans/public.py:105–110,173–176` explicitly
    distinguishes `RE.stop()` success from normal completion marked by
    `COMPLETE`. `atomic.py:54–88` writes metadata and acquisition products in
    three directories; `plans/im_craw.py:47–50` writes `im_craw.nxs` separately.
    MoDaCor `server/source_profiles.py:12–27` requires sample and background for
    its existing MOUSE profile; source registration is not just a raw UID.
    **Proposed change:** put MOUSE selection, completion/file validation,
    directory-to-source mapping, background/calibration choice and pipeline
    selection in MoDaCor-examples. Check readable, complete inputs in the
    processing deployment; a recorded `destination` does not guarantee a valid
    local mount. Publish a generic eligibility/product contract so the viewer
    can distinguish not applicable, expected, pending, active, failed and ready
    without testing a MOUSE plan name or filesystem path. Absence of a result
    alone should default to unknown/not available, not “awaiting processing.”

12. **Should fix — §3a: retain the watcher recommendation, but remove the automatic reliability claim.**
    **Problem:** polling does not itself provide catch-up, deduplication or
    recovery. A ten-second poll while processing is active can submit duplicate
    work; a lost response after publication can produce the same ambiguity.
    Failed jobs also need a retry policy rather than being rediscovered forever.
    **Evidence (verified):** `plan.md:161–172` supplies no attempt/claim protocol;
    MoDaCor `server/session_manager.py:75–83,184–218` stores sessions and run
    history in memory, creates a fresh runtime ID and rejects a busy session.
    `server/process_service.py:58–145` executes processing before returning,
    rather than returning a durable asynchronous job receipt.
    **Proposed change:** begin with one serialized watcher owner, persisted
    cursor/work state and a stable request identity derived from raw inputs,
    pipeline configuration and deliberate reprocessing generation. Check active
    attempts as well as successes; use bounded retries/backoff and reconcile
    uncertain HTTP outcomes and stale claims after restart. Handle late-arriving
    runs with overlapping scans or reconciliation, not only a monotonically
    increasing acquisition-time cursor. A small local durable ledger is enough
    initially; multiple watcher replicas can wait. Put these guarantees in
    MoDaCor's orchestration tests, outside this read-only repository.

13. **Should fix — §3a: make source binding and processing one owned operation.**
    **Problem:** the existing API is reusable, but the two-request sequence is
    not atomic. Another client can replace a session's sources between the
    update and execution. The proposed raw UID/provenance also has no typed
    process-request field today.
    **Evidence (verified):** full routes are
    `PUT /v1/sessions/{session_id}/sources`,
    `POST /v1/sessions/{session_id}/sample` and
    `POST /v1/sessions/{session_id}/process`
    (`server/api.py:266–284,358–360`).
    `session_manager.py:138–149` permits source changes independently of active
    run admission. `process_requests.py:43–54` and
    `process_service.py:231–294` contain no `derived_from` or idempotency field.
    **Proposed change:** initially give each watcher exclusive session ownership
    and serialize source update plus process. For shared clients, add a generic
    job request that snapshots source/sink bindings and validated provenance
    together with execution admission. Reuse existing registries and partial-run
    invalidation (`changed_sources`/`changed_keys`). Integrate publication into
    success, failure and automatic fallback paths; an HTTP timeout must not be
    treated as evidence that execution did not happen.

14. **Suggestion — §3a: the proposed watcher home fits, with a narrow boundary.**
    **Problem:** a polling loop inside the web application's request lifecycle
    could accidentally duplicate across workers or couple processing to HTTP
    service restarts. Existing MOUSE examples in core do not justify adding more.
    **Evidence (verified):** MoDaCor `AGENTS.md:12–14` explicitly places new
    instrument implementations and example pipelines in MoDaCor-examples;
    lines 82–86 require reuse of I/O registries and builders. Its server API
    reference at lines 732–738 describes callback/plan clients and transport
    independence, not a requirement that acquisition must initiate processing.
    Bluesky **1.14.6** dispatches callbacks synchronously
    (`run_engine.py:2654–2661`); MOUSE already uses a background `BufferingWrapper`
    for Tiled (`startup/02_tiledwriter.py:27–30`). The installed wrapper's
    `buffer.py:53–84` queues in memory and logs callback failures; this is not a
    durable processing queue.
    **Proposed change:** keep generic orchestration in MoDaCor's server package
    as a separately launched runner with one explicit owner, and Tiled format
    access in its I/O layer. Keep MOUSE rules, pipeline YAML and deployment
    source mappings in MoDaCor-examples. A callback handing off a notification
    remains a reasonable optional latency optimization; it still needs the
    same reconciliation. Do not describe all callback implementations as
    inherently blocking HTTP calls, or the watcher as already implemented.

15. **Should fix — §§4.2, 5, 9: define the operator truth boundary and degraded display.**
    **Problem:** the minimum layout is useful for runs recorded by Tiled, but
    cannot explain a paused/idle queue, a plan that failed before opening a run,
    or a broken ingestion worker. Tiled reachability is not ingestion health.
    A successful health request can coexist with failed searches or stale data.
    **Evidence (verified):** `plan.md:221,272–279,372–374` includes health but
    deliberately excludes unstarted queue items. MOUSE `plans/public.py:123–135`
    checks power and configures energy before opening its run; its Tiled writer
    is asynchronous (`startup/02_tiledwriter.py:27–30`). The old
    `timeline/tiled_source.py:54–62` updates `last_update_time` even on failure,
    so it must not be copied as “last successful poll.”
    **Proposed change:** keep queue control and future clips out of v1. Label
    the page as recorded runs, provide a link to the existing queue UI, and
    expose last successful *data* fetch, last attempt, stale age, partial-result
    errors and sanitized error category. Preserve stale clips visibly marked
    stale on failure; distinguish empty results from unavailable data. Show
    failed/aborted runs and their reasons in an obvious table or summary, and
    processing failures when that feature arrives. Stale/unknown acquisition
    state is the first correctness gap; queue pause/backlog is the first
    contextual gap. Add a read-only queue-status source later only if trials
    demonstrate the need; Tiled-only inference cannot supply it.

16. **Should fix — §§4.2, 4.4: bound endpoint work and qualify immutability.**
    **Problem:** `max_points` bounds returned points, not necessarily bytes read
    upstream. Listing stream extents for every visible clip risks repeated
    per-run reads. A stop document is not a storage-level immutability promise,
    and cached relationships change when later processing appears.
    **Evidence (verified):** `plan.md:225–231,257–261` gives no bounds or cache
    invalidation contract. MoDaCor `tiled_sink.py:27–29,80–96` explicitly supports
    overwriting existing arrays. That does not establish that this project's
    processing publisher will overwrite, but disproves a universal Tiled
    immutability assumption.
    **Proposed change:** validate finite ordered times, field selections and
    positive capped limits; paginate clip lists and disclose truncation. Fetch
    stream data only for selected details or a narrowly defined freshness need.
    Prefer column selection and slicing before transfer. State the decimation
    policy and keep curve axes/uncertainties aligned; report nonfinite values
    predictably. Bound image dimensions/frame selection and handle empty or
    nonpositive log inputs. Cache completed content only under an explicit
    publication contract, with bounded TTL/revalidation otherwise; separately
    refresh `/related` and readiness. Key caches by configured source/root,
    run identity and relevant rendering/view settings.

17. **Should fix — §§4.1–4.2, 7, 9: finish the browser image and credential route.**
    **Problem:** an adapter-held Tiled key solves upstream authentication, but
    does not by itself make an image endpoint reachable or authorized from the
    browser. Grafana sign-in does not protect a separately exposed FastAPI
    service. The direct-access route must also keep secrets out of dashboard
    queries and URLs.
    **Evidence (verified architecture; deployment unverified):**
    `plan.md:201–202,231,243,342–349,373–374` names an HTML/image panel and a
    compose-internal adapter without defining that browser route. Grafana's
    [Infinity authentication documentation](https://grafana.com/docs/plugins/yesoreyeram-infinity-datasource/latest/)
    lists API-key authentication; therefore the adapter is not inherently
    required just to hide a key for JSON requests. The inspected Infinity
    [changelog](https://grafana.com/grafana/plugins/yesoreyeram-infinity-datasource/changelog/)
    identifies **4.1.3**; its documentation is rolling, not a project pin.
    **Proposed change:** use backend Infinity queries with server-side secured
    credentials, an allowlisted fixed upstream and a read-only Tiled principal.
    For images, prove a same-origin authenticated proxy route or another
    explicitly tested published-panel route that supplies no Tiled key to the
    browser. Keep the adapter private except for that controlled route. Escape
    metadata in labels/HTML and return sanitized health errors. Do not disable
    sanitization globally to make an image work. Record the actual tested
    Grafana/plugin versions and treat the image route as a separate spike gate.

18. **Suggestion — §§2, 6: carry over decisions, not unverified old implementation behaviour.**
    **Problem:** the selected carry-over is mostly right, but the plan calls
    presets “step 4” at `plan.md:68–69` while current step 4 is run views.
    It also equates a shared hover crosshair with the old persistent playhead.
    **Evidence (verified):** all five old design documents and all modules in
    `bluesky-web-plotting/src/bluesky_web_plots/timeline/` were read. The old
    `ARCHITECTURE.md` §§6–7 separates domain projection, selection and cursor;
    `CONFIG_SPEC.md:420–430` keeps selection/cursor out of configuration.
    `OPERATIONS.md` §1.2 explicitly accepts central-Tiled polling. The source
    discrepancies in findings 3, 4 and 15 show why porting needs fresh tests.
    **Proposed change:** retain the clip invariant, UID diagnostics, source
    health, explicit time window and pure table projection; dropping ZMQ/merge
    complexity is appropriate. Keep strict versioned preset validation and
    useful predicate/label semantics for the later customization step, without
    importing the full old UI/configuration surface now. Treat hover crosshair
    as the agreed v1 interaction and document persistent playhead/highlight as
    deferred unless the spike proves them. Correct the stale step reference.

19. **Should fix — §§6–8: reorder the gates around a small vertical slice and early operator trial.**
    **Problem:** step 1 needs realistic fixtures and PostgreSQL before step 2
    creates them; external raw images are requested as if MOUSE registered
    them; the operator trial comes only after corrected views. The foundational
    processing contract is deferred until its consumer is being built.
    **Evidence (verified):** `plan.md:291–319,355–365` has that order;
    the file registration and publisher gaps are verified in findings 7–8.
    **Proposed change:** first pin a tested Tiled/Bluesky/event-model/Grafana/
    Infinity combination and create the smallest real-writer fixture catalog
    on PostgreSQL. Prove search, paging, open/closed overlap and layout access;
    independently prove the panel cases in finding 2; then connect them through
    the thin adapter with summary, fallback view and honest health. Trial this
    with operators and request instrument identity metadata before the trial.
    Agree the processing schema in parallel early, then gate corrected views on
    the MoDaCor publisher contract. Use a synthetic 2D result to test that path,
    not an assumed raw Eiger asset. Preserve two-instrument fixtures from the
    outset. Delay presets and custom panels as planned. Do not require the
    broader Python/platform matrix until dependencies resolve and the first
    vertical slice works; keep the declared compatibility honest.

20. **Should fix — AGENTS.md; §§4.1, 7–8: resolve instruction conflicts and add enforceable contracts.**
    **Problem:** the conventions mostly match the siblings, but two literal
    prohibitions make planned work impossible: no project code may write to
    Tiled, yet the demo seeds it; every fixture must use a RunEngine, yet
    processing fixtures are composed with event-model. The direct Infinity
    experiment is also forbidden by the adapter-only rule.
    **Evidence (verified):** `AGENTS.md:24,34–39,49–54` versus
    `plan.md:193–196,301–305,359–365`. The tooling, strict typing, locked uv,
    privacy, schema compatibility and release approach align with
    `electrochemistry_bluesky/pyproject.toml:1–16,44–62,81–101`,
    `CONTRIBUTING.md`, `docs/technical/development.md:7–37` and
    `.github/workflows/ci.yml:22–26,31–54`. Requiring attrs validation is a
    reasonable stronger project rule than MoDaCor's preference at
    `MoDaCor/AGENTS.md:20`.
    **Proposed change:** distinguish read-only runtime/production access from
    writes to isolated disposable fixture/demo catalogs. Require acquisition
    fixtures from real RunEngine/ophyd devices and processing fixtures from
    valid event-model documents through the actual publisher/writer contract;
    prohibit hand-made fake Tiled trees in both. Either adopt adapter-first or
    explicitly permit the isolated throwaway direct spike. Add the strict docs
    build and dashboard/provisioning validation to checks when scaffolded, and
    require the PostgreSQL search contract once introduced. State that runtime
    code never starts acquisition or processing, dependency fixes belong
    upstream, API/preset changes update contracts and migrations, and tests
    never contact production services or hardware. Separate source health,
    observed run state and processing eligibility in the documented model.

## Verified evidence and limits

Verified by reading: the supplied revision-4 plan and instructions; all files
in the earlier design directory and timeline package; the requested sibling
conventions; MOUSE startup and relevant acquisition/configuration/vacuum/device
code; MoDaCor's API reference, request/execution/session/source handling,
fallback publication path, runtime sink registry and TiledSink. MoDaCor's
checkout declares version **1.17.1**. Installed source checked for relevant
library claims was MOUSE's **Bluesky 1.14.6**, **Tiled 0.2.8**, **ophyd 1.11.0**
and **event-model 1.23.1**, plus MoDaCor's development **Tiled 0.2.18**. These are
local environment versions, not evidence of the central server's versions or
the future project's pins. External Grafana documentation was labelled
**v13.3**; Infinity's inspected changelog was **4.1.3**. Neither was run here.

Not verified: central Tiled contents, authorization policy, actual MOUSE
descriptors or detector files, deployed writer/server versions, PostgreSQL
query correctness/performance, panel UID linking/overlap/conditional-display
behaviour, uncertainty rendering, browser image authorization, or processing
restart/retry behaviour in a running deployment. Performance concerns,
operator priorities and the proposed minimum contracts are review judgments
based on the cited source, not measured failures. MoDaCor-examples was not
audited; the proposed repository split is checked against MoDaCor's instructions,
not against an existing watcher implementation there.

No code, dependencies or configuration were changed. No services, hardware or
production data were accessed and no tests were run: this task is a source and
architecture review, and this repository has no runnable scaffold yet. Only
this response file was added.
