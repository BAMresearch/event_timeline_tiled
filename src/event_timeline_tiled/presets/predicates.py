"""Field paths, typed equality and predicates (presets.md §5).

Defined once, here: every predicate of a preset is evaluated by the adapter,
on a run's start and stop documents and what the timeline knows about it.
Missing and ``null`` are the same to predicates; values compare by type
category, so ``1`` equals ``1.0`` and ``true`` is never ``1``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from attrs import field, frozen

PATH_ROOTS = ("uid", "root", "start", "stop", "role", "status", "streams")
"""What a field path may start with."""
_DOCUMENTS = ("start", "stop")
"""Roots with keys below them; the others are values themselves."""

ORDERING = frozenset({"gt", "gte", "lt", "lte"})
EQUALITY = frozenset({"eq", "neq"})
MEMBERSHIP = frozenset({"in", "nin"})
PRESENCE = frozenset({"exists", "missing", "not_empty"})
STRINGS = frozenset({"contains", "starts_with", "ends_with", "regex"})
OPERATORS = ORDERING | EQUALITY | MEMBERSHIP | PRESENCE | STRINGS


class _Missing:
    """A value that is absent or ``null``."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()


def parse_path(text: str) -> tuple[str, ...]:
    """A dotted field path, checked against :data:`PATH_ROOTS`; raises
    ``ValueError`` with the reason."""
    if not isinstance(text, str) or not text:
        raise ValueError("a field path must be non-empty text")
    parts = tuple(text.split("."))
    if any(not p for p in parts):
        raise ValueError(f"{text!r}: empty segment in the field path")
    if parts[0] not in PATH_ROOTS:
        raise ValueError(f"{text!r}: a field path starts with one of {', '.join(PATH_ROOTS)}")
    if len(parts) > 1 and parts[0] not in _DOCUMENTS:
        raise ValueError(f"{text!r}: {parts[0]} has no keys below it")
    return parts


@frozen
class Context:
    """What predicates and templates see of one run (presets.md §5.1)."""

    uid: str
    root: str
    start: Mapping[str, Any] = field(repr=False)
    stop: Mapping[str, Any] | None = field(repr=False)
    role: str
    status: str
    streams: tuple[str, ...] | None
    """Sorted stream names; ``None`` while they are not known."""


def resolve(context: Context, path: Sequence[str]) -> Any:
    """The value at ``path``, or :data:`MISSING`. Keys through mappings only:
    no list indices, no traversal through lists."""
    head = path[0]
    value: Any
    if head == "start":
        value = context.start
    elif head == "stop":
        value = context.stop if context.stop is not None else MISSING
    elif head == "streams":
        value = list(context.streams) if context.streams is not None else MISSING
    else:
        value = getattr(context, head)
    for key in path[1:]:
        if not isinstance(value, Mapping) or key not in value:
            return MISSING
        value = value[key]
    return MISSING if value is None else value


def category(value: Any) -> str:
    """``boolean``, ``number``, ``string``, ``list``, ``mapping``, ``null`` or ``other``."""
    if value is None or value is MISSING:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list | tuple):
        return "list"
    if isinstance(value, Mapping):
        return "mapping"
    return "other"


def same(a: Any, b: Any) -> bool:
    """Typed equality: equal categories, numbers by value, lists and mappings
    element by element by the same rule."""
    kind = category(a)
    if kind != category(b):
        return False
    if kind == "list":
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    if kind == "mapping":
        return set(a) == set(b) and all(same(a[k], b[k]) for k in a)
    if kind == "null":
        return True
    return bool(a == b)


def _finite_number(value: Any) -> bool:
    return category(value) == "number" and math.isfinite(value)


@frozen
class Leaf:
    """``{field, op, value}``. ``relative`` (seconds from the evaluation time)
    replaces ``value`` for a relative time; ``pattern`` is a compiled regex."""

    field_text: str
    path: tuple[str, ...]
    op: str
    value: Any = None
    relative: float | None = None
    pattern: re.Pattern[str] | None = field(default=None, eq=False, repr=False)


@frozen
class All:
    items: tuple[Predicate, ...]


@frozen
class AnyOf:
    items: tuple[Predicate, ...]


@frozen
class Not:
    item: Predicate


Predicate = Leaf | All | AnyOf | Not

ALWAYS: Predicate = All(())


def evaluate(predicate: Predicate, context: Context, evaluated_at: float) -> bool:
    """Whether the run satisfies ``predicate``; relative times count from
    ``evaluated_at`` (one value per request, presets.md §5.3)."""
    if isinstance(predicate, All):
        return all(evaluate(p, context, evaluated_at) for p in predicate.items)
    if isinstance(predicate, AnyOf):
        return any(evaluate(p, context, evaluated_at) for p in predicate.items)
    if isinstance(predicate, Not):
        return not evaluate(predicate.item, context, evaluated_at)
    return _leaf(predicate, resolve(context, predicate.path), evaluated_at)


def _leaf(leaf: Leaf, value: Any, evaluated_at: float) -> bool:
    op = leaf.op
    if op == "exists":
        return value is not MISSING
    if op == "missing":
        return value is MISSING
    if op == "not_empty":
        return category(value) in ("string", "list") and len(value) > 0
    if op == "eq":
        return same(value, leaf.value)
    if op == "neq":
        return not same(value, leaf.value)
    if op == "in":
        return any(same(value, member) for member in leaf.value)
    if op == "nin":
        return not any(same(value, member) for member in leaf.value)
    if op in ORDERING:
        operand = evaluated_at + leaf.relative if leaf.relative is not None else leaf.value
        if not _finite_number(value):
            return False
        return bool(
            {
                "gt": value > operand,
                "gte": value >= operand,
                "lt": value < operand,
                "lte": value <= operand,
            }[op]  # fmt: skip
        )
    if not isinstance(value, str):
        return False
    if op == "contains":
        return leaf.value in value
    if op == "starts_with":
        return value.startswith(leaf.value)
    if op == "ends_with":
        return value.endswith(leaf.value)
    assert leaf.pattern is not None
    return leaf.pattern.search(value) is not None
