# Changelog

<!-- version list -->

## v0.1.1 (2026-11-28)

### Bug Fixes

- Give each stack its own Grafana session cookie
  ([`a785f29`](https://github.com/BAMresearch/event_timeline_tiled/commit/a785f293b85f5a1d03089a7b16bc49ea2430c954))


## v0.1.0 (2026-11-19)

- Initial Release

First release: a read-only Grafana timeline of Bluesky runs in Tiled.

- Adapter: reads runs from Tiled with a read-only key (paged searches, never
  one run at a time) and serves flat tables to Grafana through Infinity;
  recorded status, inferred activity and source health kept apart; read-only
  queue status from RE Managers (ZeroMQ) and Bluesky HTTP Servers.
- Dashboards: the timeline with lanes and presets, the Overview (selected
  run, its streams and time series, trends of sequential runs, queues), and
  health; presets for MOUSE-like instruments and electrochemistry stations.
- Deployment: the demo stack (Docker or rootless Podman) with a disposable
  multi-user Tiled 0.2.21 on PostgreSQL, and the single-host layout for an
  instrument PC, on loopback only (`deploy/compose.host.yaml`).
