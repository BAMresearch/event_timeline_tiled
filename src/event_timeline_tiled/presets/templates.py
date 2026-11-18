"""Templates for clip labels and lane names (presets.md §6.4).

A template is a string or a list of strings (parts). A part is literal text
with ``{path}`` or ``{path|default}`` placeholders; ``{{`` and ``}}`` are
literal braces. Each part is rendered and trimmed; with a list, empty parts
are dropped and the rest joined with `` · ``. Values are inserted as they
are: that join is the only clean-up, so a `` · `` inside metadata stays.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from attrs import frozen

from .predicates import MISSING, category, parse_path

SEPARATOR = " · "


@frozen
class Placeholder:
    text: str
    path: tuple[str, ...]
    default: str | None


Piece = str | Placeholder


@frozen
class Template:
    parts: tuple[tuple[Piece, ...], ...]

    @property
    def paths(self) -> set[tuple[str, ...]]:
        return {p.path for part in self.parts for p in part if isinstance(p, Placeholder)}


def _parse_part(text: str) -> tuple[Piece, ...]:
    pieces: list[Piece] = []
    literal: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c in "{}" and text[i : i + 2] == c * 2:
            literal.append(c)
            i += 2
            continue
        if c == "}":
            raise ValueError(f"{text!r}: a lone '}}' (write '}}}}' for a brace)")
        if c == "|":
            raise ValueError(f"{text!r}: '|' is only allowed inside a placeholder")
        if c != "{":
            literal.append(c)
            i += 1
            continue
        end = text.find("}", i + 1)
        if end < 0:
            raise ValueError(f"{text!r}: an unclosed '{{'")
        inner = text[i + 1 : end]
        if "{" in inner:
            raise ValueError(f"{text!r}: '{{' inside a placeholder")
        path_text, bar, default = inner.partition("|")
        if "|" in default:
            raise ValueError(f"{text!r}: a default may not contain '|'")
        if literal:
            pieces.append("".join(literal))
            literal = []
        pieces.append(Placeholder(path_text, parse_path(path_text), default if bar else None))
        i = end + 1
    if literal:
        pieces.append("".join(literal))
    return tuple(pieces)


def parse_template(value: Any) -> Template:
    """A template from a string or a non-empty list of strings; raises
    ``ValueError`` with the reason."""
    if isinstance(value, str):
        texts = [value]
    elif isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        texts = list(value)
    else:
        raise ValueError("a template is text, or a non-empty list of texts")
    return Template(tuple(_parse_part(t) for t in texts))


def value_text(value: Any) -> str:
    """How a value appears in a template: strings as they are, ``null`` and
    missing as nothing, anything else as deterministic JSON."""
    if value is MISSING or value is None:
        return ""
    if isinstance(value, str):
        return value
    if category(value) == "number" and isinstance(value, float) and value.is_integer():
        return json.dumps(int(value))
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def render(template: Template, lookup: Callable[[tuple[str, ...]], Any]) -> str:
    """The template filled in with ``lookup(path)`` for each placeholder."""
    rendered = []
    for part in template.parts:
        out = []
        for piece in part:
            if isinstance(piece, str):
                out.append(piece)
                continue
            text = value_text(lookup(piece.path))
            if not text.strip() and piece.default is not None:
                text = piece.default
            out.append(text)
        rendered.append("".join(out).strip())
    return SEPARATOR.join(r for r in rendered if r)
