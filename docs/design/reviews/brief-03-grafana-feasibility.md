# Codex brief 03: check that Grafana can carry the NLE layout

## Context

`~/Code/event_timeline_tiled` is a new prototype: a read-only
timeline of Bluesky runs from Tiled, as a Grafana dashboard laid out like a
video editor (NLE), shipped as our own Grafana container. Data comes through
the Infinity data source from a
small adapter serving flat JSON/CSV tables. The plan is `docs/design/plan.md`
(revision 5); sections 6, 7 and 8 rest on assumptions about Grafana that
have not been checked. The maintainer has not used Grafana before. Built-in
panels and published plugins come first; custom plugins only if those fall short.

First users are operators following a queue: one timeline lane per
instrument plus a "corrected" lane under it for MoDaCor processing results,
runs as clips coloured by status, running runs growing to "now", auto-refresh
every few seconds. MOUSE comes first (views: readings table; after MoDaCor
processing, a corrected 1D curve I(Q) and a corrected 2D image); a second
instrument (electrochemistry, 1D time series) joins later.

## Task

Check each point against the current Grafana OSS release and the current
Infinity plugin release (state the versions you checked), using official
documentation, release notes and source. Where feasible, confirm in a
throwaway Grafana container (Docker is available) with a static JSON file
served locally; keep that outside the repository. Do not add code to the repository.

1. Timeline panel. Can the built-in State timeline panel show runs as clips,
   fed from a static file of synthetic rows? Test these acceptance cases (plan
   section 9, step 1.2): two touching successful runs with different UIDs (with
   merging of equal values turned off); a real gap; runs on two lanes with
   unequal times; a run crossing the window edge; an open run drawn in its own
   style up to "now"; overlapping processing attempts on one lane; a run of a
   few seconds in a 12-hour window. Each per-clip data link using a hidden
   `uid` field (for example `?var-run=${__data.fields.uid}`) must select the
   exact UID, keep the time range and survive refresh. What input frame shape
   does it need, and can transformations produce it from a long table
   (`uid, lane, start, stop, status, label`), or must the adapter emit the
   wide shape? How does it behave with 20 lanes and 2,000 clips? If overlaps
   cannot be shown, which sub-lane scheme or published panel would?
2. Infinity to the adapter: with backend parsing only and a URL allow-list,
   can Infinity read the adapter's JSON with variables and
   `${__from}`/`${__to}` in the URL? As a lower-priority diagnostic only: could
   it read Tiled's `/api/v1/search/...` JSON:API response directly, keeping a
   credential server-side?
3. Selection and zoom: does a data link that changes `var-run` keep the time
   range and avoid a full page reload? Can a second link set `from`/`to` to the
   clip extent? Does the shared crosshair line up across the timeline and a
   time series panel, so it can serve as a playhead?
4. Per-instrument views: how can one dashboard show only the panels that apply
   to the selected run's instrument (conditional rendering in the current
   dashboard schema, repeated rows on a variable, or a link to a per-instrument
   detail dashboard)? Which is least fragile?
5. Viewer panels: the best built-in or maintained way to show (a) a corrected
   2D image served as PNG from a URL, (b) a 1D scattering curve I(Q) with a
   non-time x-axis, log–log axes and, ideally, uncertainty bars (XY chart or
   otherwise), (c) a key/value metadata table. For (a), the image is fetched by the
   browser, not by Infinity's backend: find a route that is authenticated
   (behind Grafana's sign-in or an equivalent same-origin proxy), never sends
   a Tiled credential to the browser, and does not need HTML sanitising
   turned off globally. Name the panel and settings, and the security
   implications. For (b),
   if uncertainty bars are not supported, what is the closest option?
6. Selector panel: is there a supported way to put variable pickers, or an
   equivalent, inside a panel at the top left rather than in the variable bar?
   If not, the least clumsy pattern.
7. Provisioning and container: the recommended way in current Grafana to keep
   dashboards as code in a repository, provision Infinity with a URL allow-list,
   pin Grafana and plugin versions in an image, and set anonymous read-only
   (viewer) access for an operator screen.

## Response

Write to `docs/design/reviews/response-03-grafana-feasibility.md`:

- One section per point: the answer, evidence (documentation link with
  version, or what you saw in the container), and confidence (verified /
  documented / unverified).
- A recommendation for the version 1 timeline, viewer and selector panels, and
  the conditions under which a custom panel plugin becomes necessary.
- A short list of corrections to `docs/design/plan.md`.
