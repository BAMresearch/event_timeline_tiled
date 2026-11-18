# Technical overview

```text
browser ──► proxy (nginx) ──► Grafana ──► Infinity data source ──► adapter ──► Tiled
                └── /run-images/ (signed-in users only) ──────────────► adapter
```

| Part | Where | Role |
|---|---|---|
| Adapter | `src/event_timeline_tiled/adapter/` | FastAPI service; reads Tiled with a read-only key, serves flat tables |
| Endpoints | `src/event_timeline_tiled/adapter/` | The [endpoint contract](endpoints.md): `overview.py` (health, queues, the window's runs), `runs.py` (one run), `trends.py`, `results.py` (processing results), `selection.py` (the dashboard's selection), each with its own caches; `state.py` holds what they share and `app.py` wires them |
| Processing results | `src/event_timeline_tiled/related.py`, `images.py` | Latest attempt and usable result per family; PNG rendering |
| Trend | `src/event_timeline_tiled/trend.py` | Runs joined into one series, with gaps |
| Timeline | `src/event_timeline_tiled/timeline.py` | Window searches per root, activity of open runs, stale data, source state |
| Clips | `src/event_timeline_tiled/clips.py` | Lane keys and names, sub-lanes, the Grafana frame |
| Queue status | `src/event_timeline_tiled/queues.py` | Read-only RE Manager status ([queue status](queues.md)) |
| Run state | `src/event_timeline_tiled/run_state.py` | Status, role and activity, defined once |
| Presets | `src/event_timeline_tiled/presets/` | Which runs, which lanes, which labels: the YAML subset, validation, predicates, templates, the registry ([presets](presets.md)) |
| View table | `src/event_timeline_tiled/views.py` | The only instrument knowledge: views, labels, stall thresholds |
| Layout | `src/event_timeline_tiled/layout.py` | The Bluesky-in-Tiled layout and query choices |
| Tiled access | `src/event_timeline_tiled/tiled_http.py` | The only module that sends requests to Tiled: paging, errors, health |
| Access check | `src/event_timeline_tiled/access_check.py` | `ett-check-access`: read-only check of the adapter's key ([Tiled access](tiled-access.md)) |
| Settings | `src/event_timeline_tiled/settings.py` | `ETT_*` environment variables, validated at start-up |
| Fixtures | `src/event_timeline_tiled/testing/` | Synthetic runs for tests and the demo stack (`testing` extra) |
| Grafana | `grafana/` | Provisioned data source and dashboards; `grafana/generator/` builds `timeline.json`, one function per row ([development](development.md#dashboards)) |
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
