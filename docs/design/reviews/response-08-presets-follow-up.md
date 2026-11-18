# Review 08: default preset and stream unfolding

Reviewed 2026-11-18 against draft 3 and `git diff c5e4f85 -- docs/design/`.
Scope: the two new decisions and their consequences; review 07's other
accepted findings are not reopened. Only this response was added; no commit
was made.

## Verdict

Accept the single lane mechanism and the default preset as an ordinary shipped
file: the added root path and tuple grouping express the intended identities,
and lexicographic component ordering puts acquisition before processing within
the same root/facility/instrument/station tuple. Today's names need not be
preserved. One-lane stream unfolding is also a useful, declarative extension,
but its design is not ready to implement unchanged: the claimed read bound is
insufficient, its status query contradicts the dashboard wiring, and copying
today's open-run geometry or scalar-stream detection would draw incorrect
extents or omit processing streams. Specify those behaviours, closure-aware
caching, template rendering and selection identity before freezing v1. The
findings below do not require restoring the fixed lane implementation.

## Findings

1. **Blocker — §6.5, “Cost is bounded”; §§6.3, 8, 13: 200 runs is not a data-read bound.**

   **Problem:** A cold request may read every timestamp of every stream of
   200 arbitrarily long runs. Stream count, event count, response size and
   total time are not bounded by that number. The proposed finished-run
   cache also lacks an eviction/size limit. Reducing the selected lane to
   one does not bound a single long run.

   **Evidence:** Checked installed **Tiled 0.2.21**'s
   `server/router.py::get_table_full/table_full`: there is a column selector
   but no row-range argument, and `entry.read(column)` runs before the
   response-size check. The adapter's `TiledReader.table_columns` then loads
   the JSON response in full. Fourteen days at just 1 Hz is 1,209,600
   timestamps per stream; 200 such float64 columns contain 1,935,360,000
   bytes before JSON and Python-object overhead. That arithmetic is an
   illustrative workload, not a benchmark. `Layout.event_columns` also reads
   `seq_num` and sorts all rows, so using it for unfolding would exceed even
   the stated “time column only” work.

   **Proposed change:** Keep the run cap, but add explicit stream, event/byte,
   request-time and concurrency budgets plus bounded cache entries/size.
   Use closed-run counts to refuse known oversized reads before fetching;
   define how unknown-size open streams are refused or read under a bounded
   transfer/decode budget. Be explicit that client cancellation/byte limits
   alone cannot bound the server's preceding scan. If a hard server-work bound
   is required, a cheap upstream min/max aggregate is needed; until then,
   report that limitation instead of promising one. Add a `Layout` operation
   that reduces only `time` without sorting, shares concurrent reads per run,
   and returns observations/errors under those budgets. A second endpoint
   request or browser refresh must not bypass the per-run read throttle.

2. **Blocker — §§6.5, 9–11: the dashboard cannot report unfolding completeness as written.**

   **Problem:** §6.5 gives `unfold` to both `/clips/frame` and `/clips/status`,
   but §11 says the frame passes it and “nothing else does”. Thus the Data
   status panel would certify a folded result while the visible frame omitted
   streams, hit the unfolding cap, or had stream-read errors. “The frame says
   only the latest are unfolded” also has no defined representation: today's
   frame is a bare list with no notice envelope.

   **Evidence:** The contradiction is at `presets.md` lines 296–308 and the
   §11 unfolding bullet. `grafana/generator/timeline_dashboard.py::header`
   reads `/clips/status.issues`; `timeline()` reads the frame separately.
   The current endpoint contract explicitly says the frame itself says
   nothing about completeness.

   **Proposed change:** Pass the same `preset`, range and `unfold` to the
   frame and Data status queries. Share bounded stream observations/render
   diagnostics between them so the status request does not repeat cold reads
   independently. Define status fields/issues for unresolved lane, attempted
   and omitted runs, empty/pending/unreadable streams, extent age and each
   unfolding budget. Use status notices, not a fake timeline clip, to disclose
   omission. An absent or empty `unfold` means folded; a valid but absent lane
   is a normal notice with the ordinary frame. A render failure must not turn
   a run's recorded status into failure or change `/health`'s source checks.

3. **Should fix — §6.5, §12: `has_table` would wrongly exclude external-only streams.**

   **Problem:** Existing stream/activity helpers use `StreamInfo.has_table`
   as a scalar-data eligibility flag, not as proof that an `internal` table
   exists. Reusing that condition for unfolding would omit the processing
   streams the brief explicitly expects to show.

   **Evidence:** With **Bluesky 1.14.6**, **event-model 1.23.1** and
   **Tiled 0.2.21**, on disposable SQLite and PostgreSQL **16.16** catalogues,
   both processing fixture streams had `has_table == false` yet each had
   one readable timestamp in its `internal` table: extent `[219, 219]`,
   matching `stop.num_events == 1`. The probe read only `time`, without
   reading the external arrays. `layout.py::streams` derives `has_table`
   from non-external descriptor fields; `last_event_time` skips false flags.
   `event_columns(run, stream)` would work for these tables but unnecessarily
   reads `seq_num` and sorts.

   **Proposed change:** Give stream-time observation its own `Layout` method,
   covering streams with only external fields as well as scalar streams.
   Distinguish absent-before-flush tables, declared zero-event streams and
   positive-count streams with missing/unreadable data. Do not infer empty
   from `has_table`. Preserve the activity contract explicitly when sharing
   observations: collecting additional external-stream extents must not
   silently redefine which events determine existing activity inference.
   Rename/document the misleading flag when implementing the helper, and
   test its independence from unfold eligibility.

4. **Should fix — §§6.2, 6.5, 10: stream geometry needs an explicit rendering contract.**

   **Problem:** A stream's measured last event is different from an open run's
   observation time. Today's `frame` ignores a stop extent when status is
   open and draws to `observed_at`; applying that code unchanged would extend
   an unfolded stream across periods without recorded events. “Keeps the
   run's helpers” also leaves stream extent versus Zoom-to-run extent unclear.

   **Evidence:** `clips.py::frame` selects geometry using `Status.OPEN`.
   In the **Tiled 0.2.21/Bluesky 1.14.6** probes, primary events in sequence
   order had times `180, 120, 160`; their correct min/max was `[120, 180]`,
   not the first/last rows. An external processing stream with a single event
   had a zero-duration true extent. A baseline with snapshots at 110 and 190
   produced the span `[110, 190]`; a solid span does not imply continuous
   recording between those snapshots.

   **Proposed change:** Separate measured display extents from parent run
   state. In unfolded rows, `start`/`stop` use stream min/max, even for open
   runs; apply short-clip widening only for visibility, with overlap allocation
   based on true stream extent. Keep explicit true-stream extent helpers,
   and retain/document parent-run helpers for “Zoom to run”. If stream zoom
   is desired, add a distinctly named action. Keep parent recorded status,
   activity and source freshness as separate fields; label/tooltips should
   identify the stream and say the outcome belongs to its run. Count empty
   streams separately from non-finite/invalid data and failed reads. State
   that this view shows an event-time span, not acquisition duty cycle.

5. **Should fix — §§6.1, 6.3, 6.5, 9–10: define replacement, overlap and cap behaviour.**

   **Problem:** The design does not say whether a lane's older runs remain
   visible when only the latest 200 are unfolded, whether the parent row is
   replaced, or what happens when all its streams are empty. It also needs
   a rule for runs present in other tracks and for render counts/budgets.

   **Evidence:** Today's frame draws every input clip and pads according to
   final visible sub-lanes. The new §6.5 soft run cap is different from §6.3's
   render-budget failure rule. §6.1 defines a run independently of placements,
   and §10 defines latest runs/peers by logical lane, not by frame rows.

   **Proposed change:** Unfold only the requested placement's logical lane;
   other placements of the same run remain folded, with observations shared.
   Replace that lane's chosen run bars with stream bars. Define the remainder
   explicitly; I recommend retaining older, unattempted runs in a clearly
   labelled folded remainder row, with the limit reported in Data status.
   Otherwise explicitly count and announce their omission. Unreadable streams
   must not be invented as measured spans. Keep the selected logical lane
   and its latest-runs table/trend unchanged by unfolding, including runs with
   no drawable streams; deduplicate their reads/counts once per `(root, uid)`.
   Group stream rows by parent logical lane then stream name; allocate overlap
   sub-lanes separately per stream using `(true start, root, uid)` tie-breaks.
   Count all resulting stream/overlap rows against final-sub-lane and placement
   budgets, with `min_lanes` padding applied afterward. Keep logical placement
   counts separate from stream-bar/render counts. Test a run in two tracks,
   overlaps in one stream, an all-empty lane, and a capped unfolding.

6. **Should fix — §§5.4, 6.5, 8: closed-run caching requires a fresh final observation.**

   **Problem:** A recently cached open observation cannot become a permanent
   finished-run extent merely because the next window record has a stop.
   Nor should an error or unknown empty result be cached forever. The
   statement that all finished events “never change” needs a producer boundary.

   **Evidence:** Checked **Bluesky 1.14.6**'s
   [`_RunWriter.stop`](https://github.com/bluesky/bluesky/blob/v1.14.6/src/bluesky/callbacks/tiled_writer.py):
   it writes buffered internal data and stream datums before updating stop
   metadata. The disposable **Tiled 0.2.21** probes on both backends confirmed
   this: with a large batch the open streams had no stored rows, and after
   stop the expected two baseline and three primary rows were present.
   With incremental writes, an earlier observation had only the first baseline
   event and no primary descriptor; the final record had both baseline events
   and all three primary events. This is a cache-transition problem even when
   the writer flushes correctly. Today's `_Observed` has no closed-generation
   or stream-extents field and can retain a recent open observation for 60 s.

   **Proposed change:** On first observing closure, bypass the ordinary open
   TTL and read a fresh complete stream observation. Cache successful final
   extents under `(root, uid)` plus the relevant closure/content generation;
   compare total rows with declared counts where available, separately from
   the number of finite timestamps. Retry errors/incomplete observations under
   a bounded policy. Record last successful extent, observation time and
   error separately during outages; stale extents are labelled as such.
   For the checked serial TiledWriter, stop-before-flush is not an evidenced
   defect. For other producers, late backfills or mutable catalogues, require
   an explicit immutability assumption or a finite revalidation policy; the
   existence of stop alone is not a general Tiled immutability guarantee.
   Bound cache memory and share concurrent reads across frame/status and
   presets. Test the open-to-closed transition inside one activity interval.

7. **Should fix — §§4, 6.2, 6.4: defaults and separator cleanup need a parsed grammar.**

   **Problem:** Post-substitution text does not identify whether a ` · ` came
   from the template, a metadata value or a literal default. “Next to an empty
   part” is undefined after field text has been concatenated. Handling of `|`,
   escaped braces in defaults, whitespace-only values and all-empty results
   is also unspecified. Applying global cleanup to clip labels can alter
   meaningful metadata text.

   **Evidence:** A value `"north · south"`, a default `"north · south"`, and
   a template joining two fields can produce the same text but have different
   separator provenance. `{start.instrument} {start.station} · {role}` with
   station missing must retain the instrument/role separator; blindly deleting
   a separator adjacent to an empty placeholder would fail the draft's own
   example. A template using only missing group fields falls back to “group
   values”, although `fallback` is described as applying only without `name`.

   **Proposed change:** Share one placeholder/default parser between lane
   names and clip labels, with explicit brace/pipe escaping or clearly rejected
   forms. Preserve placeholder/default expansions as opaque text; apply layout
   cleanup only to template-literal whitespace/separators. Specify grouping
   of literal text into optional parts and examples for empty middle/end
   parts, or simplify to a deterministic literal-separator rule. State exactly
   what “empty” means and give an eventual non-empty lane-name fallback.
   Lane-name resolution must use canonical group components, including numeric
   normalisation and the missing sentinel, rather than a representative run's
   raw values. Clip labels can use the same grammar without destructive
   metadata cleanup. Test defaults containing separators/pipes/braces, literal
   separators in metadata, all-empty templates and `1` versus `1.0` in one group.

8. **Should fix — §§4.1, 6.2: the shipped default changes run inclusion too.**

   **Problem:** The newly grouped identity fields expose the generic rejection
   of list/mapping group values to the default view. Today those values are
   ignored or fall back to the unknown instrument; under draft 3 the run is
   excluded from the only default track. That is a visibility change beyond
   the approved name/order changes and the §4.1 migration table.

   **Evidence:** Real RunEngine documents written through **Bluesky 1.14.6**
   to disposable **Tiled 0.2.21** SQLite and PostgreSQL catalogues accepted
   synthetic `instrument: [synthetic]`, a mapping-valued facility, and a
   list-valued station. Current `clips.lane_key` retained every run, treating
   the malformed identity component as absent. Applying §6.2's declared
   scalar-only rule to §4.1's group fields excludes those placements; this
   latter result is a specification deduction, not an executed preset engine.

   **Proposed change:** Prefer retaining such runs in the default with an
   explicit generic grouping policy for invalid components, such as
   `invalid: missing`, selected by the shipped YAML and reported diagnostically.
   That policy belongs to grouping configuration, not instrument-name code.
   Alternatively obtain an explicit design decision to exclude them and
   document/test the additional visibility change. Missing/null/empty-string
   instruments already work with the proposed tuple/default; no legacy lane
   strategy is needed for those. Also record that adding `root` separates
   lanes and trend peers previously merged across roots. Processing adjacency
   is guaranteed only within the same root and identity tuple, not across
   separate acquisition and processing roots.

9. **Should fix — §§6.2, 6.4–6.5: final row names need one collision allocator and a length rule.**

   **Problem:** Logical names, stream suffixes and overlap suffixes can collide.
   Grafana partitions on the final display name, so a collision merges data,
   even if hidden lane IDs differ. The 200-character rule does not specify
   truncation/failure, suffix space, prefix inclusion or whether it applies
   before stream names are appended.

   **Evidence:** Unfolding logical lane `A` into stream `x` produces
   `A · x`, which can already be another logical lane's name. Stream
   `x /2` also collides with a second overlap row of stream `x`. Current
   `clips.py::frame` reserves logical names before allocating overlap names,
   and `timeline()`'s sole transformation partitions only by `lane`.
   Numeric disambiguation is stable for a fixed set but can change when a
   colliding root/facility leaves the window; final names therefore do not
   depend on their own key alone.

   **Proposed change:** Allocate names globally and deterministically across
   logical, stream, overlap and padding rows, reserving literal names before
   adding suffixes. Use identity tuples internally, never concatenated display
   strings, and preserve the parent logical `lane_id` on every unfolded row.
   Clarify that base names are key-derived while collision suffixes depend on
   the rendered set; URLs always use IDs. Define the 200-character policy for
   the final composed name, reserve room for unique suffixes, and specify
   truncation versus rejection plus a guaranteed non-empty fallback. Include
   root/facility in tooltips so `(2)` alone is not the only source cue.

10. **Should fix — §§2, 10, 12: removing the fixed key affects summaries and default trends.**

    **Problem:** The approved `/clips.lane_key` replacement is reasonable as a
    convenience projection of the first placement, but it does not finish the
    migration. Run summaries and trends still depend on the functions being
    removed. The trend promise “Without `preset`, as today” contradicts the
    new default's root-separated identities and the single-mechanism decision.

    **Evidence:** `adapter/runs.py:43` and `:51` compute the run-summary
    `lane` using `lane_key`/`lane_names`; it appears in `/runs/{uid}`,
    `/selection/run` and summary views. `adapter/trends.py` compares fixed
    `lane_key(run)` values, and its routes currently have no `lane` argument.
    `/health` itself uses source observations rather than lane keys.

    **Proposed change:** Define “first” as the first matching placement in
    `lanes.order`, not frame order or the clicked placement. Specify whether
    `/clips.label` remains the view-table run label or projects that same
    placement; keep placement-specific labels in `placements`. Resolve the
    preset-independent run-summary lane through the ordinary default preset,
    clearly describing it as a default/base name rather than the current
    window's disambiguated selected lane, or remove/replace it in the approved
    endpoint migration. Explicitly give both trend routes the selected lane
    and the direct-link resolution rule. Omitted preset now means the new
    default consistently; document any peer changes and update the stale
    “as today” phrase and disposition 07's preserved-fields statement.
    Record all changed endpoint meanings in the pre-v1 minor migration;
    do not keep a second lane implementation to satisfy stale wording.

11. **Should fix — §§4.1, 5.1, 6.1, 10–11: root-specific lanes require root-specific selection.**

    **Problem:** A visible placement identifies `(root, uid)`, but the proposed
    links still send only UID to detail endpoints, which deliberately do not
    take preset/lane. Copies with the same run key under two configured roots
    can therefore select the wrong source. Adding root to the default key
    makes those separate selectable lanes without resolving the ambiguity.

    **Evidence:** `Adapter.find` calls `Layout.find_run` over configured roots
    and takes the first hit. On both **Tiled 0.2.21** backends I replayed the
    same captured RunEngine run through TiledWriter into two disposable roots,
    with a different synthetic source-label field in each copy. UID-only
    lookup returned `source-a` even for a placement in `source-b`; explicit
    lookup under `source-b` returned the clicked record.

    **Proposed change:** Carry a root-qualified run selection in frame/table
    helpers and links, and let run/stream/data/result lookups consume that
    source identity independently of preset filtering. Keep bare-UID bookmarks
    under a documented compatibility rule. If root-qualified selection is
    deliberately deferred, define and enforce uniqueness/ambiguity handling
    across configured roots; do not silently choose another displayed source.
    Test Select, Zoom and stream navigation from the second root. `lane_id`
    alone cannot fix details that never receive it.

12. **Should fix — §11: define link updates explicitly, and provide a Fold action without a clip.**

    **Problem:** Unfolding is independent of selected run/lane, and stream
    clicks add another state variable. Reusing the current link templates
    would discard that context; appending `${__all_variables}` and explicit
    replacements would duplicate parameters. If every stream is empty or
    unreadable, there is no unfolded bar from which to invoke Fold.

    **Evidence:** Checked **Grafana 13.3.1**'s
    [data-link interface](https://github.com/grafana/grafana/blob/v13.3.1/packages/grafana-data/src/types/dataLink.ts),
    [link supplier](https://github.com/grafana/grafana/blob/v13.3.1/packages/grafana-data/src/field/fieldOverrides.ts)
    and
    [data macros](https://github.com/grafana/grafana/blob/v13.3.1/public/app/features/templating/dataMacros.ts).
    Row fields support formatting, so encoded lane/stream values are possible.
    The static serialisable link interface has no visibility predicate;
    the supplier creates a link model for every configured ordinary link,
    including one whose interpolated URL/title is empty. Empty values are
    not a supported conditional-hide mechanism: the
    [menu conversion](https://github.com/grafana/grafana/blob/v13.3.1/packages/grafana-ui/src/utils/dataLinks.ts)
    maps every supplied link without a visibility filter. The State timeline
    uses that field link supplier. Today's generator reconstructs Select/Zoom/stream
    URLs using only the original variables.

    **Proposed change:** Use an explicit transition table in the generator:

    | Action | Selection change | Unfold/time change |
    |---|---|---|
    | Unfold clicked lane | preserve run, selected lane, preset and stream | replace `unfold` with clicked logical lane ID; preserve range |
    | Fold | preserve selection and stream | set `var-unfold=` explicitly; preserve range |
    | Select run/stream bar | set clicked run identity and parent logical lane; set clicked stream, or clear an unrelated old stream | preserve current unfolding and range |
    | Zoom to run | set clicked run identity/lane and a defined stream value | preserve unfolding; replace range with parent-run zoom bounds |
    | Stream-table navigation | preserve run identity/lane/preset; set stream | preserve unfolding and range |

    Supply each variable exactly once using `${preset:queryparam}` and the
    corresponding current-variable form when preserving it, or an explicit
    encoded row-field value when replacing it. For example use
    `${__data.fields["lane_id"]:percentencode}` for `var-unfold`; never use the
    displayed name. Zoom uses explicit `from`/`to` instead of additionally
    appending `${__url_time_range}`. Preserve `unfold` in all selection links,
    and validate/report stale lane IDs on preset/range changes. Add explicit
    column definitions and hide overrides for lane ID, track, root identity
    and stream so they do not become extra State timeline value rows.

    Within the chosen static panel with one visible `state` field, keep both
    Unfold and Fold as idempotent data links; no custom JavaScript is needed.
    Add a declarative dashboard/header Fold link that remains available with
    no drawable clips. A separate conditionally visible control would require
    another layout/control arrangement and its own verification; it is not
    an individual data-link visibility option. Test URL round-trips and empty
    clearing on the pinned stack, including special characters and the new
    dashboard variant UID.

13. **Suggestion — §§2, 4.1, 12–13: reserve the shipped ID, but make deployment defaults convenient.**

    **Problem and evidence:** Nothing in the new ordinary-file mechanism
    requires a deployment to replace `instruments`. Cloning it under a new ID
    already allows different naming/grouping. However, the picker still always
    starts at `instruments`, and shipping YAML needs package-resource support:
    current `pyproject.toml` package data lists only `py.typed`.

    **Proposed change:** Keep `instruments` reserved and use the same parser,
    validator and evaluator for its file as for deployment files. If operators
    need their custom view on first load, allow deployment configuration to
    select the default preset and initialise the dashboard picker consistently,
    rather than shadowing a stable package ID. Include `presets/*.yaml` as
    package resources, load them independently of checkout location, and smoke
    test an installed wheel. Make typed tuple sorting lexicographic by component,
    with a documented type-category order and missing last. For normal scalar
    identity metadata, the proposed default otherwise expresses the requested
    roots, facilities, stations, unknown instruments and role ordering.

## What I ran and what remains unverified

- Read the repository instructions, draft 3, disposition 07, the relevant
  response 07 findings, endpoint contracts and affected code/generator. The
  design diff against `c5e4f85` changes only `presets.md` and disposition 07.
  Existing working-tree edits were left alone.
- Checked installed versions: Tiled **0.2.21**, Bluesky **1.14.6**,
  event-model **1.23.1**, bluesky-tiled-plugins **2.0.2**. Inspected the
  installed table-read route and TiledWriter flush order, and exact
  Grafana **13.3.1** source for State timeline, field links and URL macros.
- Ran `/private/tmp/ett-review08-probe.py` via
  `UV_CACHE_DIR=/private/tmp/ett-review-uv-cache PYTHONDONTWRITEBYTECODE=1
  uv run --locked --no-sync python ...`. It reused `tests/conftest.py`'s
  disposable server construction on SQLite and PostgreSQL **16.16**.
  Acquisition documents came from a real RunEngine with `ophyd.sim`, retimed
  with synthetic metadata and written through TiledWriter; processing runs
  came from the existing valid event-model/TiledWriter fixture. No imitation
  Tiled run trees, production data or hardware were used. The roots in the
  duplicate-key probe were disposable catalogue containers populated through
  TiledWriter. PostgreSQL containers were removed in `finally` blocks.
- The probes checked descriptor-only streams, buffering before stop, final
  flush, open-to-closed observation changes, unsorted event-time extrema,
  external-only processing timestamps, non-scalar identity fields and
  duplicate UID selection across roots. Assertions passed on both backends;
  all 13 measured extent-table requests per backend selected only `time`.
  Results: `/private/tmp/ett-review08-results.json`. Applying draft 3's
  grouping exclusion rule is explicitly a design deduction; no preset engine
  exists to execute it yet.
- Ran `uv run --locked --no-sync pytest -q tests/unit/test_clips.py
  tests/unit/test_grafana_provisioning.py tests/contract/test_layout.py
  tests/contract/test_endpoints.py -p no:cacheprovider
  --basetemp=/private/tmp/ett-review08-pytest`, with the temporary cache and
  bytecode environment above: **95 passed**, including PostgreSQL, with three
  dependency deprecation warnings. These test today's code, not an unfolding
  implementation.
- Ran `uv run --locked --no-sync mkdocs build --strict --site-dir
  /private/tmp/ett-review08-docs` with the same temporary uv cache: passed.
  The new response being outside navigation was an INFO message; navigation
  was left unchanged under the response-only instruction.
- No Grafana browser session, dynamic-control experiment, installed-wheel
  test or 14-day performance benchmark was run. Link support/visibility
  conclusions are source-based; actual interpolation, empty-variable clearing
  and navigation still need the proposed pinned-stack browser tests. Other
  writers' late ingestion and mutable-finished-run policies were not tested.
  No full lint/type suite or implementation changes were needed for this
  response-only review.
