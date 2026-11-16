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
