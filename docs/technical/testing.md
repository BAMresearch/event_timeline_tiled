# Testing

```sh
uv run --locked pytest                 # everything (about 10 s)
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
| `tests/unit/` | Settings, the Tiled reader's status mapping (stubbed responses), Grafana provisioning and dashboards |
| `tests/contract/` | The adapter's `/health`, the access check's data reads and the fixture layout against a real Tiled |

## Stack smoke test

With the demo stack running (see [demo stack](demo-stack.md)):

```sh
uv run --locked python deploy/smoke_test.py
```

It checks, through the published proxy: anonymous API and image requests are
refused; the provisioned health query returns `ok` through Grafana, Infinity
and the adapter; `ett-check-access` passes inside the adapter container for a
seeded run; and the adapter's key is refused a write. CI runs it on every
push with generated secrets and removes the stack afterwards.

PostgreSQL contract tests (required in CI) arrive in step 3 with the window
search; until then the demo stack runs Tiled on PostgreSQL.
