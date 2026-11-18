# Development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
uv sync --locked --group dev --extra testing
```

| Task | Command |
|---|---|
| Tests, lint, types | see [Testing](testing.md) |
| Strict docs build | `uv run --locked mkdocs build --strict` |
| Distributions | `uv build` |
| Demo stack | see [Demo stack](demo-stack.md) |

The development Python is 3.14 (`.python-version`), which is also what the
package declares; widen `requires-python` together with the CI matrix.

## Pins

The Tiled/Bluesky stack is pinned in `[tool.uv] constraint-dependencies`;
container images by digest in `deploy/`. To raise one: change it, run
`uv lock` (with `--upgrade-package` for transitive dependencies), run the full
test suite and the demo stack, and commit `pyproject.toml` and `uv.lock`
together. Record the reason in [decisions](../design/decisions.md).

Commits follow Conventional Commits. Keep the changelog's
`<!-- version list -->` marker; do not bump versions by hand.

## Dashboards

`grafana/dashboards/timeline.json` and `overview.json` are generated: edit
`timeline_dashboard.py` or `overview_dashboard.py` in `grafana/generator/`
(their rows, positions relative to the row), the shared panels in
`timeline_panels.py` (each takes its position and the dashboard's links), or
the building blocks in `dash_parts.py`, then run

```sh
uv run --locked python grafana/generator
```

and commit them. A test fails if a JSON file differs from the generator's
output. Grafana provisions only the JSON, which stays declarative.
`health.json` is still written by hand.
