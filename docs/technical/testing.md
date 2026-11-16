# Testing

```sh
uv run --locked pytest                 # everything (about 35 s, starts PostgreSQL)
uv run --locked pytest tests/unit      # fast, no server started
uv run --locked ruff check . && uv run --locked ruff format --check .
uv run --locked mypy src
```

No test contacts a production service or the lab network. Contract tests
start an in-process Tiled (`SimpleTiledServer`, SQLite) and fill it with the
window scenario: MOUSE-like runs written by a real RunEngine through
`TiledWriter`, retimed onto the edge cases of plan §5.4, and processing runs
with HDF5 arrays.

| Directory | What it tests |
|---|---|
| `tests/unit/` | Settings, run state, clips and lanes, the view table, stale data, the Tiled reader's status mapping (stubbed responses), Grafana provisioning and dashboards |
| `tests/contract/` | Window search, paging and run reads (SQLite and PostgreSQL), every endpoint, `/health`, the access check and the fixture layout against a real Tiled |

## Stack smoke test

With the demo stack running (see [demo stack](demo-stack.md)):

```sh
uv run --locked python deploy/smoke_test.py
```

It checks, through the published proxy: anonymous API and image requests are
refused; the provisioned health query returns `ok` through Grafana, Infinity
and the adapter; `ett-check-access` passes inside the adapter container for a
seeded run; the adapter's key is refused a write; and every panel query of
the timeline dashboard returns without error, with clips in the timeline. CI runs it on every
push with generated secrets and removes the stack afterwards.

```sh
uv run --locked python deploy/old_client_check.py
```

writes runs with MOUSE's client stack (Tiled 0.2.8, Bluesky 1.14.6, in a
throwaway environment inside the `demo-seed` image) into the stack's Tiled
and checks that the adapter finds and reads them (decision D-006). CI runs
it after the smoke test.

## PostgreSQL

Search and endpoint contract tests run on both SQLite and PostgreSQL
(fixture `catalog_server`, marker `postgres`). PostgreSQL comes from
`ETT_TEST_POSTGRES_URI` (an admin URL such as
`postgresql://user:password@host:5432/postgres`; each session creates its
own database) or, without it, from a throwaway `postgres:16` container
started through Docker. If neither is available these tests **fail**; leave
them out deliberately with `pytest -m "not postgres"`.

All in-process Tiled servers of a session share one API key: Tiled 0.2.21's
`build_app()` stores the single-user key in process-wide settings, so a
second server would otherwise lock out the first.
