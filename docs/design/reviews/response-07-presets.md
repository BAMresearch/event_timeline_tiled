# Review 07: presets and layout

Reviewed 2026-11-18 against the draft in `presets.md` and the current working
tree. This is a design review, not an implementation. Only this response was
added; no commit was made.

## Verdict

The architecture and the four maintainer decisions are sound: deployment-owned
YAML, a dashboard preset picker, membership in every matching track, and a
generated layout progressing from overview to selected-run detail. Keep them.
Do not freeze preset format v1 yet. The draft overstates Tiled predicate
equivalence, cannot express its own built-in compatibility promise, and leaves
multi-track selection and endpoint contracts unresolved. YAML scalar resolution,
group identity, limits, stream knowledge and cache isolation also need normative
rules before deployments depend on them. Most changes are specification work;
they do not require a new UI architecture. The fixed first layout is feasible,
but its claim that Grafana 13.3.1's new layouts/file provisioning remain
experimental needs correction.

## Findings

1. **Blocker — §6: `Eq`/`In` are not the adapter's equality.**

   **Problem and evidence:** Real RunEngine/ophyd runs written through
   Bluesky 1.14.6's `TiledWriter` into disposable Tiled 0.2.21 catalogues
   produced the following results. PostgreSQL was 16.16; SQLAlchemy was 2.1.8.
   Each named value below was a separate run's `start.probe`; missing keys
   were tested too.

   | Query | SQLite matches | PostgreSQL matches |
   |---|---|---|
   | `Eq(..., None)` / `In(..., [None])` | explicit null | explicit null |
   | `Eq(..., 1)` / `In(..., [1])` | `1`, `1.0`, `true` | `1`, `1.0` |
   | `Eq(..., True)` / `In(..., [True])` | `true`, `1`, `1.0` | `true` |
   | `Eq(..., False)` / `In(..., [False])` | `false`, `0` | `false` |
   | `Eq(..., "1")` / `In(..., ["1"])` | string `"1"` | string `"1"` |
   | `Eq(..., "01")` / `In(..., ["01"])` | string `"01"` | string `"01"` |
   | `Eq(..., [1])` | HTTP error | `[1]`, `[1, 2]` |
   | `Eq(..., {"a": 1})` | not executed; unsupported binding in source | `{"a": 1}`, `{"a": 1, "b": 2}` |

   None of these queries matched a missing key. Importantly, PostgreSQL's
   scalar `Eq(..., 1)` did **not** match the property value `[1]`, `[1, 2]`
   or `[[1]]`; `Eq(..., "1")` did not match `["1"]`. Do not generalise
   PostgreSQL's top-level array/scalar containment exception to an object
   property's nested array. Collection-valued query operands do use subset
   containment, rather than whole-value equality. An additional wire-level
   probe, `In(..., [[1]])`, matched numeric/boolean scalars on SQLite and
   arrays containing `1` on PostgreSQL: nested membership operands must not
   be accepted for pushdown.

   These results agree with Tiled's
   [0.2.21 query translators](https://github.com/bluesky/tiled/blob/v0.2.21/tiled/catalog/adapter.py):
   PostgreSQL uses JSONB containment; SQLite extracts native SQL values,
   losing the JSON boolean/number distinction. The
   [0.2.7 source](https://github.com/bluesky/tiled/blob/v0.2.7/tiled/catalog/adapter.py)
   has the same PostgreSQL translation but a different SQLite implementation:
   `Eq` selects a typed accessor by the operand's Python type, and `In`
   selects it using its first operand. Null/list/dict types are absent from
   that accessor map. It therefore cannot support the same null/collection
   cases; typed-accessor coercion also depends on SQLAlchemy. I did not run
   an actual 0.2.7 server or the lab's SQLAlchemy version.

   **Proposed change:** Keep adapter evaluation authoritative, including
   rechecking `scope`. Define pushdown as a proven **candidate superset**,
   not exact equality. For v1, restrict scope operands to non-null JSON
   scalars, with homogeneous `in` lists (integers/floats may share the numeric
   category); reject objects, nested arrays and relative operands there.
   Prove losslessness for supported server/compiler versions and fall back
   to a paged window scan where it is unproved. Retain incomplete/truncated
   reporting when extra candidates consume a cap. Keep all catalogue-specific
   translation and fallback choices in `layout.py`. Adapter evaluation of
   general `where` predicates is the right default; do not push `neq`, `nin`
   or regex opportunistically. Replace the unsupported “tested on both”
   equivalence assertion with the actual tested operand matrix.

2. **Blocker — §§2–3, 8: the generic format cannot reproduce the built-in.**

   **Problem and evidence:** `clips.py::lane_key` groups by
   `(facility, instrument, station, role)`. Missing/non-string/empty instrument
   metadata falls back to `(<catalogue root>)`, or `(unknown)` at the server
   root. `_component` distinguishes absent components from a literal `-`;
   `lane_names` adds facility only to resolve collisions and then uses numeric
   suffixes. `_lane_order` puts acquisition and processing next to one another
   for each identity tuple. A single `group_by.field` cannot express that
   tuple or root fallback. Two globally ordered tracks put all acquisition
   groups before all processing groups, changing today's order. The proposed
   `<track> · <group>` naming changes today's names too. These are observable
   endpoint contracts, not just a YAML presentation detail.

   **Proposed change:** Reserve `instruments` as a built-in strategy implemented
   by the existing functions; do not allow files to shadow it. Omitted
   `preset` and explicit `preset=instruments` should reproduce today's clips,
   labels, frame, ordering, fallback and name disambiguation. Do not pretend
   it is two ordinary YAML tracks. A serialisable equivalent can come later
   if the general format gains the necessary expressive power. Preserve
   regression cases for missing instruments, roots, `-`, colliding facilities,
   processing adjacency, overlap sub-lanes and padding.

3. **Blocker — §§2–3, 7–8: membership needs a selection and response contract.**

   **Problem and evidence:** Today `/clips` has one clip with a scalar
   `lane_key`/`lane`; `/clips/status.runs` counts clips; the frame exposes UID
   but no machine-readable placement identity. `Trends.trend` filters by
   `lane_key(run)` plus role and `plan_name`. A UID matching Measurements and
   Failures does not identify which clicked lane should supply the latest-runs
   table or trend. Duplicating `Clip` objects alone would double counts and
   potentially duplicate trend reads. The draft only resolves table deduplication.

   **Proposed change:** Represent unique runs separately from placements.
   Deduplicate internally by `(root, uid)`, then create one placement per
   matching track/group; allocate overlaps separately within each placement's
   logical lane. Keep the existing `/clips` fields' documented meanings and
   add `placements` with preset/track/group identity, display name and label.
   Add hidden placement helpers to the frame, while preserving its final
   `lane` column and existing fields. Do not repurpose the documented
   `facility/instrument/station/role` text in `/clips.lane_key` into a new key
   format. Document changes in `endpoints.md` and `migrations.md`.

   Carry the clicked logical lane in a hidden URL-synchronised variable,
   alongside preset and UID. Both Select and Zoom must set it. The latest-runs
   table should deduplicate runs in that lane, newest first; without a selection
   it should show the preset's window runs once each. For a direct UID link
   without a lane, choose the first matching lane in specified preset order
   and identify that choice. On preset changes, validate the old lane and
   recompute or report no matching lane; never silently use an unrelated lane.
   Keep selected-run details readable even when the run no longer matches.
   No matching lane means no peer trend, with a notice. For trends, retain
   role, plan, view/stream and unit compatibility checks **in addition to**
   the selected preset lane, and read each peer run once. Sub-lanes are a
   rendering detail and must not define “runs like it”.

4. **Should fix — §§2, 8: strict validation needs a complete registry contract.**

   **Problem and evidence:** The draft promises a JSON Schema and unknown-key
   rejection without specifying required keys/defaults, duplicate preset IDs,
   filename versus ID identity, or collisions with `instruments`. It also
   promises root narrowing in §1 without defining a field for it. CONFIG_SPEC
   §15 ignored unknown top-level keys; this draft deliberately changes that.
   `version: true` must not pass a Python `version == 1` check.

   **Proposed change:** Require an actual integer `version: 1`, slug IDs and
   non-empty titles; require a non-empty track list with unique slug IDs and
   non-empty names. Reject unknown keys at every level and mixed predicate
   forms. Define missing defaults, empty-list semantics and permitted scalar
   types. Reserve built-in IDs, reject duplicate IDs across files, use ID rather
   than filename as identity, and load the registry atomically at startup.
   Add `roots` as an optional subset of configured roots, or explicitly defer
   the §1 promise. A preset cannot expand the adapter's configured access.
   Reject empty/unknown `preset` arguments with documented client errors;
   do not silently fall back to `instruments`. Specify `/presets` ordering
   and its `id`/`title` response, including the built-in. Expose a registry
   revision/digest for diagnostics and cache identity; format version is not
   content revision. Keep old format semantics in the loader and record later
   contract changes through the repository's migration policy.

5. **Should fix — §§2, 5: safe YAML loading is insufficient for typed predicates.**

   **Problem and evidence:** With installed PyYAML 6.0.3, `safe_load` resolves
   `no`/`off` to false, `on`/`yes` to true, `2026-11-18` to a date, `012` to
   integer ten, `null`/`~` to `None`, and `.nan` to a non-finite float.
   Duplicate mapping keys silently keep the last value. For a predicate's
   unconstrained `value`, ordinary post-load schema validation cannot recover
   what the author intended. Safe construction prevents arbitrary object
   construction; it does not solve these ambiguities or resource limits.

   **Proposed change:** Specify a YAML subset with JSON scalar resolution:
   only lowercase `true`, `false`, `null` and JSON number syntax get implicit
   non-string types; dates and `no/on/off/yes` remain strings. For clarity,
   reject bare `~`, leading-zero numeric-looking values and non-finite numeric
   spellings and require quoting when they are strings. Document this as an
   intentional subset, not unrestricted YAML 1.2. Use a dedicated safe loader
   without modifying PyYAML's global resolvers. Reject duplicate/non-string
   mapping keys before constructing dictionaries, multiple documents,
   explicit tags, aliases and merge keys. Report filename and line/column plus
   the schema path. Set concrete byte, depth, node, track and predicate-count
   limits before recursive validation; aliases/merges need not be a v1 feature.
   A file-size cap alone does not bound nesting depth. Add literal-type tests
   for every example above, including nested `value` mappings and sequences.

6. **Should fix — §§4–5: define a truth table and field-path grammar.**

   **Problem and evidence:** “No coercion” does not decide whether `1` equals
   `1.0`, whether equality of lists is deep/type-aware, or whether
   `streams in [primary]` means array-element membership. Ordinary Python
   equality makes `True == 1`, including inside lists. Traversal through
   lists, literal dots in metadata keys, malformed paths and extra predicate
   keys are unspecified. CONFIG_SPEC §11 describes numeric/time ordering,
   `not_empty` for strings/lists only, and empty boolean compositions, but its
   examples also use bare predicate arrays despite the formal grammar not
   defining them. Calling it “unchanged” inherits that inconsistency.

   **Proposed change:** Make v1's grammar and operator operand schemas
   normative. Recommend one numeric category (`1 == 1.0`), a separate boolean
   category, recursive typed JSON equality, and ordering only of finite numbers
   excluding booleans. `in` compares the **whole resolved value** against
   each list member; no implicit traversal, substring or array overlap.
   `require_streams` supplies all-of stream membership. Future any-of/element
   operators can be added under new names. Define `exists` as non-null presence,
   `missing` as absent/null, and `not_empty` as a non-empty string/list; `0`,
   `false` and `{}` are not non-empty lists/strings. Preserve the draft's
   missing/null rules, including under `not`, and define empty `all=true`,
   `any=false`, `in=false`, `nin=true`. Reject null equality operands at the
   top level with advice to use `missing`, rather than accepting a predicate
   that cannot match.

   Permit exactly a leaf, `all`, `any`, or `not`; reject bare predicate arrays
   and document the CONFIG_SPEC adaptation. Use dictionary-only dotted traversal;
   do not interpret `streams.0` as an index or vectorise through lists. Document
   the limitation for literal-dot keys now; a later segment-array path form
   can be additive. Define `uid` as the Tiled run key, not `start.uid`, and
   `role`/`status` using `run_state.py`'s exact enums. In particular predicates
   use `status: failed/aborted`, while `stop.exit_status` uses `fail/abort`.

7. **Should fix — §§5–6: relative times, cached records and regex need an epoch.**

   **Problem and evidence:** CONFIG_SPEC evaluates relative values at “now”,
   while the window's open-run horizon is intentionally anchored to `to`.
   Reusing a cached filtered result can freeze a relative predicate; evaluating
   each leaf with a fresh clock can disagree even within a run. The draft does
   not distinguish these clocks. It also leaves regex length and invalid
   patterns undefined. Python `re.search` and a pattern-length cap do not
   guarantee bounded matching time.

   **Proposed change:** Capture one UTC epoch-second `evaluated_at` per request,
   resolve every relative operand against it, and re-evaluate predicates on
   cached raw records. Explicitly retain the window-end clock for the open-run
   horizon. Explain that a relative predicate can change the result when a
   historical range is refreshed; use absolute operands for reproducibility.
   Define the relative grammar, units and allowed operators; do not permit it
   in scope. Return the evaluation time and preset revision for diagnostics.
   Compile regex at startup, specify the actual length limit, no implicit
   flags, and fail invalid patterns there. Preserve trusted deployment files
   as the boundary; do not offer arbitrary regex in browser query parameters.
   If untrusted authoring is introduced later, use a bounded matcher or an
   isolated execution budget, rather than claiming a length cap suffices.

8. **Should fix — §§3, 6, 8: scoping must participate in stale-cache coverage.**

   **Problem and evidence:** `Timeline` currently has one `SourceState` per root;
   its `runs` dictionary accumulates records across windows, and failure uses
   `_in_window` over that dictionary. Its `last_success` is also per root.
   Adding scoped requests without changing coverage tracking could reuse
   records from a different preset or advertise another scope's success time
   as this scope's freshness. Open-run `_Observed` caches only activity, not
   stream names. There is no cached complete window snapshot today.

   **Proposed change:** Track query coverage and last successful fetch by root,
   window and normalised scope, with preset revision where results depend on
   it; alternatively cache neutral records with explicit coverage descriptors.
   Reapply scope/where to every stale fallback and preserve each run's
   observation time. Share per-run activity/stream observations separately
   across placements. Keep source health distinct from membership failures
   and include preset context in the data-status request. Add a regression:
   fetch preset A, fetch B, fail A, then verify A's membership, timestamps and
   incomplete/stale reporting. Do not make a successful scope B query certify
   scope A's coverage.

9. **Should fix — §3: lane identity cannot be a concatenated display name.**

   **Problem and evidence:** Metadata values `1`, `"1"`, missing data and a
   literal fallback string can render identically. Track names need not be
   unique, and metadata can contain separators or sub-lane suffixes. Current
   `clips.py` deliberately separates machine keys from disambiguated names;
   the new design needs the same distinction. CONFIG_SPEC's unconditional
   stringification is insufficient as a key contract.

   **Proposed change:** Define a stable logical key from preset ID, track ID
   and a canonical typed group value (or tuple), with a distinct missing
   sentinel. The fallback changes display only; it must not merge a missing
   value with the literal text `(unknown)`. Specify missing/null/empty-string
   fallback, numeric normalisation consistent with predicate equality,
   boolean display, ordering and collision disambiguation. Do not call
   `str(dict)` a portable group encoding. Reject list/object group values in
   v1 with an explicit evaluation issue, or deliberately specify their whole
   value encoding; never silently fan out. Add optional multi-field grouping
   for facilities needing, for example, beamline and station without a
   precomputed upstream key. Keep group identity independent of labels,
   sample ordering and changes to other lanes in the window.

10. **Should fix — §§3, 8: ordering, padding and lane expansion need bounds.**

    **Problem and evidence:** `lanes.order` contains track IDs, but its name
    suggests lane ordering. Unlisted/duplicate/unknown IDs and within-track
    ordering are not defined. Current frame padding counts final sub-lanes
    and the endpoint accepts `min_lanes` from 0 through 20. A group per sample
    and a run in several tracks can multiply rows even when unique runs stay
    below `ETT_MAX_RUNS`. Track count, group count and overlap count are
    independent limits.

    **Proposed change:** Define `order` as a partial track order: reject
    duplicate/unknown IDs and append omitted tracks in file order. Specify a
    stable typed group sort and preserve existing true-extent overlap
    allocation and short-clip widening. Count sub-lanes for `min_lanes`; use
    the preset default only when the query argument is absent, with explicit
    query precedence and the existing 0–20 range. Give deployment settings
    limits for tracks, logical lanes, final sub-lanes and placements, plus a
    lower optional preset lane budget. Prefer a clear render-limit error with
    a status issue and advice to narrow the grouping. Do not silently fold
    arbitrary samples into “other” or label an empty failed frame complete.
    An overflow grouping policy could be a separately specified feature.

11. **Should fix — §§3, 6: counts and truncation must describe unique runs and work.**

    **Problem and evidence:** `Layout.window_runs` caps raw candidates per root,
    prioritising open runs then closed runs in insertion order. Consequently
    adapter filtering can return no matches while newer matching runs were
    never read. The draft's “every run in the window” is already conditional
    on that cap and on the open horizon. `SourceFetch.runs`, activity errors,
    trend counts and `/clips/status.runs` currently refer to runs, not track
    memberships. A post-filter limit cannot certify completeness.

    **Proposed change:** Apply safe scope candidates to both searches, repeat
    their filters on every page, then evaluate authoritative scope and tracks
    in the adapter. Retain the raw-candidate truncation flag even when the
    filtered list is short or empty. Preserve run-count fields as unique-run
    counts and add clearly named candidate/matched/placement/lane counts where
    needed. Count an activity failure once per run, not per placement. Distinguish
    source truncation, trend read caps and layout limits in notices. “Latest
    runs” should mean latest among the covered candidate set, with incomplete
    data visible when the search cap prevents a true latest result. Test a
    selective track whose matches lie beyond the candidate cap.

12. **Should fix — §4: stream coverage is valid for the checked producer, not free.**

    **Problem and evidence:** On **both** Tiled 0.2.21 backends, a real
    Bluesky 1.14.6 RunEngine plan declared `empty` with no events and emitted
    one `primary` event. `TiledWriter` persisted both stream containers and
    `stop.num_events == {"empty": 0, "primary": 1}`. Observations after start,
    descriptors and event confirmed that names appear at descriptor time,
    before a batch is flushed. Bluesky 1.14.6's
    [bundler](https://github.com/bluesky/bluesky/blob/v1.14.6/src/bluesky/bundlers.py)
    initialises stream counters at descriptor creation; installed event-model
    1.23.1's `ComposeStop` subtracts one from each counter.
    [TiledWriter](https://github.com/bluesky/bluesky/blob/v1.14.6/src/bluesky/callbacks/tiled_writer.py)
    stores the supplied stop document rather than reconstructing its counts.
    Thus a different writer's absent/incomplete `num_events` is not repaired
    by TiledWriter.

    `Layout.last_event_time` does list open-run streams, but returns only a
    timestamp. `Timeline._Observed` caches that timestamp/error for up to the
    activity refresh interval; the stream list is discarded. Preset evaluation
    cannot currently reuse a known list “without extra reads”.

    **Proposed change:** Define `streams` as known descriptor-created stream
    names, independent of event count, scalar table presence or plot availability;
    sort/deduplicate them. State that `require_streams` is all-of, with an empty
    list passing, and descriptor-only streams qualifying. Retain strict exclusion
    before confirmation. Reuse one per-run cached observation containing stream
    names, activity and observation/error information; do not reread for each
    matching track or every dashboard panel. For closed runs lacking a usable
    count mapping, either obtain names through `Layout` under an explicit read
    budget or report stream knowledge as unknown and exclude strictly. Never
    present a failed stream read as confirmed absence or healthy empty data.
    Document descriptor/read-cache lag and test missing counts, zero-event
    streams, processing external-only streams and stream-list errors.

13. **Should fix — §§2, 7–8: the Infinity picker is supported, but links drop it.**

    **Problem and evidence:** Infinity **4.1.6**
    [variable support](https://github.com/grafana/grafana-infinity-datasource/blob/v4.1.6/src/app/variablesQuery/index.ts)
    sends an `infinityQuery` through the normal data-source query path, defaults
    to the backend parser, and supports explicit text/value mapping. Grafana
    **13.3.1**'s
    [classic-model variable conversion](https://github.com/grafana/grafana/blob/v13.3.1/public/app/features/dashboard-scene/utils/variables.ts)
    preserves `skipUrlSync` and query settings. The picker therefore needs no
    browser-to-adapter query, frontend parser or transformation. However,
    `timeline_dashboard.py`'s Select/Zoom links reconstruct the URL using run
    and time only; stream links preserve run and time only. Adding a variable
    alone will not preserve it through those navigations. A new dashboard
    variant also cannot reuse links hard-coded to `ett-timeline`.

    **Proposed change:** Return sorted `id`/`title` rows from `/presets`. Use an
    Infinity query variable with `queryType: infinity`, `infinityQuery.parser:
    backend`, table format, and `meta: {textField: title, valueField: id}`.
    Disable multi-value/All/custom values, enable URL sync, choose `instruments`
    as the initial value, and refresh the registry on dashboard load. Use
    `${preset:percentencode}` in adapter query URLs and `${preset:queryparam}`
    in every navigation, plus encoded placement context where appropriate.
    Parameterise the destination dashboard UID in shared row builders. Preserve
    preset through run/stream/related-run links and zooms, setting or clearing
    placement context deliberately. Avoid appending all old variables and a
    second `var-run`, which creates duplicate URL parameters.

    Apply preset semantics to clips, frame, window data status, the peer trend
    **including `/selection/trend`**, and the new latest-runs query. Keep
    `/health`, `/queues`, `/presets`, individual-run metadata/streams/fields/data,
    and related/curve/image result lookup independent of preset filtering.
    A failure-only acquisition preset must not hide a successful processing
    result needed for the selected measurement. Such detail links can preserve
    the dashboard picker without filtering their data. Verify generated JSON
    and a real pinned-stack navigation cycle before implementing the contract;
    source support is not an end-to-end browser test.

14. **Should fix — §7: the experimental-file-provisioning premise is outdated.**

    **Problem and evidence:** In the checked Grafana **13.3.1** tag,
    [`dashboardNewLayouts`](https://github.com/grafana/grafana/blob/v13.3.1/pkg/services/featuremgmt/registry.go)
    is marked generally available and enabled by default. Its
    [file-provisioning documentation](https://github.com/grafana/grafana/blob/v13.3.1/docs/sources/administration/provisioning/index.md#dashboards)
    documents provisioning v2/dynamic dashboards using Kubernetes resource
    format. Those sources do not support the draft's blanket statement that
    file provisioning is still experimental. This is separate from the
    stability of Git Sync or other provisioning systems.

    **Proposed change:** Keep the maintainer's chosen first static layout and
    current dashboard, but describe that as a scope/compatibility choice.
    Correct §7 and record the revised evidence alongside D-009 when the design
    is updated; do not silently rewrite the historical decision. A future
    dynamic variant needs the resource envelope and pinned-version smoke
    tests. I did not provision such a variant, so I am not claiming all
    conditional-layout interactions work with the existing Infinity panels.

15. **Should fix — §7: static positioning works; the missing parts are adapter data.**

    **Problem and evidence:** `dash_parts.stack` and the existing generator
    already build fixed rows from `gridPos`, so the chosen order, three
    columns, a summary over metadata, and two adjacent curve panels can be
    expressed declaratively. Existing endpoint data does not provide “latest
    runs on the clicked preset lane, else window runs”, and the draft omits
    the maintainer's explicit summary above metadata. Stream links currently
    select fields, not a different arbitrary timeseries view.

    **Proposed change:** Add a bounded adapter endpoint, for example
    `/selection/runs?uid&lane&preset&from&to&limit`, returning final deduplicated,
    newest-first rows and notices. Resolve its unselected/out-of-preset cases
    as in finding 3; do not add Grafana joins/filter/sort transformations.
    Put `/selection/run.summary` above metadata in the third column. Place
    the selected view's event timeseries beside its corrected curve; keep
    explicit no-selection/no-timeseries/no-correction notices and access to
    images, failed/aborted reasons, full time extent and activity/source health.
    Title the trend/table with their current lane context. A compact queue
    row must still handle multiple configured managers and unreachable states.
    If “event stream” means an operator-selected arbitrary stream, specify
    that new data-selection contract through views; the existing `stream`
    variable does not implement it. Fixed panels will leave empty space;
    automatic conditional reflow would require choosing a new layout strategy.
    No custom panel is needed for the stated static arrangement.

16. **Should fix — §§3, 8: template evaluation and escaping need precise ownership.**

    **Problem and evidence:** `label` is now a string, whereas CONFIG_SPEC §9
    used title/subtitle template objects. Missing-field substitution alone does
    not define braces, nulls, list/dict stringification, maximum output length,
    or precedence over the view-table label. Python attribute formatting would
    not implement the documented dictionary field paths safely or consistently.
    The existing view-table labels are plain text; Grafana tables handle their
    display, while URLs and HTML require different escaping.

    **Proposed change:** Specify a restricted field-placeholder parser with
    escaped literal braces, no expressions/conversions/attribute calls, and
    validation of every field path at startup. Use deterministic JSON text for
    non-string values and empty text for missing/null; bound output length and
    define empty-result fallback. Omitted track `label` delegates to
    `views.label`; an override is placement-specific and does not change
    run-detail labels or instrument rules. Treat returned label/name fields as
    plain text; escape at an actual HTML sink and percent-encode at URL sinks,
    avoiding global HTML escaping/double escaping. Never recursively interpret
    metadata as template source. Test markup, ampersands, braces, newlines,
    separators and URL metacharacters. Document the label format adaptation
    rather than implying existing CONFIG_SPEC label objects still load.

17. **Suggestion — §§4, 8 and plan §13: keep the format general and name its limits.**

    **Problem and evidence:** The proposed preset fields are generally useful
    outside this lab; neither YAML nor the picker requires a Dash callback or
    MoDaCor-specific UI. Current limitations for other facilities are composite
    identities, list-valued sample/proposal metadata, dotted keys, stream-read
    cost, high lane cardinality, and the existing role classifier's knowledge
    of `modacor_correction`. A preset cannot make another processing pipeline
    acquire that role. Plan §9 step 8 also mentions lanes per event stream;
    the draft's single whole-value grouping does not implement that fan-out.

    **Proposed change:** Include generic beamline/station and campaign/sample
    examples with synthetic metadata; keep MOUSE as an example, never a code
    branch. Add tuple grouping now or clearly defer it; explicitly defer
    per-stream lane fan-out rather than stringifying the whole stream list.
    Treat the existing processing-role classifier and view table as separately
    versioned contracts to generalise later; presets can still select arbitrary
    `start` facts without redefining role. In implementation, `layout.py` owns
    physical Tiled layout, stream extraction and query compilation;
    `run_state.py` owns role/status/activity definitions; `presets.py` owns
    validated configuration, predicates and logical placements; `timeline.py`
    owns observations and coverage; `clips.py` owns display extents/sub-lanes;
    adapters assemble responses and the generator lays them out. Use `attrs`
    containers and no instrument-name tests outside the view table. Neither
    preset loading nor dashboard selection warrants a Tiled write or a queue
    control call. Instrument-specific view/stream preferences and thresholds
    remain in the view table, with deployment presets allowed to refer to
    metadata and stream names as data.

## Proposed preset format v1

The following is a proposed contract, not a request to implement it in this
review. It preserves the draft's ordinary single-field example and the four
maintainer decisions. The material additions are explicit root narrowing,
optional composite grouping and a lane budget; the built-in is deliberately
outside the deployment-file schema.

```yaml
version: 1
id: beamline-by-sample
title: Beamline by sample
# Optional: a subset of configured roots; omit to use all configured roots.
roots: [acquisition]
scope:
  - {field: start.beamline, op: in, value: [beamline-a]}
tracks:
  - id: measurements
    name: Measurements
    where:
      all:
        - {field: role, op: eq, value: acquisition}
        - {field: start.plan_name, op: regex, value: '^measure_'}
    require_streams: [primary]
    group_by: {field: start.sample_id, fallback: '(unknown)'}
    label: '{start.sample_id} · {start.plan_name}'
  - id: failures
    name: Failed or aborted
    where: {field: status, op: in, value: [failed, aborted]}
    # Exactly one of field or fields. No list-element fan-out.
    group_by:
      fields: [start.beamline, start.station]
      fallback: '(unknown)'
lanes:
  order: [measurements, failures]
  min_lanes: 4
  max_lanes: 100
```

Required root keys are `version`, `id`, `title`, `tracks`. Unknown keys are
errors everywhere. Optional defaults are `roots` = configured roots,
`scope` = empty conjunction, `where` = `all: []`, `require_streams` = `[]`,
no `group_by` = one ungrouped lane per track, `label` omitted = view-table
label, `lanes.order` = file track order, `lanes.min_lanes` = 0, and
`lanes.max_lanes` = deployment budget. Explicit null is not an alias for an
omitted configuration key. Scope is restricted as in finding 1. Predicates
use the typed rules in finding 6; structured equality is adapter-only.
Grouped missing/null/empty-string components use a distinct sentinel, displayed
using `fallback` (default `(unknown)`). Multi-field groups are ordered tuples,
with fallback applied independently to missing components. Arrays/objects
are unsupported grouping results in this v1 proposal and produce a visible
evaluation issue. An absent group is distinct from a missing field's group.

`max_lanes` counts logical lanes and can only lower the deployment budget;
deployment limits also bound final sub-lanes and total placements. An overflow
is a reported render failure, not silent omission. Track order, group ordering,
sub-lane allocation and query/preset padding precedence must be fixed as in
findings 9–10. Preset identities are slugs of 1–64 ASCII characters, starting
with a lowercase letter and continuing with lowercase letters, digits or
hyphens; titles/names are display text, not identities. Duplicate IDs are errors.

Suggested initial loader limits, to be documented and tested rather than left
implicit: 256 KiB per file, 32 nesting levels, 10,000 parsed nodes, 32 tracks,
1,000 predicate nodes and 1,024 characters per regex. These are proposed
operating budgets, not measured capacity claims. Bound registry/file count
and display strings too. Do not infer a regex execution-time guarantee from
these limits. Put deployment-wide workload budgets in settings, not server
addresses or credentials in YAML.

The registry always includes `instruments`; no deployment file may claim its
ID. Its strategy retains the existing implementation and contracts. A file
describing ordinary tracks is not assumed to be capable of serialising that
strategy. `/presets` returns stable `id`/`title` rows; results expose the
resolved ID/revision and evaluation time without exposing file paths. Document
new placement helpers and selection context separately from preset format
version. Changing the meaning of a v1 key needs a new format version and a
migration, while adding a new optional construct must preserve old files and
their semantics.

## What I ran and what remains unverified

- Read the specified design/decision/endpoint documents and external
  CONFIG_SPEC v1, plus `clips.py`, `timeline.py`, `layout.py`, `views.py`,
  `run_state.py`, overview/trend/selection adapters, fixture helpers and the
  dashboard generator. Concurrent working-tree edits were left alone.
- Checked installed versions with `uv run --locked`: Tiled 0.2.21,
  Bluesky 1.14.6, event-model 1.23.1, PyYAML 6.0.3 and SQLAlchemy 2.1.8.
  Fetched the exact Tiled 0.2.7/0.2.21, Infinity 4.1.6 and Grafana 13.3.1
  source/documentation tags linked above; did not substitute latest docs.
- Ran `/private/tmp/ett-review07-probe.py` through `uv run --locked` against
  disposable SQLite and PostgreSQL catalogues, reusing `tests/conftest.py`'s
  server construction. Acquisition fixtures used real RunEngine/count plans
  and `ophyd.sim.SynSignal`, written through `TiledWriter`, with synthetic
  metadata only. Tested missing/null, booleans, integer/float equivalents,
  numeric-looking strings, array and object operands, array/object supersets,
  nested arrays, and descriptor-only/buffered streams. Results are in
  `/private/tmp/ett-review07-results.json` and
  `/private/tmp/ett-review07-results-expanded.json`. The PostgreSQL containers
  were removed in `finally` blocks. No production service was contacted.
- The initial SQLite list-equality probes returned HTTP errors as recorded.
  An intermediate expanded probe with retries disabled stopped on a connection
  reset following such an error; it is not evidence for predicate semantics.
  The completed expanded probe skipped known unsupported SQLite bindings and
  used three client attempts. PyYAML scalar/duplicate-key probes ran in the
  same temporary script.
- Ran `PYTHONDONTWRITEBYTECODE=1 uv run --locked pytest -q
  tests/contract/test_layout.py tests/unit/test_clips.py
  tests/unit/test_timeline.py tests/unit/test_grafana_provisioning.py
  -p no:cacheprovider --basetemp=/private/tmp/ett-review07-pytest`:
  **57 passed**, including PostgreSQL, with three dependency deprecation
  warnings. These verify existing behaviour; presets are not implemented and
  therefore have no implementation test coverage yet.
- Ran `uv run --locked mkdocs build --strict --site-dir
  /private/tmp/ett-review07-docs`: passed. MkDocs reported the new response as
  outside the navigation at INFO level; navigation was left unchanged to
  respect the response-only scope.
- Did not run a Tiled 0.2.7 server/client stack, a Grafana browser session,
  an Infinity `/presets` query against a running adapter, or a dynamic-dashboard
  provisioning experiment. Source inspection establishes supported mechanisms,
  not their complete integration. No lab data or operator trial was available.
  Full lint/type/test checks were not necessary for a response-only review;
  no PR or implementation change is being proposed here.
