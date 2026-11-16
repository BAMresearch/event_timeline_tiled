# event-timeline-tiled

A read-only timeline of Bluesky runs stored in Tiled, shown as a Grafana
dashboard laid out like a video editor: the timeline of runs at the bottom, a
viewer for the selected run top right, run details and channels top left.
First users are operators following the queue: what is running where, and
with what status.

Status: prototype. Step 2 of the [plan](docs/design/plan.md) (scaffold and
demo stack) is in place; the timeline itself comes in step 3.

```text
browser ──► proxy (nginx) ──► Grafana ──► Infinity data source ──► adapter ──► Tiled
                └── /run-images/ (signed-in users only) ──────────────► adapter
```

- **Adapter** (this Python package): reads runs from Tiled with a read-only
  key and serves flat tables to Grafana. It never writes to Tiled.
- **Grafana**: dashboards and data source provisioned from `grafana/`;
  pinned plugins, nothing downloaded at start-up.
- **Demo stack**: a disposable multi-user Tiled on PostgreSQL with synthetic
  MOUSE-like runs, so everything runs without the lab network.

## Try the demo stack

```sh
cp deploy/.env.example deploy/.env      # replace every "change-me"
docker compose -f deploy/compose.yaml --profile demo up -d --build
```

Open <http://localhost:8080> and sign in as `admin` with `GF_ADMIN_PASSWORD`.
The **Timeline health** dashboard shows whether the adapter can read Tiled.
Stop with `docker compose -f deploy/compose.yaml --profile demo down` (add `-v`
to discard the demo data). See [the demo stack](docs/technical/demo-stack.md).

## Development

```sh
uv sync --locked --group dev --extra testing
uv run --locked pytest
uv run --locked ruff check . && uv run --locked ruff format --check .
uv run --locked mypy src
```

See [development](docs/technical/development.md) and [AGENTS.md](AGENTS.md).

## Licence

BSD-3-Clause, see [LICENSE.md](LICENSE.md).
