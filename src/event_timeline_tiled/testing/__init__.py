"""Synthetic runs for tests and the demo stack (needs the ``testing`` extra).

Acquisition runs are produced by a real ``RunEngine`` with ``ophyd.sim``
signals, then retimed onto chosen intervals; processing runs are composed with
``event_model`` and carry HDF5 arrays registered through stream resources
(``docs/design/processing-runs.md``). Everything is written through
``bluesky.callbacks.tiled_writer.TiledWriter`` into a Tiled server that the
caller owns and may discard. Never point this at a production server.
"""

from .fixtures import FixtureWriter, ProcessingOutputs, window_scenario

__all__ = ["FixtureWriter", "ProcessingOutputs", "window_scenario"]
