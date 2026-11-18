# Decisions

Short records of decisions made while building; the plan's §14 holds the
maintainer's decisions from the design phase. Newest first.

## D-011 — Docker or rootless Podman (2026-11-18; replaces D-004)

The lab runs rootless Podman 4.6 on Ubuntu 24.04, with netavark (so
aardvark-dns, not Docker's `127.0.0.11`) and no `podman compose`. The proxy
now takes its resolver from the container's `/etc/resolv.conf` through the
nginx image's own entrypoint (`NGINX_ENTRYPOINT_LOCAL_RESOLVERS`, nginx
1.27.5), names are still resolved at request time, and images are named in
full. The supported way to run the stack under Podman is Docker's compose
tool against Podman's Docker-compatible socket, which reads the compose file
as written; CI runs it with Podman 4.9.3 (Ubuntu 24.04's package, which the
lab is asked to install). `podman-compose` is not tested.

## D-010 — The Timeline dashboard is generated, the JSON committed (2026-11-17)

`timeline.json` had grown to 25 panels (2,600 lines) built by a script kept
outside the repository. Grafana's file provisioning has no includes, and
library panels cannot be provisioned from files, so the JSON cannot be split
for Grafana. The generator now lives in `grafana/generator/`, one function
per row with positions relative to the row, and writes the JSON that Grafana
provisions; the JSON stays committed and declarative, and a test fails if it
differs from the generator's output. The first generated file equals the
earlier one as data (only the key order of one row changed).

## D-009 — Corrected data in a row of fixed panels (2026-11-17)

Plan §8 left open how the dashboard switches between view kinds (table,
time series, curve, image): an Auto grid viewer with show/hide rules, or
detail dashboards per kind. Show/hide rules need dynamic dashboards (schema
v2), whose file provisioning is still experimental in Grafana 13.3.1, and
detail dashboards would cost a navigation per selection. Instead each kind
has fixed panels: the curve and the image sit in a *Corrected data* row,
always present. For a run without them they are empty, and a notice line
says why (no processing run, no usable result, the latest attempt failed).
The row can be collapsed. Selecting a measurement shows its latest usable
processing result there directly. Revisit in step 8 with dynamic dashboards.

Correction (2026-11-18, Codex review 07, finding 14): in Grafana 13.3.1 the
new dashboard layouts are generally available and can be file-provisioned in
the Kubernetes-style resource format; they are not experimental, as this
decision assumed. The fixed panels stay, for the static layouts the
maintainer chose ([presets.md](presets.md) §11); a dynamic variant remains
possible later.

## D-008 — Processing runs found with `Like`, checked exactly (2026-11-17, revised after review 06)

Processing-runs.md §1.2 finds a measurement's processing runs with
`Eq('start.derived_from', [uid])`, JSONB containment on PostgreSQL. On
SQLite, Tiled 0.2.21 binds the list as an SQL value, which the driver
refuses: the search fails with a server error. The adapter does not know
which catalogue a server uses. It therefore searches with
`Like('start.derived_from', '%"<uid>"%')` (the UID quoted as JSON, so a
UID that is part of another does not match), which works on both and in
Tiled 0.2.7 (the lab server's version, source checked), and keeps only runs
whose `derived_from` list holds the UID exactly. The cost: on PostgreSQL the
search cannot use the metadata GIN index and filters the root's runs, as
the window searches already do (spike: 2.5 ms for 5,000 runs). Never
`Contains`, which matches substrings without the quotes.

Review 06 (finding 5) showed this is lossless only for some UIDs: `_` is a
`LIKE` wildcard (false candidates can use up the cap), and non-ASCII
characters and backslashes are serialised differently by JSON and the
databases (a run with `derived_from: ["párent"]` was not found on SQLite).
So `Like` is used only for UIDs of letters, digits, `.`, `~` and `-` (all
UUIDs), for which the candidates are exactly the matches. Any other UID is
looked up by scanning the runs that have `start.derived_from` (at most
10,000, reported as truncated beyond), checked exactly in the adapter.

## D-007 — Timeline refresh stays at 30 s until the operator trial (2026-11-16)

Plan §1 asks for a 5–10 s refresh. Each refresh of the Timeline dashboard
runs four window requests (*Runs in range*, *Failed and aborted*, *Data
status*, the timeline), each two searches per catalogue root, plus at most
one activity read per open run per minute and the selected run's reads.
On the demo stack (D-006) a window costs about 65 ms, so 10 s would be
fine there, but the lab Tiled's cost is not measured yet. The default stays
30 s, with 10 s in the refresh picker; step 4 measures the lab window queries
and event-column reads per viewer and then sets the default.

## D-006 — Window search and old-client checks on Tiled 0.2.21 (2026-11-16)

Measured on the demo stack (Tiled 0.2.21, PostgreSQL 16, multi-user), with
5,000 run entries under one container (6,565 nodes in the catalogue):

- A 12-hour window (both searches, through the adapter's layout module):
  median 65 ms, maximum 78 ms over ten windows; 87 hits.
- Closed runs: Tiled orders by `nodes.id` with a `LIMIT`, so PostgreSQL walks
  the primary key over **all nodes of all parents** and filters (3.6 ms,
  6,523 rows removed). Open runs (`KeyPresent` compiles to
  `metadata #> '{stop}' IS NULL`) use the parent index (1.0 ms).
- So the closed-run search grows with the whole catalogue, streams and tables
  included, not with the root's runs. This is one measured scale point, not
  proof that time filtering is indexed at any catalogue size. Fine for MOUSE for years; on a much
  larger catalogue, an index on `(parent, (metadata #> '{start,time}'))` or an
  upstream change to the ordering would be the fix. The 0.2.19 index change
  (`ix_nodes_metadata`, GIN `jsonb_path_ops`) does not take part in these
  range queries.

MOUSE's client stack (Tiled 0.2.8, Bluesky 1.14.6) writes measurements, open
runs and processing runs with registered HDF5 arrays into 0.2.21, and the
adapter finds and reads them all (`deploy/old_client_check.py`, also run in
CI). One difference: with the 0.2.8 client, streams whose fields are all
external also get an `internal` table holding only `seq_num` and `time`; the
layout module treats such streams as having no scalar data either way. The
check against the lab server's real version remains a lab-session item.

## D-005 — The access check never writes (2026-11-16)

`ett-check-access` proves a key is read-only from the scopes Tiled reports
for it (`/auth/whoami`, the key's own entry), which is what Tiled enforces,
then proves reading by fetching real data. It does not attempt a write: with
a wrongly privileged key, a valid write request would really create a node on
the lab server (review 04, finding 6).

## D-004 — Docker only for now (2026-11-16; replaced by D-011)

The proxy resolves Grafana and the adapter at request time through Docker's
embedded DNS (`127.0.0.11`). Podman provides DNS differently and is untested,
so the stack claims Docker Compose only until a Podman run is tested
(review 04, finding 9).

## D-003 — Seeder principal has the `admin` role (2026-11-16)

Tiled's default `user` role lacks the `register` scope, which registering
external files (the processing runs' HDF5 arrays) requires. The demo seeder's
service principal therefore has the `admin` role; its API key is still limited
to the scopes it needs. Consequence for the lab server: MoDaCor's publisher
needs a principal with `register` (by default only `admin`), or a custom role.

## D-002 — Tiled pinned at 0.2.21 instead of 0.2.18 (2026-11-16)

Tiled 0.2.18 caps DuckDB below 1.4, and DuckDB publishes Linux wheels for
Python 3.14 only from 1.4.2. With 0.2.18 the demo Tiled image and Linux CI
would have to compile DuckDB. Tiled 0.2.19 and later allow newer DuckDB, so
the pin moved to 0.2.21 (DuckDB 1.5.6), keeping Python 3.14 throughout. The
spike's data gate ran on 0.2.18; it was re-run on the demo stack (0.2.21,
PostgreSQL, multi-user, reads with the read-only key): 9 of 9 checks pass —
window union, paging, `derived_from` containment and batching, HDF5 arrays
and binary slices. 0.2.21 also requires `expires_in` when creating an API key.

Other changes between 0.2.18 and 0.2.21 that matter here (review 04,
finding 11; release notes and sources checked by Codex):

- 0.2.19: access blobs become access tags, with catalogue migrations;
  the PostgreSQL metadata GIN index is replaced and query-plan settings
  change, so the spike's 0.2.18 query-plan conclusions are re-measured in
  step 3; deep search added (plain `/search` stays shallow, so explicit
  catalogue roots are still right).
- 0.2.20: fixes for exhausted pagination, data-source loading and SQL
  asset/pool handling.
- 0.2.21: a missing registered HDF5 dataset returns `410` (to be reported
  as a missing asset, not as an unavailable source); SQLite JSON comparisons
  fixed, including explicit null versus missing keys (keep `KeyPresent` for
  open runs); PostgreSQL `Eq` still uses JSONB containment.
- MOUSE's 0.2.8 client: 0.2.21 translates its `access_blob` and strips newer
  asset fields for old clients, which is promising but not proof. An
  isolated old-client check is planned for step 3 and the real check for a
  lab session. Upgrading an existing server needs its catalogue migrations.

## D-001 — Package declares Python 3.14 only (2026-11-16)

`requires-python = ">=3.14"`, matching what CI tests. Widen with the matrix.
