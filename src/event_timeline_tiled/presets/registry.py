"""The preset registry: the shipped default and a deployment's files
(presets.md §2), loaded once, at start, all or nothing."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from importlib import resources
from pathlib import Path

from attrs import frozen

from .errors import PresetError
from .model import Preset, parse_preset
from .yaml_subset import load

SHIPPED = ("instruments.yaml",)
"""Preset files in this package; their ids are reserved."""
MAX_FILES = 64


@frozen
class Registry:
    presets: Mapping[str, Preset]
    default: str
    revision: str
    """A digest of every loaded file: changes whenever a preset does."""

    def ordered(self) -> list[Preset]:
        """The default first, then the others by title."""
        rest = sorted(
            (p for p in self.presets.values() if p.id != self.default),
            key=lambda p: (p.title.casefold(), p.id),
        )
        return [self.presets[self.default], *rest]

    def get(self, preset_id: str) -> Preset:
        """The preset ``preset_id``; ``KeyError`` if there is none."""
        return self.presets[preset_id]


def load_registry(
    directory: Path | None = None,
    roots: Sequence[str] | None = None,
    default: str = "instruments",
    lane_budget: int | None = None,
) -> Registry:
    """The shipped presets and the ``*.yaml``/``*.yml`` files in ``directory``.
    Raises :class:`PresetError` for the first problem: a file that does not
    load, a reserved or repeated id, too many files, an unknown default."""
    package = resources.files(__package__)
    sources: list[tuple[str, bytes, bool]] = [
        (f"(package)/{name}", (package / name).read_bytes(), True) for name in SHIPPED
    ]
    if directory is not None:
        if not directory.is_dir():
            raise PresetError("the presets directory does not exist", str(directory))
        files = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
        if len(files) > MAX_FILES:
            raise PresetError(f"more than {MAX_FILES} preset files", str(directory))
        sources += [(str(f), f.read_bytes(), False) for f in files]
    reserved: set[str] = set()
    presets: dict[str, Preset] = {}
    digest = hashlib.sha256()
    for source, raw, shipped in sources:
        data, marks = load(raw, source)
        preset = parse_preset(data, marks, source, roots, lane_budget)
        if preset.id in reserved:
            raise PresetError(f"the id {preset.id!r} is reserved for the shipped preset",
                              source, "id", *marks.get("id", (None, None)))  # fmt: skip
        if preset.id in presets:
            raise PresetError(f"the id {preset.id!r} is already used by "
                              f"{presets[preset.id].source}", source, "id",
                              *marks.get("id", (None, None)))  # fmt: skip
        if shipped:
            reserved.add(preset.id)
        presets[preset.id] = preset
        digest.update(preset.id.encode() + b"\0" + hashlib.sha256(raw).digest())
    if default not in presets:
        raise PresetError(f"the default preset {default!r} is not loaded")
    return Registry(presets, default, digest.hexdigest()[:16])
