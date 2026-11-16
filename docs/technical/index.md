# Technical overview

```text
browser ──► proxy (nginx) ──► Grafana ──► Infinity data source ──► adapter ──► Tiled
                └── /run-images/ (signed-in users only) ──────────────► adapter
```

| Part | Where | Role |
|---|---|---|
| Adapter | `src/event_timeline_tiled/adapter/` | FastAPI service; reads Tiled with a read-only key, serves flat tables |
| Tiled access | `src/event_timeline_tiled/tiled_http.py` | The only module that talks to Tiled (grows into the layout module in step 3) |
| Access check | `src/event_timeline_tiled/access_check.py` | `ett-check-access`: read-only check of the adapter's key ([Tiled access](tiled-access.md)) |
| Settings | `src/event_timeline_tiled/settings.py` | `ETT_*` environment variables, validated at start-up |
| Fixtures | `src/event_timeline_tiled/testing/` | Synthetic runs for tests and the demo stack (`testing` extra) |
| Grafana | `grafana/` | Provisioned data source and dashboards |
| Containers | `deploy/` | Compose file, Dockerfiles, proxy and demo-Tiled configuration |

Design background: [plan](../design/plan.md),
[decisions](../design/decisions.md),
[processing-run contract](../design/processing-runs.md),
[spike results](../design/spike-results.md).

## Tested stack

Pinned through `[tool.uv] constraint-dependencies` in `pyproject.toml` and the
image digests in `deploy/`:

| Component | Version |
|---|---|
| Python | 3.14 |
| Tiled | 0.2.21 |
| Bluesky / bluesky-tiled-plugins / event-model / ophyd | 1.14.6 / 2.0.2 / 1.23.1 / 1.11.0 |
| Grafana OSS | 13.3.1 |
| Infinity / Business Variable | 4.1.6 / 5.2.0 |
| PostgreSQL (demo) | 16 |
| nginx | 1.27 |

Raise a pin deliberately, with the tests passing; see
[development](development.md).
