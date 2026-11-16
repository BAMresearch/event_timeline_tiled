"""ett-check-access's data reads against a real in-process Tiled."""

from typing import Any

from event_timeline_tiled.settings import Settings
from event_timeline_tiled.tiled_http import TiledReader


def _reader(tiled_server: Any, url: str) -> TiledReader:
    return TiledReader(Settings(tiled_url=url, tiled_api_key=tiled_server.api_key))


def test_reads_a_table_below_an_acquisition_run(
    window_manifest: dict[str, Any], tiled_server: Any, tiled_base_url: str
) -> None:
    reader = _reader(tiled_server, tiled_base_url)
    uid = window_manifest["runs"]["inside"]["uid"]
    assert reader.metadata_status(uid) == 200
    sample = reader.read_sample(uid)
    assert sample.ok and sample.detail.startswith("table "), sample.detail


def test_reads_an_array_below_a_processing_run(
    window_manifest: dict[str, Any], tiled_server: Any, tiled_base_url: str
) -> None:
    reader = _reader(tiled_server, tiled_base_url)
    sample = reader.read_sample(window_manifest["runs"]["proc_inside_ok"]["uid"])
    assert sample.ok, sample.detail


def test_missing_node_and_empty_run(
    window_manifest: dict[str, Any], tiled_server: Any, tiled_base_url: str
) -> None:
    reader = _reader(tiled_server, tiled_base_url)
    assert reader.metadata_status("no-such-run") == 404
    assert not reader.read_sample("no-such-run").ok
    failed = window_manifest["runs"]["proc_inside_failed"]["uid"]
    assert reader.read_sample(failed).detail.startswith("no array or table")
