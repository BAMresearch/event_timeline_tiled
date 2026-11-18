"""Preset files: a subset of YAML with JSON's scalar types (presets.md §3).

PyYAML's ``safe_load`` reads ``no`` as false, ``2026-11-18`` as a date and
``012`` as ten, so a predicate would not mean what its author wrote. Here no
YAML type is guessed: only lowercase ``true``, ``false``, ``null`` and JSON
number syntax are typed; every other plain value is a string. Forms that are
easy to misread are refused with a hint to quote them, and features presets
do not need (tags, anchors, aliases, merge keys, several documents) are
refused outright. Limits are checked while reading, before validation.
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .errors import PresetError

MAX_BYTES = 256 * 1024
MAX_DEPTH = 32
MAX_NODES = 10_000

_STR, _SEQ, _MAP = "tag:yaml.org,2002:str", "tag:yaml.org,2002:seq", "tag:yaml.org,2002:map"
_INT = re.compile(r"-?(0|[1-9][0-9]*)")
_FLOAT = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?")
_LEADING_ZERO = re.compile(r"[-+]?0[0-9]+(\.[0-9]*)?([eE][+-]?[0-9]+)?")
_NOT_FINITE = re.compile(r"[-+]?\.(nan|inf|NaN|Inf|NAN|INF)")
_YAML_NUMBER = re.compile(
    r"[-+]?(\.[0-9][0-9_]*|[0-9][0-9_]*\.?[0-9_]*)([eE][-+]?[0-9]+)?"
    r"|[-+]?0x[0-9a-fA-F_]+|[-+]?0o[0-7_]+|[-+]?0b[01_]+"
)
"""Plain values YAML 1.1 or 1.2 may read as numbers: refused unless JSON agrees."""

Marks = dict[str, tuple[int, int]]
"""Line and column (from 1) of each schema path in the file."""


class _Loader(yaml.SafeLoader):
    """A loader that types nothing by itself: every plain scalar is a string,
    so explicit tags can be told from resolved ones."""

    yaml_implicit_resolvers: dict[Any, Any] = {}


def _mark(node: Node) -> tuple[int, int]:
    return node.start_mark.line + 1, node.start_mark.column + 1


def _scalar(node: ScalarNode, source: str, path: str) -> Any:
    text = node.value
    if node.style is not None:  # quoted or a block scalar: always text
        return text
    if text in ("true", "false"):
        return text == "true"
    if text == "null":
        return None
    hint = "quote it if it is meant as text"
    if text == "~":
        raise PresetError(
            f"'~' is not null here: write null, or {hint}", source, path, *_mark(node)
        )
    if _LEADING_ZERO.fullmatch(text):
        raise PresetError(f"{text!r}: a number with a leading zero; {hint}", source, path,
                          *_mark(node))  # fmt: skip
    if _NOT_FINITE.fullmatch(text):
        raise PresetError(f"{text!r}: numbers must be finite; {hint}", source, path,
                          *_mark(node))  # fmt: skip
    if _INT.fullmatch(text):
        return int(text)
    if _FLOAT.fullmatch(text):
        return float(text)
    if _YAML_NUMBER.fullmatch(text):
        raise PresetError(f"{text!r}: not a JSON number; write it as one, or {hint}", source,
                          path, *_mark(node))  # fmt: skip
    return text


def load(text: str | bytes, source: str = "") -> tuple[Any, Marks]:
    """The file's content as plain JSON data, and where each path is."""
    raw = text.encode() if isinstance(text, str) else text
    if len(raw) > MAX_BYTES:
        raise PresetError(f"larger than {MAX_BYTES // 1024} KiB", source)
    try:
        events = list(yaml.parse(raw, Loader=_Loader))
        documents = sum(isinstance(e, yaml.DocumentStartEvent) for e in events)
        if documents != 1:
            raise PresetError(f"must hold exactly one YAML document, not {documents}", source)
        for event in events:
            tag = getattr(event, "tag", None)
            if tag is not None:
                at = event.start_mark
                line, column = (at.line + 1, at.column + 1) if at else (None, None)
                raise PresetError(f"explicit YAML tags ({tag}) are not supported",
                                  source, "", line, column)  # fmt: skip
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                at = event.start_mark
                line, column = (at.line + 1, at.column + 1) if at else (None, None)
                raise PresetError("anchors and aliases are not supported", source, "", line, column)
        root = yaml.compose(raw, Loader=_Loader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line, column = (mark.line + 1, mark.column + 1) if mark else (None, None)
        problem = getattr(exc, "problem", None) or str(exc)
        raise PresetError(f"not valid YAML: {problem}", source, "", line, column) from None
    marks: Marks = {}
    count = [0]

    def convert(node: Node, path: str, depth: int) -> Any:
        count[0] += 1
        if count[0] > MAX_NODES:
            raise PresetError(f"more than {MAX_NODES} YAML nodes", source, path, *_mark(node))
        if depth > MAX_DEPTH:
            raise PresetError(f"nested deeper than {MAX_DEPTH}", source, path, *_mark(node))
        marks[path] = _mark(node)
        if node.tag not in (_STR, _SEQ, _MAP):
            raise PresetError(f"explicit YAML tags ({node.tag}) are not supported", source,
                              path, *_mark(node))  # fmt: skip
        if isinstance(node, ScalarNode):
            return _scalar(node, source, path)
        if isinstance(node, SequenceNode):
            return [convert(item, f"{path}[{i}]", depth + 1) for i, item in enumerate(node.value)]
        if isinstance(node, MappingNode):
            out: dict[str, Any] = {}
            for key_node, value_node in node.value:
                if not isinstance(key_node, ScalarNode):
                    raise PresetError("mapping keys must be text", source, path, *_mark(key_node))
                key = _scalar(key_node, source, path)
                if not isinstance(key, str):
                    raise PresetError(f"mapping key {key_node.value!r} must be text", source,
                                      path, *_mark(key_node))  # fmt: skip
                if key == "<<" and key_node.style is None:
                    raise PresetError("merge keys (<<) are not supported", source, path,
                                      *_mark(key_node))  # fmt: skip
                child = f"{path}.{key}" if path else key
                if key in out:
                    raise PresetError(f"duplicate key {key!r}", source, path, *_mark(key_node))
                out[key] = convert(value_node, child, depth + 1)
            return out
        raise PresetError("unsupported YAML node", source, path, *_mark(node))

    if root is None:
        raise PresetError("the file is empty", source)
    return convert(root, "", 0), marks
