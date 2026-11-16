# Review 02: reading Bluesky runs from Tiled

The adapter can read the proposed run and stream layout. The main corrections
are to the overlap searches, exact provenance matching, assumptions about cheap
table slicing, and the processing-array representation. Recommend HDF5 products
registered by stream-resource/stream-datum documents for processing schema v1.
That path round-tripped curves and a four-megapixel float image on both inspected
Tiled versions. PostgreSQL execution remains an open gate: Docker's daemon was
unavailable, so PostgreSQL results below are source analysis and SQL compilation,
not successful PostgreSQL integration tests.

## Scope, versions and reproducibility

The requested `brief-02-architecture.md` does not exist. This follows the supplied
`brief-02-tiled-access.md`, identified by plan revision 5 as review 02. The plan
was updated during this review; the current open-run horizon is **21 days**.
Neither that change nor the added queue-status scope changes the Tiled findings.
Only this response was added to the repository by this review.

Installed-source abbreviations used in citations:

- **M** = `~/Code/mouse_bluesky/.venv/lib/python3.14/site-packages`.
  Tiled **0.2.8**, Bluesky **1.14.6**, bluesky-tiled-plugins **2.0.2**,
  event-model **1.23.1**, ophyd **1.11.0**; PyArrow **23.0.1**, pandas **3.0.1**,
  Dask **2026.8.0**, DuckDB **1.3.2**.
- **D** = `~/Code/MoDaCor/.venv-dev/lib/python3.14/site-packages`.
  Tiled **0.2.18**; PyArrow **25.0.1**, pandas **3.0.5**, Dask **2026.8.0**.
  The second test environment used D's native dependencies and appended M to
  `sys.path` for missing acquisition packages, retaining the same Bluesky,
  plugin, event-model and ophyd versions. Installed package sources were not
  edited. These were two in-process client/server combinations, not a test of
  a 0.2.8 client connecting over the network to a 0.2.18 server.
- **MC** = `~/Code/MoDaCor/src/modacor`, checkout version **1.17.1**.
  The sink test confirmed it imported that checkout's `tiled_sink.py`.

Throwaway scripts and focused logs are in
`/private/tmp/event-timeline-review02.CgFF08/`: `probe.py`, `assets.py`,
`inline.py`, `queries.py`, `plugin_arrays.py`, and `sink.py`. All Python checks
used `uv run --locked --no-sync` against the existing environments; no package
installation or lock-file change was needed. Each Tiled application used an
isolated in-memory **SQLite metadata catalog**, temporary filesystem storage and
**DuckDB event-table storage**. SQLite metadata is not the same thing as SQLite
event-table storage; the latter was not exercised.

Acquisition checks used a real RunEngine, synthetic signals, and
`ophyd.sim.SynSignal`/`SynSignalWithRegistry`. Window fixtures replayed actual
RunEngine documents with consistently rescaled synthetic timestamps; open
fixtures omitted their stop. Processing documents were composed and validated
by event-model. No imitation Tiled trees or production data were used.

`docker info` reported that it could not connect to the Docker daemon. No
PostgreSQL server executable was found in the checked local installation paths.
The older `~/Code/tiled` checkout was not used.

**Proposed spike/test pins:** Python 3.14, `tiled==0.2.18`,
`bluesky==1.14.6`, `bluesky-tiled-plugins==2.0.2`, `event-model==1.23.1`,
`ophyd==1.11.0`, with the tested D dependencies resolved into the future lock.
Use the **Bluesky callback import** for the compatibility fixtures and the
recommended HDF5 publisher below. This aligns the new project's Tiled pin with
MoDaCor's declared `>=0.2.18,<0.3` requirement. It is a tested prototype
combination, not a claim about all supported Python versions, deployed server
versions, or electrochemistry's future writer. PostgreSQL execution and the
existing MOUSE 0.2.8-client/target-server pairing still need a compatibility gate
before rollout; instrument dependency changes belong in their repositories.

## 1. Layout, descriptors and legacy detector assets

**Confidence: verified by running for scalar layout and simulated external
assets; read in source for descriptor updates and AD-HDF5 normalization.**

MOUSE imports `TiledWriter` from **`bluesky.callbacks.tiled_writer`**. It is
important to specify the import: the installed plugin also contains a separate,
newer writer with different array handling; it is not the class MOUSE uses.

For a configured catalog root `R`, the physical paths are:

| Item | Storage/access |
|---|---|
| Run | `R/{start.uid}`, spec `BlueskyRun` version `3.0` |
| Start document | Run **metadata** under `start`, not a child node |
| Stop document | Run metadata under `stop`; key absent before it is written |
| Stream | `R/{uid}/{stream}`, specs `BlueskyEventStream:3.0` and `composite` |
| Descriptor information | Stream metadata: `uid`, `time`, `data_keys`, `hints`, `configuration` |
| Internal scalar table | `R/{uid}/{stream}/internal` |
| Scalar columns | `seq_num`, `time`, each data key, and `ts_{key}` for each per-field timestamp |
| External array | `R/{uid}/{stream}/{data_key}`, with external data-source registration |

The Python stream client flattens table columns and external arrays into one
namespace: `client[uid][stream][field]`. Use `.base['internal']` on the stream to
access its physical table. There is no physical `start`, `stop`, `descriptors`
or `stream/data` subtree for this writer's v3 layout. Earlier client interfaces
and layouts should not be inferred from their similarly named conveniences.

Measured output, identical in relevant layout on 0.2.8 and 0.2.18:

```text
run metadata keys: start, stop
streams: snapshot, mouse_eiger_measure
measurement physical children: internal
columns: seq_num, time, roi, stats, ts_roi, ts_stats
snapshot events: 1; measurement events: 3
stream client: BlueskyEventStreamV3
```

The writer removes `name`, `object_keys` and `run_start` from the stored stream
metadata. Further descriptors for that stream retain update information under
`_config_updates` (UID, time and configuration), rather than creating descriptor
nodes. This is not a lossless archive of arbitrary descriptor schema changes.
The plugin's `.descriptors` convenience reconstructs documents through run
export; it is not a cheap metadata-only descriptor endpoint. The adapter should
read the stream's stored field/hint/configuration metadata directly for its
selector and avoid exporting a whole run to discover fields.

For ophyd v1 resource/datum assets, the normalizer converts supported resource
specs such as `AD_HDF5_SWMR_STREAM`/`AD_HDF5_SWMR_SLICE` to an HDF5 stream
resource. It combines `root` and `resource_path` into a file URI, translates
resource kwargs `path`/`dataset`, and uses the referencing event and datum
information to associate the data key, descriptor and sequence numbers.
The writer registers an array node directly under the stream. The HTTP reader
then reads that array; it does not need an ophyd filestore handler on the client.
The **server** needs the files mounted at the registered URI and permitted by
its readable-storage configuration. A spec-to-MIME mapping alone cannot repair
an unsupported file layout or missing dataset/shape/dtype information.

A real RunEngine with `SynSignalWithRegistry` produced two external 12×16
float64 images through legacy resource/datum documents; both versions read them
back as an array of shape `(2,12,16)` with exact values. This used simulated NPY
assets, not a live Eiger or AD-HDF5 IOC. Descriptor `dtype_numpy` must match the
actual file dtype; the broad `dtype: array` fallback is not sufficient to assume
an integer detector's machine type.

**Evidence:** `M/bluesky/callbacks/tiled_writer.py:569–651` (run, stop, stream,
table); `:202–240,242–315,398–447` (legacy normalization);
`:660–698` (external array registration);
`M/bluesky_tiled_plugins/clients/bluesky_event_stream.py:351–386` and
`clients/bluesky_run.py:365–371` (client conveniences);
`M/bluesky_tiled_plugins/exporters.py:47–85` (descriptor reconstruction).
Runtime evidence: `probe-0.2.8.log`, `probe-0.2.18.log`, `assets-0.2.8.log`,
`assets-0.2.18.log` in the temporary directory.

## 1a. Processing arrays and the recommended representation

**Confidence: verified by running on both Tiled versions for the cases listed;
production file access, PostgreSQL table storage and xarray compatibility of
the alternative writer remain unverified.**

The three proposed approaches have materially different outcomes:

| Approach | Observed outcome |
|---|---|
| MOUSE's Bluesky writer, inline NumPy vector of length 2,000 | Written as an Arrow list column; exact Arrow HTTP round-trip works |
| Same writer, inline NumPy 1024×1024 or 2048×2048 array | Fails at Arrow conversion: only one-dimensional NumPy array values are accepted there |
| Same writer, 1024×1024 array converted to nested Python lists | Writes a nested Arrow list column and round-trips via Arrow; float32 inputs become double-valued Python lists, about 8.39 MB for this HTTP column response |
| HDF5 arrays registered with native stream resources/datums | Exact typed array round-trip for three 2,000-point fields and a 2048×2048 float32 image |
| MoDaCor TiledSink writing inside a run | Array I/O works, but produces generic nested containers, without stream descriptors/specs or lifecycle |

There is an additional client trap for inline list-valued columns: the default
DataFrame client went through Dask and returned the vector cell as a **string**.
The data was not lost in storage. Both direct Arrow HTTP and
`dask.config.set({'dataframe.convert-string': False})` around the read restored
the vector exactly. Thus a successful event write does not establish that the
normal stream/DataFrame plotting path handles it correctly. Neither inline
table arrays nor subsequent client decimation provide cheap image-region reads.

The separately installed
`bluesky_tiled_plugins.writing.tiled_writer.TiledWriter` **2.0.2** offers another
option: it promotes inline arrays to managed Zarr nodes. With
`max_array_size=0`, both versions round-tripped a 2,000-point vector and a
1024×1024 NumPy image at `run/result/signal`, with shapes `(1,2000)` and
`(1,1024,1024)`. However, this writer hard-codes **two** dimension names
`('time','dim_1')` for both, including the **three-dimensional** image stack.
The test confirmed that inconsistent metadata. This is a promising alternative
after an upstream dimension-label fix and focused xarray/append tests; changing
the import must be an explicit producer decision. Installing the plugin does
not change the old Bluesky callback's behaviour.

**Recommendation for processing schema v1:** use the already verified **HDF5
stream-resource/stream-datum representation for all array products**, retaining
the normal Bluesky run/stream metadata contract. In the successful test:

- `corrected_1d`: descriptor fields `Q`, `I`, `I_uncertainty`, each
  `dtype: array`, explicit `dtype_numpy: '<f4'`, `shape: [2000]`, appropriate
  units and `external: 'STREAM:'`. Real uncertainty component names and their
  meanings must be fixed by the processing contract.
- `corrected_2d`: `image` with `shape: [2048,2048]`, the same explicit external
  declaration and machine dtype, plus agreed axis/units metadata.
- An HDF5 file closed before publication, with datasets shaped `(1,2000)` and
  `(1,2048,2048)`: the leading dimension indexes published results/events.
- One stream resource per field, MIME `application/x-hdf5`, parameters
  `dataset: '/field'`, `swmr: false`, **`join_method: 'stack'`**, and matching
  chunks `(1,2000)` or `(1,256,256)` for this test. Specify stack explicitly;
  the consolidator's concat default has different shape semantics.
- One event per stream providing its time/sequence record, omitting the
  `STREAM:` data keys from the event payload, followed by matching stream datums
  with `indices=[0,1)` and `seq_nums=[1,2)` and the descriptor UID. Start and
  stop are composed by event-model; stop is published after products are ready.

The reader sees ordinary array nodes: `run['corrected_1d']['Q']` of shape
`(1,2000)` and `run['corrected_2d']['image']` of shape `(1,H,W)`. It selects
result index zero explicitly. File mounting, readable-storage permissions,
retention and immutability become producer/server deployment requirements.
HDF5 datasets must be available to the **Tiled server**, not merely MoDaCor.
Add explicit image-axis fields/metadata to the final contract; the experiment
verified pixel values, not a complete scientific axis schema.

PNG is available through the array endpoint, with Pillow installed:

```text
GET /api/v1/array/full/R/UID/corrected_2d/image?format=png&slice=0,:,:
2048×2048 float32: HTTP 200, image/png, 9,780 bytes for this synthetic ramp
same slice, format=application/octet-stream: HTTP 200, 16,777,216 bytes
slice=0,0:64,0:64, octet-stream: HTTP 200, 16,384 bytes
```

Tiled's PNG serializer casts to float32, rescales between the **1st and 99th
percentiles**, clips to `[0,1]` and makes an 8-bit image. It supplies neither
the proposed logarithmic scientific scaling nor a configurable colormap or
robust finite/constant-array policy. The adapter should fetch typed binary
slices, use metadata for shape/dtype, and render its own bounded PNG when those
controls matter. Raw binary is compact numeric data, not automatically compressed
or downsampled. A small HDF5 ROI avoids transferring the full image; storage I/O
still follows its chunk layout. PNG size above is content-dependent, not a
performance or compression guarantee.

**Evidence:** `M/bluesky/callbacks/tiled_writer.py:526–549,639–655` (inline
Arrow path); `M/tiled/client/dataframe.py:168–190,344–357` (Dask read);
`M/bluesky_tiled_plugins/writing/tiled_writer.py:56–61,676–719,874–881`
(new writer promotion and dimensions);
`M/bluesky/consolidators.py:104–136,148–158,244–275,355–375` (external shapes,
dtype, chunks and HDF5 parameters); `M/event_model/__init__.py:2018–2046,2089–2124,2375–2398`
(valid composition);
`MC/io/tiled/tiled_sink.py:60–104,116–145` (generic exports).
The actual sink test wrote `/exports/sample/I/signal` successfully, but
`exports` had no stream specs or `data_keys`.
`M/tiled/serialization/array.py:18–31,64–105` and the same lines in D document
binary and PNG serialization; `M/tiled/server/router.py:602–658` applies array
slices before serialization. Runtime evidence: the `assets`, `inline`,
`plugin-arrays` logs for both versions and `sink.log`.

## 1b. Exact provenance lookup and missing processing

**Confidence: read in source and compiled for PostgreSQL, not executed there;
SQLite query behaviour verified by running.**

Do **not** implement exact UID membership as
`Contains('start.derived_from', uid)` in these SQL catalogs. Both versions cast
the JSON value to a string and apply SQL substring `LIKE`, not JSON-array
membership. `Contains(..., [uid])` raises `KeyError: <class 'list'>` because its
type conversion map accepts only int/float/string/bool.

On **PostgreSQL**, use the installed translator's JSONB containment path:

```python
catalog.search(Eq('start.derived_from', [raw_uid]))
```

Its compiled predicate is:

```sql
nodes.metadata @> :value::JSONB
-- value = {"start": {"derived_from": [raw_uid]}}
```

With the contract's list-valued field, this is the appropriate exact containment
form, including results with several measured parents. Note that PostgreSQL's
`Eq` translator here uses **containment**, not strict equality of the complete
JSON array. On **SQLite**, this list-valued `Eq` fails at type conversion; the
test confirmed it. Keep the backend-specific query choice inside the one layout/
access module. SQLite tests can fetch bounded candidate processing metadata and
perform exact list membership in Python; a true SQLite JSON-array membership
query would need upstream support. Do not make substring matching the contract.

There is no server-side cross-run anti-join in the inspected generic Tiled
search protocol. To find measurements lacking processing, fetch fully paged
measurement candidates and relevant processing metadata, then compute the
UID set difference in the adapter/watcher. On PostgreSQL, the `In` translator
can batch candidate one-element lists, e.g.
`In('start.derived_from', [[uid1], [uid2]])`, into OR-ed JSONB containment
predicates. Bound each batch/request size. This batching formulation is source
analysis, not a measured PostgreSQL query.

Do not apply the raw measurement's time window to the processing search: a
correction can arrive later. Define whether the question means no attempt,
no completed attempt, or no **usable** success; they require different sets.
Missing results alone still do not establish processing eligibility.

Changing `derived_from` to a scalar would simplify scalar equality on SQLite,
but lose the agreed multi-input contract. There is no measured evidence here
that it would improve PostgreSQL performance enough to justify that change:
the list-containing predicate uses the same metadata `@>` family targeted by
the catalog's GIN index. Keep the list, validate its type, and verify an actual
PostgreSQL execution plan before making performance claims.

**Evidence:** `M/tiled/catalog/adapter.py:1544–1558,1570–1586,1606–1609,1701–1726`
and `D/tiled/catalog/adapter.py:2288–2304,2324–2327,2419–2444`;
`M/tiled/catalog/orm.py:97–111` (GIN index declaration). `queries.py` compiled
both versions' actual translators, including the JSONB parameter above and
the substring `LIKE`; `probe.py` confirmed the SQLite list-query failures.
PostgreSQL server configuration, index availability, results and timings remain
unverified.

## 2. Window searches, paging and query support

**Confidence: verified by running on SQLite; read in source and SQL-compiled
for PostgreSQL. No PostgreSQL integration pass is claimed.**

The individual range and presence queries exist, but the plan's four-search
union has a boundary omission. For window `[100,200)`, a run starting at 50 and
stopping at **200** satisfies the plan's stated overlap predicate, but none of
searches 1–3 includes it. Search 3 requires `stop > to` when it needs at least
`stop >= to`. The test reproduced the miss on both versions:

```text
10 real-RE-derived interval fixtures
original union: 5 matches; missing stop_to
corrected closed-overlap + open-horizon union: 6 matches, exact expected set
paging: 4 pages, 10 entries, 10 distinct IDs
page[limit]=301: HTTP 422
```

Prefer the simpler two-query union, chaining each query's terms with `.search()`:

```python
closed = catalog.search(Comparison('lt', 'start.time', to)).search(
    Comparison('ge', 'stop.time', from_)
)
opened = catalog.search(KeyPresent('stop', exists=False)).search(
    Comparison('lt', 'start.time', to)
).search(Comparison('ge', 'start.time', horizon_start))
```

Deduplicate by source/root/container identity. This preserves the plan's
`stop >= from` convention; if the intended run intervals are also half-open,
change that comparison to `>` and update boundary tests explicitly. A missing
stop does not match the numeric closed query for well-formed documents. Validate
times, report malformed metadata, and define the horizon anchor independently
of the live polling clock so historical browsing remains reproducible.

`KeyPresent('stop', exists=False)` is the absence test, not `Eq('stop', None)`.
PostgreSQL compiles it as `(metadata #> ARRAY['stop']) IS NULL`; SQLite uses
`metadata -> '$.stop'`. JSON `null` is present JSON, so missing, explicit null,
an empty object and a valid stop must not be casually treated as interchangeable.

`bluesky_tiled_plugins.queries.TimeRange` exists in **2.0.2**.
`CatalogOfBlueskyRuns.search(TimeRange(...))` decomposes it into
`Comparison('ge','start.time',since)` and
`Comparison('lt','start.time',until)`. It filters **start times**, not overlapping
intervals or missing stops. Passing this convenience to a plain Container does
not perform that client-side translation. Its wrapper also uses truthiness
checks for bounds, so use explicit `Comparison` queries when zero is meaningful.

| Query on nested metadata | PostgreSQL translation in both versions | SQLite observation/semantics |
|---|---|---|
| `Eq` | JSONB `@>` with nested object | Scalar equality works; list value raises KeyError |
| `In` | OR-ed JSONB containment predicates | Scalar membership works; list-valued candidates unsupported |
| `NotIn` | Negation of those containment predicates | Scalar exclusion works; missing-key behaviour differs from PostgreSQL |
| `Comparison` | Nested JSONB comparison with numeric JSONB bound | Numeric range comparisons work |
| `Regex` | Not registered for this catalog adapter | HTTP 400, unsupported query |
| `Contains` | Cast-to-text substring `LIKE` | Substring matching, not array membership; list RHS fails |
| `FullText` | Whole-metadata `jsonb_to_tsvector` / `to_tsquery` | Whole-metadata FTS5 query works |

`FullText` has no nested-key parameter and is not an exact UID lookup. `NotIn`
also deserves an explicit missing-field test: SQLite's typed extraction followed
by SQL `NOT IN` excludes SQL NULLs, while PostgreSQL negates whole-object JSONB
containment and can include nodes lacking the field. Keep metadata types
consistent; successful JSONB comparisons alone do not validate timestamp types.

Server-side filtering also does not prove efficient index use. The compiled
time predicate compares extracted JSONB values; it is not a demonstrated
indexed numeric timestamp column. Check PostgreSQL query plans and latency on
a representative catalog before treating a short time window as a bounded
database-work guarantee.

Both versions support offset/limit paging and next links. Normal `.items()`
iteration follows pages and returns clients constructed from each returned item;
reading their already-returned metadata avoids a separate request per run.
Do not enumerate keys and fetch every run individually. Paging is not an
atomic snapshot of a concurrently changing catalog; deduplicate, use stable
ordering and repeat/reconcile polls as needed. No start/stop search automatically
walks nested instrument roots: search each configured runs container.

**Evidence:** `M/tiled/catalog/adapter.py:1570–1609,1612–1625,1684–1779`;
corresponding D implementations at `:2288–2343,2402–2497` have the same relevant
query semantics. `M/tiled/queries.py:377–407` defines key presence;
`M/bluesky_tiled_plugins/queries.py:246–313` and
`clients/catalog_of_bluesky_runs.py:171–185` define TimeRange translation.
`M/tiled/client/container.py:479–544` handles paged items.
Runtime evidence: `queries-0.2.8.log`, `queries-0.2.18.log`, both probe logs.

## 3. HTTP tables, column selection and search metadata

**Confidence: verified by running on both versions for the named table formats,
column selection, search paging and limits; remaining formats/parameters read
in source.**

For the scalar table, use these paths under `/api/v1`, with the configured root
included if nonempty:

```text
GET /table/full/R/UID/STREAM/internal?column=time&column=roi&format=arrow
GET /table/partition/R/UID/STREAM/internal?partition=0&column=time&format=json
GET /search/R?page[limit]=300&fields=metadata
GET /metadata/R/UID/STREAM
```

`column` is repeatable; it is not the adapter's proposed `fields` parameter.
Table `format=csv`, `json`, `arrow`, and the full
`application/vnd.apache.arrow.file` MIME value all returned HTTP 200 in the
probe. Arrow is an **IPC file** representation. JSON is a column-to-values
mapping such as `{'time': [...], 'roi': [...]}`, not an array of row objects.
CSV includes a header by default. Other registered table formats include
`application/json-seq`, Parquet and optional Excel/HDF5; dependency availability
and data types still matter. A response-byte limit is enforced (default
300,000,000 bytes in both inspected settings), after reading the requested data.

The table routes select columns, but have no public row-offset/row-limit or
server-side min/max/count API in these versions. A `slice` query string on the
table route does not establish row pushdown. Array routes have slicing, but
getting a SQL table column as an ArrayAdapter can already read the full column
on the server; see question 4. Do not promise “sliced before transfer” for scalar
table rows without qualifying that limitation.

Search defaults to **100** entries per page, maximum **300**, in both versions.
The response includes `data[*].attributes.metadata`, so start/stop documents
can be returned with a page of clip candidates in **one** request. Use
`fields=metadata` and optionally `select_metadata` to reduce metadata payloads;
these are projections of metadata, not array/table slicing. Preserve the
returned pagination links/parameters and keep fetching until `links.next` is
null. An arbitrary window can require several pages and several filtered searches.
The probe requested one item with metadata and confirmed a next link and both
start/stop keys for a finished run.

**Differences:** 0.2.18 moves pagination parsing into
`server/dependencies.py` and adds more robust scalar/NA/bytes conversion in the
table JSON serializer. Its SQL column adapter also adds regular/ragged array
handling; that does not remove the upstream full-column read or make NumPy 2D
event values acceptable to the old writer's Arrow constructor.

**Evidence:** `M/tiled/server/router.py:299–326,749–814,910–1028`;
`D/tiled/server/router.py:322–338,1077–1142,1238–1360`;
`M/tiled/server/core.py:65–66`, `D/tiled/server/dependencies.py:29–30`;
`M/tiled/serialization/table.py:17–41,57–83,109–142`;
`D/tiled/serialization/table.py:113–153`;
both `tiled/server/settings.py:40`. Runtime evidence: both probe logs and the
four-page checks in both query logs.

## 4. Cheapest stream extent/count access

**Confidence: read in source; recorded counts and column reads verified by
running. No large-table benchmark or PostgreSQL aggregate implementation tested.**

For a closed run, the cheapest recorded event count is
`run.metadata['stop']['num_events'][stream]`: it is already in the run metadata.
The real-RE test yielded snapshot=1 and measurement=3. It is a producer-reported
document count, not an independent verification of stored rows, readable frames,
or usable corrected products. If it is absent, report unknown or count the
selected table explicitly. An external array's leading dimension can count
published rows under a known representation, but generic frame, event and
document counts are not interchangeable.

There is no cheap first/last **event** timestamp in the inspected scalar table's
metadata. Stream descriptor time and run start/stop time are different quantities.
`TableStructure` contains schema, columns, partitions and resizability, not a
row count or time extrema. The old SQLAdapter constructs a column ArrayAdapter
by calling `self.read([key])`; this loads the column even if the eventual HTTP
array slice asks only for its endpoints. 0.2.18 retains the full-column read
before its new regular/ragged-array handling. Writer batch size does not create
one partition per batch: its appends target partition zero.

For v1, read only the `time` column for the **selected run**, compute count and
min/max in the adapter, and cache under the finality contract. This avoids all
other fields and arrays, but remains O(number of events). Use min/max rather
than assuming the SQL table's returned row order defines chronological extrema;
the old read query orders partitions, not event times within each partition.
The `time` column is the event document's time; it is not automatically every
detector frame's or flyer's independent acquisition clock. Preserve that
distinction when selecting future instrument-specific time axes.
If multi-day flyer runs make that too expensive, request an upstream aggregate/
bounded-row API or producer-supplied indexed summary. A client's `max_points`
cap alone does not bound upstream work. Do not query the catalog database or
its private SQL table identifiers from adapter endpoints.

**Evidence:** `M/bluesky/callbacks/tiled_writer.py:544–549,579–605`;
`M/tiled/structures/table.py:20–33`;
`M/tiled/adapters/sql.py:234–247,311–348,362–394`;
`D/tiled/adapters/sql.py:370–399` (full read persists).
The table GET signatures cited in question 3 contain no aggregate or row-range
parameters. Runtime evidence: `fresh_stop` and layout checks in both probe logs.

## 5. Open runs, visibility and streaming

**Confidence: verified by running for polling visibility; streaming capability
and requirements read in source, not exercised end to end.**

The start creates a run immediately with no `stop` key. A descriptor creates
the stream; before the first flushed event it need not have an `internal` table.
Events append to the table; stop flushes remaining internal/external caches and
then updates run metadata. Intermediate states are observable and the run is
not a cross-node transactional snapshot.

Fresh-client reads immediately after the synchronous writer call returned:

```text
batch_size=1: rows after successive events = [1,2,3], stop absent
batch_size=3: rows after successive events = [0,0,3], stop absent
KeyPresent('stop', exists=False) found each open run
```

This establishes local read-after-write behaviour, not a fixed production
latency. MOUSE wraps the callback in `BufferingWrapper`, adding asynchronous
queue delay; network/server latency and client/proxy caching add further delay.
Use fresh/revalidated metadata when polling. A missing stop remains `open`,
with activity/source health separate, as the current plan specifies.

Tiled does have `/api/v1/stream/single/{path}` WebSockets and client table
subscription support. It needs a configured streaming cache. The inspected
backends include process-local memory and Redis; process-local memory is not
a shared notification bus for multiple server workers. The cache is disabled
when not configured. Metadata changes, table appends and array updates have
publication hooks, but a subscription is to a node, not automatically a complete
run-discovery/finished-run-overlap query. Correct connection auth, reconnect,
retention and reconciliation still need an integration experiment.

Use polling first. Streaming can later nudge the same reconciliation path;
it does not prove that an instrument is alive or replace historical searches.
0.2.18 improves cache backend selection and Redis Sentinel support, but neither
environment's default non-streaming fixture demonstrated deployed WebSockets.

**Evidence:** `M/bluesky/callbacks/tiled_writer.py:569–651,707–727`;
MOUSE `qserver_profile/profile_mouse/startup/02_tiledwriter.py:27–30`;
`M/bluesky/callbacks/buffer.py:42–84`;
`M/tiled/catalog/adapter.py:242–248,1198–1205,1430–1455`;
`M/tiled/server/router.py:702–746`;
`M/tiled/client/dataframe.py:310–342`;
`M/tiled/server/streaming.py:101–123,307–349`;
`D/tiled/catalog/adapter.py:284–290` and `D/tiled/server/streaming.py:21–61`.
Runtime evidence: both asset logs' `open_visibility` checks.

## 6. Instrument identity and subcontainers

**Confidence: read in source; arbitrary supplied instrument metadata was
preserved in both runtime fixtures. Per-instrument subcontainer writing was not
separately executed.**

The writer records supplied start metadata. It does not derive an authoritative
instrument identity from the producer's host, QueueServer, detector name or
stream name. Preserve the agreed `facility`, `instrument`, optional `station`
keys and copy them into processing runs. Stream names alone are not reliable
cross-instrument identity.

`TiledWriter(client_for_instrument_subcontainer)` can write runs below that
container: it creates each `{uid}` relative to its supplied client. The client
must have the relevant create/write permissions; a root can be provisioned
upstream, and `Container.create_container` can create one where allowed. The
viewer itself does not create containers. Configure roots explicitly and search
each one, because generic root searches inspect direct children, not all nested
runs recursively. A known configured root can supply a labelled legacy fallback;
it cannot establish missing metadata when both instruments write to one root.

**Evidence:** `M/bluesky/callbacks/tiled_writer.py:512–514,569–576,770–790`
(supplied client and relative run creation);
`M/tiled/client/container.py:806–832` (container creation);
MOUSE startup `02_tiledwriter.py:26–30` supplies its connected root without
subcontainer selection. Runtime evidence: both probe logs' nested instrument
equality searches found the synthetic run.

## 7. Authentication and read-only credentials

**Confidence: read in source; scoped-key enforcement was not tested against a
multi-user server and production policy was not inspected.**

The native client accepts `api_key=` and sends:

```text
Authorization: Apikey <secret>
```

The server also accepts `api_key` as a query parameter and an API-key cookie.
Use the header from the adapter, never the URL/browser option. API keys are
not Bearer tokens; their authentication schemes are distinct.

On a **multi-user authenticated server**, create a key with explicit scopes
`['read:metadata','read:data']`, without `inherit`, create/write/register/delete
scopes or unnecessary administration rights. The creator must be allowed those
scopes, and effective rights are intersected with the principal's current
permissions. Access tags/policies separately determine which data can be read.

On a **single-user server**, its static shared key is not a separately
scopable read-only credential. In 0.2.8 it receives `USER_SCOPES`, including
write/create/delete/register; in 0.2.18 the equivalent is named
`SINGLE_USER_SCOPES` and still includes those rights. The client refuses to
mint additional scoped keys in single-user mode. Therefore an environment
variable containing a Tiled key is not sufficient evidence that production
access is read-only. Confirm the central server's auth mode and issue a scoped
service credential there; otherwise a separately enforced read-only gateway/
policy would be required. The disposable fixture's generated single-user
credential was used only for its own writes and was not printed.

**Evidence:** `M/tiled/client/context.py:499–513,538–570`;
`M/tiled/server/authentication.py:103–125,182–200,332–366,998–1011`;
`M/tiled/access_control/scopes.py:28–41`;
`D/tiled/client/context.py:814–826,851–883`,
`D/tiled/server/authentication.py:347–356,1163–1176` and
`D/tiled/access_control/scopes.py:30–45`.

## Corrections to carry into the plan

1. **§5.4:** replace the four searches with completed-overlap plus
   missing-stop/horizon searches, or fix the spanning upper-edge comparison.
   Add the reproduced `stop == to` fixture and document the lower-edge and
   historical horizon conventions. Keep the current 21-day horizon configurable.
2. **§§4, 9 and processing-runs.md:** choose the tested HDF5 stream-resource
   representation, leading result dimension, explicit dtype, stack semantics,
   units/axes/uncertainties and server-side file-access requirements. Specify
   the writer import. Document the separate plugin writer and its dimension
   metadata defect as an upstream alternative, not an automatic replacement.
3. **§§5.4, 6.1:** use PostgreSQL JSONB containment for exact `derived_from`
   lookup; avoid `Contains` substring semantics. Define the SQLite fallback
   and adapter-side anti-join, with related results independent of raw-run time.
4. **§6.1 and caching/load expectations:** qualify scalar row slicing and
   decimation. Selected columns are pushed down; arbitrary row limits and time
   extrema are not provided cheaply by the inspected SQL table routes. Start
   with selected-run time-column reads and recorded stop counts.
5. **§6 images:** Tiled's built-in PNG is an 8-bit percentile-scaled preview.
   Keep adapter rendering for log scale, colormap and finite/constant handling,
   using typed array slices rather than JSON images.
6. **§§6, 9, 10:** pin the source-tested combination, keep scoped multi-user
   read-only credentials explicit, and leave the PostgreSQL data gate open.
   SQLite execution and PostgreSQL SQL compilation do not satisfy the required
   PostgreSQL integration tests. Add actual server/index/result checks and a
   0.2.8-writer-client to target-server compatibility check before rollout.

No production service, acquisition hardware, queue or real catalog was
contacted. No implementation, plan, contract, instruction or dependency file
was changed by this review.
