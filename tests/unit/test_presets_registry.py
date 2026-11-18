"""The preset registry (presets.md §2)."""

from pathlib import Path

import pytest

from event_timeline_tiled.presets import PresetError, load_registry
from event_timeline_tiled.presets import registry as registry_module

TRACKS = "tracks: [{id: runs, name: Runs}]\n"


def write(directory: Path, name: str, preset_id: str, title: str) -> None:
    (directory / name).write_text(f"version: 1\nid: {preset_id}\ntitle: {title}\n{TRACKS}")


def test_the_shipped_default_alone() -> None:
    registry = load_registry()
    assert [p.id for p in registry.ordered()] == ["instruments"]
    assert registry.default == "instruments" and len(registry.revision) == 16


def test_a_deployments_presets(tmp_path: Path) -> None:
    write(tmp_path, "b.yaml", "failures", "Failures")
    write(tmp_path, "a.yml", "by-sample", "By sample")
    (tmp_path / "notes.txt").write_text("not a preset")
    registry = load_registry(tmp_path)
    assert [p.id for p in registry.ordered()] == ["instruments", "by-sample", "failures"]
    assert registry.get("failures").source == str(tmp_path / "b.yaml")
    chosen = load_registry(tmp_path, default="failures")
    assert [p.id for p in chosen.ordered()][0] == "failures"
    with pytest.raises(KeyError):
        registry.get("nope")


def test_the_revision_follows_the_files(tmp_path: Path) -> None:
    write(tmp_path, "a.yaml", "a", "A")
    first = load_registry(tmp_path).revision
    assert load_registry(tmp_path).revision == first
    write(tmp_path, "a.yaml", "a", "A changed")
    assert load_registry(tmp_path).revision != first


def test_reserved_and_repeated_ids(tmp_path: Path) -> None:
    write(tmp_path, "a.yaml", "instruments", "Mine")
    with pytest.raises(PresetError, match="reserved") as caught:
        load_registry(tmp_path)
    assert caught.value.path == "id" and caught.value.line == 2
    (tmp_path / "a.yaml").unlink()
    write(tmp_path, "a.yaml", "same", "One")
    write(tmp_path, "b.yaml", "same", "Two")
    with pytest.raises(PresetError, match=r"already used by .*a\.yaml"):
        load_registry(tmp_path)


def test_one_bad_file_stops_the_whole_registry(tmp_path: Path) -> None:
    write(tmp_path, "a.yaml", "good", "Good")
    (tmp_path / "b.yaml").write_text("version: 1\nid: bad\ntitle: Bad\ntracks: []\n")
    with pytest.raises(PresetError, match=r"b\.yaml:4:9: tracks: must be a non-empty list"):
        load_registry(tmp_path)


def test_limits_and_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(PresetError, match="does not exist"):
        load_registry(tmp_path / "missing")
    with pytest.raises(PresetError, match="default preset 'nope'"):
        load_registry(default="nope")
    monkeypatch.setattr(registry_module, "MAX_FILES", 1)
    write(tmp_path, "a.yaml", "a", "A")
    write(tmp_path, "b.yaml", "b", "B")
    with pytest.raises(PresetError, match="more than 1 preset files"):
        load_registry(tmp_path)


def test_roots_are_checked_against_the_configured_ones(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(f"version: 1\nid: a\ntitle: A\nroots: [raw]\n{TRACKS}")
    assert load_registry(tmp_path, roots=("raw", "processed")).get("a").roots == ("raw",)
    with pytest.raises(PresetError, match="not configured roots: raw"):
        load_registry(tmp_path, roots=("processed",))
