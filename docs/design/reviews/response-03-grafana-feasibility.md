# Review 03: Grafana feasibility

Reviewed 2026-11-15 against plan revision 5. **Proceed with a Grafana
prototype, but keep the panel and image gates open.** State timeline fits
non-overlapping intervals; it is not a general-purpose clip editor.

## Versions and verification limits

| Component | Version checked | Evidence |
|---|---|---|
| Grafana OSS | **13.3.1**, released 2026-11-10 | [Official download](https://grafana.com/grafana/download?edition=oss&platform=docker); source tag `v13.3.1` below |
| Infinity | **4.1.6**, updated 2026-11-04 | [Official plugin listing](https://grafana.com/grafana/plugins/yesoreyeram-infinity-datasource/); source tag `v4.1.6` below |
| Optional Business Variable | **5.2.0** | [Official listing](https://grafana.com/grafana/plugins/volkovlabs-variable-panel/) |
| Optional Business Charts | **7.3.0** | [Official listing](https://grafana.com/grafana/plugins/volkovlabs-echarts-panel/) |

Infinity's published Grafana version constraint admits 13.3.1. The two
optional panels require Grafana >=12.3.0. Version compatibility declarations
are not integration-test results.

Confidence labels below mean **verified** by inspected tagged source or a
local check, **documented** by official documentation, and **unverified**
where execution is still needed. Source verification does not establish
browser usability.

Docker's CLI exists, but `docker info` could not connect to the local daemon.
An attempt to launch Docker also failed. No Grafana container or browser test
ran. In particular, this review does **not** pass the rendering, UID-click,
performance or authenticated-image gates.

Throwaway static inputs are outside the repository at
`/private/tmp/event-timeline-review03.aCM217/`: `cases.json` contains ten
synthetic clips covering the requested cases; `load-20x100.json` contains
2,000 distinct clips on 20 lanes. Counts and identities were checked locally;
these are test inputs, not rendering results. They contain no Tiled data.
Only this response is added to the repository.

## 1. Timeline panel

**Answer:** use built-in **State timeline**, status value mappings for colour,
`mergeValues: false`, and a separate series for each physical lane or
sublane. Treat gap handling and hidden UID links as explicit acceptance
tests. Overlapping intervals cannot share one state series faithfully.

### Frame contract

Grafana accepts a frame containing a start time, an optional second time
field for interval ends, and state fields. For this application the clearest
target is **one frame per non-overlapping lane**, with these columns:

| Field | Grafana type | Purpose |
|---|---|---|
| `start_ms` | time | Display start, UTC epoch milliseconds |
| `end_ms` | time | Display end, UTC epoch milliseconds |
| `status` | string | Only visible state field; display name identifies the lane |
| `uid`, `label` | string | Same-row identity and descriptive helpers |
| `clip_from_ms`, `clip_to_ms` | number | Original extent for zoom; deliberately not additional time fields |

Sort by start, keep helper arrays aligned, and preserve original times when
clipping at the window. Set helper overrides to hide them from visualization,
legend and tooltip while retaining the fields for links. Removing them with
a field-filtering transformation removes the link context too.

This is supported by the tagged implementation: the first two time fields
are interpreted as start/end, end timestamps introduce null boundaries, and
additional string/numeric fields remain graphable unless hidden.
[Grafana 13.3.1 timeline preparation](https://github.com/grafana/grafana/blob/v13.3.1/public/app/core/components/TimelineChart/utils.ts).
The official [State timeline documentation](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/state-timeline/)
also documents one- and two-time-column input, gaps and equal-value merging.

**Hidden UID support is plausible, rather than an automatic failure.** When
links exist, GraphNG aligns helper fields with the plotted data, constructs
link suppliers from the aligned frames, then omits hidden visual fields.
State timeline obtains links using the hovered interval's aligned row index.
That explicitly addresses the unequal-timestamp alignment problem in this
version. It still needs the exact end-to-end click test through Infinity.
[GraphNG 13.3.1](https://github.com/grafana/grafana/blob/v13.3.1/public/app/core/components/GraphNG/GraphNG.tsx),
[StateTimelinePanel 13.3.1](https://github.com/grafana/grafana/blob/v13.3.1/public/app/plugins/panel/state-timeline/StateTimelinePanel.tsx).

### Requested acceptance cases

All browser results in this table remain **unverified**. The conclusions
describe the source-supported expectation and the necessary test.

| Case | Expected result / acceptance requirement |
|---|---|
| Touching successes, distinct UIDs | With merging off, two start samples remain separate intervals even when both values are `success`. Click both interiors and both sides of the boundary; verify different UIDs. |
| Real gap | Explicit interval ends, or an explicit null event at the gap start, stop the preceding state. Leave “Connect null values” disabled. Verify the gap has no selectable run. |
| Two lanes, unequal times | Separate frames can be aligned internally. Verify UID selection on each side of every timestamp introduced by the other lane. |
| Window-crossing run | Include overlapping runs in the adapter response. The renderer clips to the plot bounds; retain original extent for summary/zoom. Include both left- and right-crossing cases. |
| Open run | Adapter supplies a distinct `open/unconfirmed` display state and a finite display end. On a healthy live window it advances at each successful fetch; cap at the window end and last successful observation time. On fetch failure it freezes and shows stale health. Missing stop does not establish “running.” |
| Overlapping processing attempts | **Fails on one state series.** A later start changes that series' state; it cannot retain two independently selectable boxes at the same time. Allocate sublanes. |
| Three seconds in twelve hours | At 1,200 plot pixels this is only 0.083 logical pixels. The renderer enforces a minimum box width of one canvas pixel, but that does not make neighbouring tiny clips reliably distinguishable. Provide zoom and a selectable runs table. |
| UID, range and refresh | For every case, inspect the resulting `var-run`, keep relative/absolute range semantics, refresh repeatedly and check that selection remains the same UID. Test duplicate labels and equal statuses. |

The renderer walks one sequence of states per series, ends a box at the
next state, clips to the plot rectangle and enforces its minimum box width.
These observations explain the overlap and tiny-clip limitations; they are
not screenshots or measured usability results.
[Grafana 13.3.1 renderer](https://github.com/grafana/grafana/blob/v13.3.1/public/app/core/components/TimelineChart/timeline.ts).

### Long tables, wide tables and Infinity transport

A raw `uid,lane,start,stop,status,label` table is **not** sufficient input
without configuration: `lane`, `uid` and `label` become state series, rather
than a lane dimension. Grafana's **Partition by values** transformation can
split a table into frames by lane. This is a useful throwaway experiment,
but shipping lane grouping, overlap allocation or gap reconstruction in
dashboard transformations conflicts with the repository's adapter boundary.
[Official transformation documentation](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/query-transform-data/transform-data/).

Make `/clips/frame` an explicit adapter projection. Its **transport through
Infinity remains a gate**, not an established contract. Infinity's backend
JSON path constructs one frame per query; selecting output “Data frame” does
not establish that it accepts an arbitrary array of serialized Grafana
frames. A straightforward spike is one query per configured lane/sublane,
each returning the interval table above, with the adapter caching the shared
window search. This costs multiple adapter requests, not repeated Tiled
searches. Dynamic sublane discovery still needs proof.
[Infinity 4.1.6 JSON backend](https://github.com/grafana/grafana-infinity-datasource/blob/v4.1.6/pkg/infinity/jsonBackend.go),
[query settings](https://grafana.com/docs/plugins/yesoreyeram-infinity-datasource/latest/query/).

An alternative is one adapter-generated wide boundary table with a state
and UID helper **per lane**, but do not assume a naive pivot works: null at
another lane's timestamp can introduce a false gap, while forward-filling
equal statuses with merging off can introduce false clip boundaries. A
single global `uid` column also cannot identify simultaneous clips on
different lanes. Accept a wide encoding only after the boundary and link
tests pass. Do not introduce a frontend UQL pivot as the production solution.

### Overlap scheme and scale

Allocate processing attempts to sublanes in the adapter: sort by
`(start, uid)`, assign the lowest numbered sublane whose preceding interval
has ended, otherwise add one. Touching intervals can share a sublane. Keep
the logical lane key separate from its displayed sublane index, and retain
every attempt. Use the same generic rule for overlapping acquisition runs.
Recompute deterministically for a window; do not promise the index is a
persistent run property when older intervals enter the window.

If operators require overlapping boxes inside one band, evaluate published
**Business Charts 7.3.0** with an ECharts custom-series rectangle renderer.
It is a maintained panel framework, not an out-of-the-box verified run
timeline; its rendering code and UID selection need testing. Keep all run
semantics in the adapter.
[Official Business Charts documentation](https://grafana.com/docs/plugins/volkovlabs-echarts-panel/latest/).

**20 lanes / 2,000 clips: unverified performance.** There is no demonstrated
capacity limit at that count, nor a measured pass. State timeline has lane
pagination (`perPage`). A common grid of approximately 4,000 boundaries and
20 lanes already represents about 80,000 state cells before UID helpers;
wire size, alignment and refresh work matter as well as drawing. Measure
payload size, query and render latency, click responsiveness and refresh
behaviour at a fixed viewport with 5-second refresh, including sublanes.
Pagination should not be assumed to bound backend work.

**Confidence:** frame handling, alignment and overlap/minimum-width limits
verified in source; configuration documented; full acceptance and scale
unverified.

## 2. Infinity to the adapter

**Answer: yes.** Use JSON with a **JSONata or JQ backend parser**, URL source,
GET, explicit column types and Table/Data frame output. Keep domain logic in
the adapter. UQL/GROQ/frontend parsing does not satisfy the backend-only rule.
[Infinity 4.1.6 query documentation](https://github.com/grafana/grafana-infinity-datasource/blob/v4.1.6/docs/sources/query/_index.md).

Template variables and Grafana `${__from}` / `${__to}` can be interpolated,
but those two built-ins default to **milliseconds**. Adapter endpoints use
seconds. Prefer the Infinity backend macros:

```text
http://adapter:8000/clips?from=${__timeFrom:date:seconds}&to=${__timeTo:date:seconds}
http://adapter:8000/runs/${run:percentencode}/views
```

Use these time macros for queries, and URL time-range variables for links.
Validate decoded identities, view IDs, ranges and limits in the adapter.
[Infinity 4.1.6 macros](https://github.com/grafana/grafana-infinity-datasource/blob/v4.1.6/docs/sources/query/macros.md),
[Grafana built-in variable units](https://grafana.com/docs/grafana/latest/visualizations/dashboards/variables/built-in-variables/).

Set `jsonData.allowedHosts` explicitly, using a fixed URL prefix such as
`http://adapter:8000/`; never interpolate the host. In 4.1.6 the check combines
a URL-prefix match with a parsed hostname match, and an empty allow-list
allows URLs generally. It is a destination restriction, not authentication
or per-endpoint authorization.
[Infinity 4.1.6 URL validation](https://github.com/grafana/grafana-infinity-datasource/blob/v4.1.6/pkg/infinity/client.go).

**Direct Tiled diagnostic: feasible, lower priority, unexecuted.** A separate
throwaway Infinity source can GET `/api/v1/search/...`, select JSON:API's
`data` rows and extract `attributes.metadata.start` / `stop`. Supply
`Authorization` using a provisioned secure header whose environment value
is the complete `Apikey …` header, and allow-list only that Tiled base URL.
Follow pagination and project metadata; a successful first page is not a
complete search. This does not supply window unions, processing joins,
state or views. Keep it outside the shipped dashboards.
[Infinity 4.1.6 secure-header provisioning](https://github.com/grafana/grafana-infinity-datasource/blob/v4.1.6/docs/sources/configure.md),
[Tiled access evidence from review 02](response-02-tiled-access.md).

**Confidence:** backend parsing, macros, allow-list and secure-header
mechanisms verified/documented; actual adapter and direct-Tiled queries
through this Grafana/Infinity pair unverified.

## 3. Selection, zoom and a playhead

**Answer:** selection and zoom are ordinary data links. Prefer two named
tooltip links, “Select run” and “Zoom to run,” targeting the same dashboard
UID with new-tab opening disabled. A selection-link template is:

```text
/d/<dashboard-uid>/<slug>?var-run=${__data.fields["uid"]}&${__url_time_range}
```

Preserve the other filters explicitly with their `queryparam` format, and
preserve the chosen refresh interval/kiosk URL state when navigation uses a
complete URL. Do not append `${__all_variables}` blindly: it can duplicate
the old `var-run`. The time-range macro preserves relative expressions such
as `now-12h` / `now`; substituting epoch-valued `${__from}` / `${__to}` would
freeze that live range. The delimiters `?` and `&` are supplied by the link.
[Official data-link variables](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/configure-data-links/).

The zoom link sets `var-run` and uses numeric millisecond helpers as
`from=${__data.fields["clip_from_ms"]}&to=${__data.fields["clip_to_ms"]}`.
Use the original run extent, with a defined finite end for open runs and
padding for zero-length extents. Do not send adapter epoch seconds as
Grafana URL milliseconds.

**Avoiding a full page reload is not established by the URL alone.** Use the
normal tooltip/menu path first and test same-dashboard routing, scroll
position and query counts. An upstream issue remains open for `oneClick`
links bypassing scene routing; its report is from 12.4.2, so it is a reason
to test 13.3.1, not proof that every panel in 13.3.1 fails. Do not make
one-click selection a v1 promise.
[Grafana issue #123836](https://github.com/grafana/grafana/issues/123836),
[13.3.1 data-link click handlers](https://github.com/grafana/grafana/blob/v13.3.1/packages/grafana-ui/src/components/DataLinks/DataLinkButton.tsx).

Define `run` as a hidden identity variable that survives refresh; refreshing
a list of available runs must not reset it to its first option. Test a
selected run remaining selected after it leaves the current window.

Enable dashboard **shared crosshair** and give State timeline and Time
series the same time range without panel overrides. State timeline's source
connects the dashboard cursor event bus to its chart. This supports a
timestamp-aligned hover guide despite different axis margins. It is not a
persistent or draggable playhead, and an I(Q) chart has a different x-domain.
[13.3.1 State timeline cursor integration](https://github.com/grafana/grafana/blob/v13.3.1/public/app/plugins/panel/state-timeline/StateTimelinePanel.tsx).

**Confidence:** URL interpolation and cursor hooks documented/source
verified; exact UID, soft navigation, range/refresh preservation and visual
crosshair alignment unverified in a browser.

## 4. Per-instrument views

**Answer:** current Grafana supports conditional show/hide rules, including
variable conditions, but **panel rules require Auto grid**. They cannot
simply be added to every manually positioned panel in the proposed NLE
layout. The tagged conditional-rendering implementation supports variable
comparisons; the official layout documentation states the grid restriction.
[Panel options](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/configure-panel-options/),
[Grafana 13.3.1 variable condition implementation](https://github.com/grafana/grafana/blob/v13.3.1/public/app/features/dashboard-scene/conditional-rendering/conditions/ConditionalRenderingVariable.tsx).

The adapter should resolve views and return a generic view kind/capability
such as `table`, `time-series`, `curve` or `image`. Conditions may use that
result; dashboards must not encode instrument-name tests. Keep a summary
and explicit “no plottable data” fallback available.

**Least fragile v1:** a fixed overview layout with built-in summary/table
and timeline panels, linking to small detail dashboards by **view kind**.
They can share run/range variables without duplicating instrument rules.
If inline switching is essential, prototype an Auto grid viewer grouping
with variable-based rules, and prove that its exported layout keeps the
desired top/bottom geometry. Query-based “has data” rules can flicker during
loading or errors and are not a replacement for view availability.

Repeated rows are suitable for multiple instruments or selected values, not
a general conditional switch. Do not depend on an empty repetition hiding
the original row. Dynamic layouts use the current v2 dashboard schema;
export the resource from the pinned Grafana rather than inventing classic
`gridPos` visibility properties. Provision its resource wrapper as described
in section 7.

**Confidence:** conditional capability and Auto grid restriction
documented/source verified; exact NLE geometry and round-trip provisioning
of a dynamic dashboard unverified.

## 5. Viewer panels

### a. Corrected 2D PNG

**Panel:** built-in generally available **Text**, HTML mode, containing a
fixed image template, for example:

```html
<img src="/run-images/${run:percentencode}/image?view=${view:percentencode}&amp;scale=log"
     alt="Corrected image" style="max-width:100%;max-height:100%;object-fit:contain">
```

Keep `disable_sanitize_html=false`. Interpolation followed by sanitizing is
present in the pinned Text implementation; no script or iframe is needed.
Use validated identity/view variables, not metadata interpolated as HTML.
[Grafana 13.3.1 Text implementation](https://github.com/grafana/grafana/blob/v13.3.1/public/app/plugins/panel/text/v1/TextPanel.tsx),
[official Text documentation](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/text/).

**Proposed authenticated route:** a same-origin reverse proxy serves
Grafana and exposes only `/run-images/...` to the adapter's image endpoint.
Before forwarding each image GET, it validates the browser's Grafana
session, for example with an internal `auth_request` to Grafana's
`GET /api/user` carrying the session cookie. Grafana 13.3.1 protects that
route with `reqSignedInNoAnonymous`. Enforce the intended organization/data
scope too; existence of any Grafana account is not automatically permission
for every run. Alternatively authenticate both paths at the same gateway.
[Grafana 13.3.1 API authorization](https://github.com/grafana/grafana/blob/v13.3.1/pkg/api/api.go).

The proxy must reject unauthenticated requests, constrain the forwarded
path/method, and keep the rest of the adapter private. The adapter obtains
Tiled bytes using its server-side credential and returns only bounded PNG
output. Browser requests contain an ordinary application session, never a
Tiled key or an adapter service secret. Use private cache policy, no shared
public image cache, and recheck authorization even on cache hits. Infinity's
allow-list does not protect browser image requests.

This is a concrete design to test, **not a verified proxy configuration**.
Test signed-in, signed-out, expired-session and wrong-scope requests, direct
URL access, and the actual browser cookie path. An anonymous Grafana Viewer
does not pass `/api/user` authentication: image-enabled kiosks need a
signed-in Viewer session or an authenticated gateway covering both paths.

A Text panel's unchanged image URL need not refetch just because dashboard
queries refresh. Immutable closed processing outputs work well. For changing
images, prove a revision URL/update mechanism; built-in **Table** with an
Image cell and an adapter-returned revisioned same-origin URL is another
option. Do not add arbitrary JavaScript polling or disable sanitizing.
[Official Table cell options](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/table/).

### b. I(Q), log–log and uncertainty

**Panel:** built-in **XY chart**, manual mapping, numeric `Q` as X and `I`
as Y, line/points as appropriate. Configure logarithmic base-10 scale on
both fields; explicitly apply the scale override to the **X field** as well
as Y. Set units and labels from the resolved view.
[XY chart documentation](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/xy-chart/),
[13.3.1 X/Y scale implementation](https://github.com/grafana/grafana/blob/v13.3.1/public/app/plugins/panel/xychart/scatter.ts).

The inspected XY series configuration has no uncertainty/error-bar mapping.
Do not promise native error bars. The closest built-in option is three
aligned series: I and adapter-computed I−σ / I+σ as lighter or dashed
bounding curves, with uncertainty in a companion table. Report excluded
non-positive/non-finite values explicitly on log axes; do not invent small
positive intensities to make them plot.
[13.3.1 XY configuration](https://github.com/grafana/grafana/blob/v13.3.1/public/app/plugins/panel/xychart/panelcfg.gen.ts).

If actual error bars or a shaded uncertainty band are required, evaluate
Business Charts 7.3.0 with an ECharts renderer before writing a new plugin.
That adds maintained renderer configuration/code and a test obligation; it
does not move scientific transformations out of the adapter.

### c. Metadata

**Panel:** built-in **Table**, with adapter-flattened `key,value` rows,
bounded lengths and explicit missing values. Render metadata as text, not
untrusted HTML. Keep UID and related-run links separate from display labels.

**Confidence:** Text sanitizing, API authentication middleware and XY scale
support verified in source; Table/XY capabilities documented; authenticated
image delivery, image refresh and actual curve rendering unverified.

## 6. Selector panel

**Answer: a published panel exists.** **Business Variable 5.2.0**, plugin ID
`volkovlabs-variable-panel`, puts dashboard-variable controls inside a panel.
Its published modes include Table, Button, Minimize and Slider, with text
input and single/multi-value selection. Grafana Labs now maintains it on a
best-effort basis. Use it for a top-left instrument/run/view selector if
that placement matters; pin and test it with 13.3.1.
[Official Business Variable listing](https://grafana.com/grafana/plugins/volkovlabs-variable-panel/).

It controls dashboard variables; it does not replace adapter-provided view
or run choices. Keep identity values distinct from human-readable option
labels. Variable queries still go through Infinity to the adapter.

For a built-ins-only v1, keep ordinary pickers in the variable bar and put a
selectable runs **Table** in the top-left panel. There is no need for a new
custom selector plugin. Business Variable changes the plan's “built-in
panels only” constraint to “built-ins plus pinned published panels.”

**Confidence:** documented supported plugin, version and modes; UX,
dependency updates and refresh behaviour with these dashboards unverified.

## 7. Dashboards as code, provisioning and container

**Answer:** keep dashboard JSON and provisioning YAML in `grafana/`; use
file provisioning for this small stack. Give dashboards and the data source
stable UIDs, reference the source by UID, set `editable: false` for the
source and `allowUiUpdates: false` for dashboard providers. Mount source
files read-only; keep Grafana's database in its own writable volume. Use a
30-second file-provider polling interval for bind-mounted files. Export
dynamic/v2 dashboards with their Kubernetes-style `apiVersion`, `kind`,
`metadata`, `spec` wrapper; a bare v2 `spec` is not a classic dashboard.
[Official provisioning documentation](https://grafana.com/docs/grafana/latest/administration/provisioning/).

Minimal adapter source provisioning, with no Tiled credential in Grafana:

```yaml
apiVersion: 1
datasources:
  - name: Timeline adapter
    uid: timeline-adapter
    type: yesoreyeram-infinity-datasource
    access: proxy
    editable: false
    jsonData:
      allowedHosts:
        - http://adapter:8000/
```

Backend parser selection belongs in **each query**, including variable
queries; `access: proxy` alone does not enforce it. For any necessary
adapter authentication, provision the header name in `jsonData` and its
environment-supplied value in `secureJsonData`, never dashboard JSON. Leave
dangerous HTTP methods disabled.
[Infinity 4.1.6 provisioning examples](https://github.com/grafana/grafana-infinity-datasource/blob/v4.1.6/docs/sources/configure.md).

Build the project's image from **`grafana/grafana:13.3.1`**, resolving and
recording its actual digest when building. Install **Infinity 4.1.6** during
the image build with Grafana's plugin CLI; optionally install Business
Variable 5.2.0. Record versions and verify the built image starts without
downloading newer plugins. For a development alternative, synchronous
preinstallation supports a versioned setting such as
`GF_PLUGINS_PREINSTALL_SYNC=yesoreyeram-infinity-datasource@4.1.6`.
Build-time installation avoids depending on plugin downloads at startup.
[Official plugin installation documentation](https://grafana.com/docs/grafana/latest/administration/plugin-management/plugin-install/).

For an intentionally anonymous read-only operator screen:

```text
GF_AUTH_ANONYMOUS_ENABLED=true
GF_AUTH_ANONYMOUS_ORG_NAME=Operators
GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
```

Create that organization, keep administrative secrets in the environment,
and use kiosk URL mode to hide navigation. Kiosk mode is presentation, not
authorization. Viewer access prevents dashboard editing; the source/adapter
still determines which data are accessible. Expose only the intended
operator data to that organization. Anonymous access must be reconciled
with the authenticated image route as explained in section 5.
[Official anonymous authentication settings](https://grafana.com/docs/grafana/latest/setup-grafana/configure-access/configure-authentication/anonymous-auth/).

**Confidence:** provisioning, allow-list configuration, versioned plugin
installation and anonymous Viewer settings documented; assembled image,
dashboard round-trip and kiosk/image combination unverified.

## Recommendation for version 1

- **Timeline:** built-in State timeline, merging off, explicit interval ends,
  hidden identity helpers, generic processing sublanes, and a companion runs
  Table for short clips and precise selection. Make the adapter projection
  and its Infinity transport a prerequisite, not a dashboard afterthought.
- **Viewers:** built-in Table and Time series initially; XY for corrected
  I(Q) with optional bounding curves; sanitized Text plus the authenticated
  same-origin route for immutable corrected PNGs. Prefer small view-kind
  detail dashboards until an Auto grid viewer proves the desired layout.
- **Selectors:** variable bar plus a runs Table for the smallest dependency
  set; add pinned Business Variable if in-panel controls are required.

A custom panel becomes justified if sublanes are unacceptable and a tested
Business Charts renderer cannot provide overlapping UID-selectable clips;
if a persistent draggable/scrubbable playhead is required; or if measured
tiny-clip interaction/performance cannot meet operator needs. True
uncertainty bars alone first justify trying Business Charts, not immediately
maintaining a new plugin. An unresolved Infinity multi-frame transport is
a data-source/projection problem and should not by itself trigger a custom
visualization plugin.

## Corrections to the plan

1. **Sections 8 and 9:** describe State timeline as conditional on the
   runtime gate. Add the pins above, exact-UID checks after alignment,
   relative-range preservation, refresh persistence, navigation behaviour
   and the 20-lane/2,000-clip benchmark. No gate passed in a container here.
2. **Section 6.1:** make `/clips/frame` and its Infinity encoding explicit.
   Specify milliseconds for Grafana time fields while retaining seconds in
   existing adapter contracts. Add/version fields rather than changing their
   established meanings. Do not promise a raw long table or arbitrary
   serialized frame array will work unchanged.
3. **Sections 8 and 9.1.2:** require generic sublanes for overlaps. Keep true
   run times; use zoom/table access for tiny clips and disclose any visual
   width accommodation. Do not silently drop competing attempts.
4. **Section 8:** call the crosshair a shared hover guide, not a persistent
   playhead. Prefer two tooltip links until same-dashboard soft navigation
   has passed on the pinned versions.
5. **Sections 7 and 8:** choose fixed overview plus view-kind detail
   dashboards, or explicitly validate Auto grid conditional viewers. Keep
   instrument decisions in the view table. Decide whether the top-left
   picker warrants the published Business Variable dependency.
6. **Sections 6 and 9.1.3:** specify the authenticated image gateway, session
   and scope checks, private caching and image-refresh policy. An anonymous
   Grafana screen needs gateway authentication or a signed-in Viewer for
   protected images. Keep sanitizing enabled.
7. **Sections 7 and 10:** XY supports log–log I(Q), but native uncertainty
   bars are not established. Name bounding curves or the published-panel
   fallback. Add file-provisioning schema, stable UIDs and image/plugin pins
   to the container work.
