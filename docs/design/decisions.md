# Decisions

Short records of decisions made while building; the plan's §14 holds the
maintainer's decisions from the design phase. Newest first.

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
  included, not with the root's runs. Fine for MOUSE for years; on a much
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

## D-004 — Docker only for now (2026-11-16)

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
