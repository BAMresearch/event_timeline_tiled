"""Presets: which runs the timeline shows, in which lanes, with which labels
(docs/design/presets.md; format reference in docs/technical/presets.md).

Deployment configuration in YAML, validated strictly at start; predicates
are evaluated here, on the adapter, never trusted to Tiled alone.
"""

from .errors import PresetError
from .model import FORMAT_VERSION, GroupBy, Lanes, Match, Preset, Track, parse_preset
from .predicates import MISSING, Context, evaluate, resolve, same
from .registry import Registry, load_registry
from .templates import Template, parse_template, render
from .yaml_subset import load

__all__ = [
    "FORMAT_VERSION",
    "MISSING",
    "Context",
    "GroupBy",
    "Lanes",
    "Match",
    "Preset",
    "PresetError",
    "Registry",
    "Template",
    "Track",
    "evaluate",
    "load",
    "load_registry",
    "parse_preset",
    "parse_template",
    "render",
    "resolve",
    "same",
]
