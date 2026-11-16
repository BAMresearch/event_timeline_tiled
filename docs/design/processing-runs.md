# Processing runs: draft contract and MoDaCor-side requirements

Status: draft (2026-11-15), from plan revision 6 and Codex reviews 01
(findings 8–14) and 02 (§1a, §1b). To be agreed with MoDaCor. The viewer in
this repository only reads these runs; everything that creates them lives in
MoDaCor (generic parts) and MoDaCor-examples (MOUSE rules and pipelines).

## 1. Processing-run schema, version 1

Every processing run is a valid Bluesky run (event-model 1.23 schemas) written
through `TiledWriter`.

**Start document**, in addition to the required `uid` and `time`:

| Key | Meaning |
|---|---|
| `processing_schema_version` | `1` |
| `kind` | `"modacor_correction"`; the viewer uses this, not the presence of `derived_from`, to treat a run as a correction |
| `product` | what it produces, e.g. `"saxs_1d_2d"`; with the pipeline identity it defines a result family |
| `facility`, `instrument`, `station` | copied from the measured run |
| `derived_from` | non-empty list of the measured run UIDs this result is *of* (normally one) |
| `inputs` | other inputs by role (`background`, `intensity_calibration`, …), each a run UID or a file reference |
| `pipeline` | name, version and content hash of the pipeline configuration |
| `software` | MoDaCor version |
| `request_id` | stable ID of the work request (section 3), the same for every attempt at the same work |
| `attempt` | attempt number for that `request_id` |
| `modacor` | session ID and MoDaCor runtime/run ID, for tracing; recovery details if MoDaCor fell back from a partial to a full run |

The clip's times are the processing times (start and stop documents). The
measurement's times stay as provenance.

**Stop document**: required `uid`, `time`, `run_start`, `exit_status`
(`success | fail | abort`), plus `reason` on failure and `outputs`: the list
of products published.

**Usable result**: `exit_status == "success"` **and** every declared product
for that `product` published. A successful run without outputs (nothing to
do) states that in `outputs: []` and `reason`, and is not shown as a usable
correction. Partial outputs are `fail` with the products that were published
listed.

**Streams**: `corrected_1d` with `Q`, `I` and the uncertainty components of
`I` as MoDaCor names them, with units and the meaning of each uncertainty
stated; `corrected_2d` with `image` and explicit axis fields or metadata
(names, units). Final field names are agreed with MoDaCor.

### 1.1 Array representation (chosen after review 02)

All array products are **HDF5 files registered through stream-resource and
stream-datum documents**, written by `bluesky.callbacks.tiled_writer.TiledWriter`
(the class MOUSE uses; not the separate writer in `bluesky-tiled-plugins`).
Review 02 round-tripped three 2,000-point fields and a 2048×2048 float32
image this way on Tiled 0.2.8 and 0.2.18. The other options failed or were
unsuitable: 2D arrays inline in events fail in that writer; 1D inline arrays
work but come back as strings through the default DataFrame client;
`TiledSink` writes generic containers without stream metadata; the plugin's
newer writer mislabels image dimensions (an upstream fix could make it an
option later).

Rules, as tested:

- Descriptor fields declare `dtype: array`, an explicit `dtype_numpy`
  (e.g. `<f4`), `shape` and units, and `external: "STREAM:"`.
- The HDF5 file has one dataset per field with a leading result dimension:
  `(1, N)` for curves, `(1, H, W)` for images. Readers take index 0.
- One stream resource per field: MIME `application/x-hdf5`, parameters
  `dataset`, `swmr: false`, **`join_method: "stack"`** (the default, concat,
  gives different shapes) and chunks matching the dataset (e.g. `(1, N)`,
  `(1, 256, 256)`).
- One event per stream with its time and sequence number, without the
  external fields in its payload; then a stream datum with `indices [0, 1)`,
  `seq_nums [1, 2)` and the descriptor UID.
- The HDF5 file is closed before the stream datum is emitted; the stop
  document follows only after all products are published.

Deployment requirements that follow:

- The **Tiled server** must read the files: MoDaCor's output directory is
  mounted on the Tiled server at the registered path and listed as readable
  storage there. Readable by MoDaCor alone is not enough.
- Files of a closed processing run are never modified or moved; retention
  follows the raw data's.

### 1.2 Finding processing runs

`derived_from` stays a list. On PostgreSQL, "processing runs of measurement
X" is `Eq('start.derived_from', [X])` (JSONB containment). `Contains` must not
be used: it is substring matching. Tiled has no cross-run anti-join, so the
watcher and the viewer compute "measurements without processing" from two
searches. "Without processing" must say which: no attempt, no finished
attempt, or no usable result.

**Finality**: once the stop document is written, the run's outputs are never
overwritten. Reprocessing creates a new run. (MoDaCor's `TiledSink` can
overwrite arrays; the publisher must not use that for processing runs.)

**Ordering**: within a result family (`product` + `pipeline.name`) and
measurement, "latest" is by stop time, ties broken by start-document UID. The
viewer shows the latest attempt and the latest usable result.

Incompatible changes increase `processing_schema_version`; the viewer keeps
reading older versions and reports malformed runs instead of hiding them.

## 2. Publisher (MoDaCor, generic)

`TiledSink` writes arrays into generic containers; it does not produce a
Bluesky run. MoDaCor needs a publisher that:

- writes the start document when execution begins;
- publishes the declared outputs in the chosen representation;
- writes the stop document only after publication succeeded, or with
  `fail` and a reason when processing or publication failed;
- is hooked into success, failure and automatic partial→full fallback paths;
- reuses MoDaCor's I/O registries and runtime sink builders.

Lifecycle tests: success, failed processing, failed publication, process
death mid-run (the run stays open; see the watcher's reconciliation).

## 3. Watcher (MoDaCor server package, separately launched; rules in MoDaCor-examples)

Recommended over a RunEngine callback: it does not run in the acquisition
worker and can catch up. Catching up is not automatic, so the watcher needs:

- **One owner.** A single watcher process per deployment, not a loop inside
  the web server's request handling.
- **A durable work ledger** (a small local database is enough): which work
  requests exist, their state, attempts and last error.
- **Stable request identity** from the measured run UID, the input selection,
  the pipeline identity and a deliberate reprocessing generation.
- **Claims.** Before submitting, check for open and finished attempts with the
  same `request_id`, not only successes.
- **Bounded retries with back-off**, then give up and record why.
- **Reconciliation after restart and after uncertain HTTP outcomes**: a
  timeout does not mean the work did not run; look for the processing run in
  Tiled first.
- **Late arrivals**: scan overlapping time ranges or reconcile, rather than a
  strictly increasing acquisition-time cursor.

A RunEngine callback that only *notifies* the watcher can be added later to
cut latency; it does not replace the ledger.

## 4. Eligibility and inputs (MoDaCor-examples, MOUSE rules)

"Finished without a processing run" is not an eligibility rule. MOUSE rules
to define there:

- which runs qualify: measurement runs (not configuration or vacuum runs),
  `exit_status == "success"` **and** `COMPLETE` present in `destination`
  (a stopped run records success without `COMPLETE`);
- input readiness: the Eiger files and other products in `destination` are
  readable and complete on the processing host (a recorded path does not
  guarantee a valid mount);
- mapping from the run to MoDaCor sources: the sample, the matching
  **background** (required by MoDaCor's `mouse` source profile) and optional
  calibration;
- which pipeline applies.

For the viewer to show "awaiting processing" later, the processing side must
publish eligibility generically (for example, the watcher opening the
processing run as soon as it claims the work, or a separate eligibility
record). Until then the viewer shows "no result".

## 5. Job request (MoDaCor server)

Today the sample source is set by `PUT /v1/sessions/{id}/sources` (or
`POST /sample`) and processing is started by `POST /v1/sessions/{id}/process`.
These are two requests, and another client can change the sources in between.
Sessions and their run history are kept in memory, and `/process` runs to
completion before replying. Requirements:

- Initially, the watcher owns its session exclusively and serialises
  "set sources + process".
- For shared use later: one job request that fixes sources, sinks,
  provenance (`derived_from`, `inputs`, `request_id`) and admission together,
  reusing the existing registries and partial-rerun invalidation
  (`changed_sources`, `changed_keys`).
- The processing run's IDs (section 1) come from this request.
