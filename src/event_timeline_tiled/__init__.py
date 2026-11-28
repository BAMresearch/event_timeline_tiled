"""Read-only timeline of Bluesky runs in Tiled, shown as a Grafana dashboard.

The adapter (:mod:`event_timeline_tiled.adapter`) reads runs from Tiled with a
read-only credential and serves flat tables to Grafana. The
:mod:`event_timeline_tiled.testing` package writes synthetic runs into a
disposable Tiled for tests and the demo stack.
"""

__version__ = "0.1.1"  # set by semantic-release in the release PR
