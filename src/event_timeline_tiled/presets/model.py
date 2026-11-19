"""Preset format v1: the validated model and its parser (presets.md §4).

Unknown keys are refused at every level, and an explicit ``null`` is not an
omitted key. Errors name the file, line, column and schema path.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from attrs import field, frozen, validators

from .errors import PresetError
from .predicates import (
    ALWAYS,
    EQUALITY,
    MEMBERSHIP,
    ORDERING,
    PRESENCE,
    STRINGS,
    All,
    AnyOf,
    Context,
    Leaf,
    Not,
    Predicate,
    category,
    evaluate,
    parse_path,
)
from .templates import Template, parse_template
from .yaml_subset import Marks

FORMAT_VERSION = 1
IDENTITY = re.compile(r"[a-z][a-z0-9-]{0,63}")
MAX_TRACKS = 32
MAX_PREDICATE_NODES = 1000
MAX_PATTERN = 1024
MIN_LANES_LIMIT = 20
RELATIVE = re.compile(r"([-+]?[0-9]+)([smhd])")
_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400}
INVALID_POLICIES = ("exclude", "missing")
"""What a list- or mapping-valued group component means (``group_by.invalid``)."""


def _identity(_instance: Any, attribute: Any, value: str) -> None:
    if not IDENTITY.fullmatch(value):
        raise ValueError(f"{attribute.name} {value!r} is not an identity (a-z, 0-9, '-')")


@frozen
class GroupBy:
    fields: tuple[tuple[str, ...], ...] = field(validator=validators.min_len(1))
    field_texts: tuple[str, ...]
    name: Template | None = None
    fallback: str = "(unknown)"
    invalid: str = field(default="exclude", validator=validators.in_(INVALID_POLICIES))


@frozen
class Track:
    id: str = field(validator=_identity)
    name: str = field(validator=validators.min_len(1))
    where: Predicate = ALWAYS
    require_streams: tuple[str, ...] = ()
    group_by: GroupBy | None = None
    label: Template | None = None
    lane_prefix: bool = True

    def matches(self, context: Context, evaluated_at: float) -> Match:
        """Whether the run belongs to this track (presets.md §4–§5)."""
        if not evaluate(self.where, context, evaluated_at):
            return Match.NO
        if not self.require_streams:
            return Match.YES
        if context.streams is None:
            return Match.STREAMS_UNKNOWN
        return Match.YES if set(self.require_streams) <= set(context.streams) else Match.NO


class Match(Enum):
    YES = "yes"
    NO = "no"
    STREAMS_UNKNOWN = "streams unknown"
    """Fails ``require_streams`` until the streams are known (presets.md §5.4)."""


@frozen
class Lanes:
    order: tuple[str, ...] = ()
    min_lanes: int = field(default=0, validator=[validators.ge(0),
                                                  validators.le(MIN_LANES_LIMIT)])  # fmt: skip
    max_lanes: int | None = None


@frozen
class Preset:
    id: str = field(validator=_identity)
    title: str = field(validator=validators.min_len(1))
    tracks: tuple[Track, ...] = field(validator=validators.min_len(1))
    """In display order: ``lanes.order`` first, then the others in file order."""
    roots: tuple[str, ...] | None = None
    """A subset of the configured roots; ``None`` for all of them."""
    scope: tuple[Leaf, ...] = ()
    lanes: Lanes = field(factory=Lanes)
    version: int = FORMAT_VERSION
    source: str = ""

    def in_scope(self, context: Context) -> bool:
        """``scope``: a conjunction of ``eq``/``in`` on start keys (presets.md §7)."""
        return all(evaluate(leaf, context, 0.0) for leaf in self.scope)

    def admits_instrument(self, instrument: str) -> bool:
        """Whether the scope's predicates on ``start.instrument`` (if any) admit
        runs of ``instrument``: which RE Managers' queues the preset shows."""
        leaves = [leaf for leaf in self.scope if leaf.path == ("start", "instrument")]
        context = Context("", "", {"instrument": instrument}, None, "", "", None)
        return all(evaluate(leaf, context, 0.0) for leaf in leaves)


class _Reader:
    """Reads one file's data, raising errors that point into the file."""

    def __init__(self, source: str, marks: Marks) -> None:
        self.source, self.marks, self.nodes = source, marks, 0

    def fail(self, path: str, message: str) -> PresetError:
        probe = path
        while probe not in self.marks and probe:
            probe = re.sub(r"(\.[^.\[]*|\[[0-9]+\])$", "", probe)
        line, column = self.marks.get(probe, (None, None))
        return PresetError(message, self.source, path, line, column)

    def mapping(
        self, data: Any, path: str, required: Sequence[str], optional: Sequence[str]
    ) -> Mapping[str, Any]:
        if not isinstance(data, Mapping):
            raise self.fail(path, "must be a mapping")
        unknown = sorted(set(data) - set(required) - set(optional))
        if unknown:
            raise self.fail(path, f"unknown key(s) {', '.join(unknown)}; allowed: "
                                  f"{', '.join([*required, *optional])}")  # fmt: skip
        missing = [k for k in required if k not in data]
        if missing:
            raise self.fail(path, f"missing key(s) {', '.join(missing)}")
        for key, value in data.items():
            if value is None:
                raise self.fail(_join(path, key), "null is not allowed: leave the key out")
        return data

    def text(self, data: Any, path: str, identity: bool = False) -> str:
        if not isinstance(data, str) or not data.strip():
            raise self.fail(path, "must be non-empty text")
        if identity and not IDENTITY.fullmatch(data):
            raise self.fail(path, f"{data!r} is not an identity: 1–64 characters, a lowercase "
                                  "letter, then lowercase letters, digits or '-'")  # fmt: skip
        return data

    def texts(self, data: Any, path: str) -> tuple[str, ...]:
        if not isinstance(data, list):
            raise self.fail(path, "must be a list")
        out = tuple(self.text(v, f"{path}[{i}]") for i, v in enumerate(data))
        if len(set(out)) != len(out):
            raise self.fail(path, "has repeated entries")
        return out

    def path(self, data: Any, path: str) -> tuple[str, ...]:
        try:
            return parse_path(data)
        except ValueError as exc:
            raise self.fail(path, str(exc)) from None

    def template(self, data: Any, path: str) -> Template:
        try:
            return parse_template(data)
        except ValueError as exc:
            raise self.fail(path, str(exc)) from None

    def predicate(self, data: Any, path: str) -> Predicate:
        self.nodes += 1
        if self.nodes > MAX_PREDICATE_NODES:
            raise self.fail(path, f"more than {MAX_PREDICATE_NODES} predicate nodes")
        if isinstance(data, list):
            raise self.fail(path, "a predicate is a leaf, all, any or not: wrap a list in all")
        if not isinstance(data, Mapping):
            raise self.fail(path, "a predicate must be a mapping")
        forms = [k for k in ("all", "any", "not") if k in data]
        if forms:
            if len(data) != 1:
                raise self.fail(path, f"{forms[0]} must be the only key of its predicate")
            key = forms[0]
            if key == "not":
                return Not(self.predicate(data[key], f"{path}.not"))
            items = data[key]
            if not isinstance(items, list):
                raise self.fail(f"{path}.{key}", "must be a list of predicates")
            parsed = tuple(self.predicate(v, f"{path}.{key}[{i}]") for i, v in enumerate(items))
            return All(parsed) if key == "all" else AnyOf(parsed)
        return self.leaf(data, path)

    def leaf(self, data: Any, path: str) -> Leaf:
        op = data.get("op") if isinstance(data, Mapping) else None
        needs_value = op not in PRESENCE
        self.mapping(data, path, ["field", "op", *(["value"] if needs_value else [])], [])
        field_text = self.text(data["field"], f"{path}.field")
        parsed_path = self.path(field_text, f"{path}.field")
        if op not in EQUALITY | MEMBERSHIP | ORDERING | PRESENCE | STRINGS:
            raise self.fail(f"{path}.op", f"unknown operator {op!r}")
        value = data.get("value")
        where = f"{path}.value"
        if op in EQUALITY:
            if isinstance(value, Mapping) and set(value) == {"relative"}:
                raise self.fail(where, "relative times only with gt, gte, lt or lte")
            return Leaf(field_text, parsed_path, op, value)
        if op in MEMBERSHIP:
            if not isinstance(value, list):
                raise self.fail(where, f"{op} needs a list")
            if any(v is None for v in value):
                raise self.fail(where, "null in a list: use the missing operator")
            return Leaf(field_text, parsed_path, op, tuple(value))
        if op in ORDERING:
            if isinstance(value, Mapping):
                self.mapping(value, where, ["relative"], [])
                match = RELATIVE.fullmatch(str(value["relative"]))
                if not isinstance(value["relative"], str) or not match:
                    raise self.fail(f"{where}.relative", "a relative time is like '-24h' "
                                                         "(s, m, h or d)")  # fmt: skip
                seconds = int(match.group(1)) * _UNIT_S[match.group(2)]
                return Leaf(field_text, parsed_path, op, None, relative=float(seconds))
            if category(value) != "number":
                raise self.fail(where, f"{op} needs a number or a relative time")
            return Leaf(field_text, parsed_path, op, value)
        if op in STRINGS:
            if not isinstance(value, str):
                raise self.fail(where, f"{op} needs text")
            if op != "regex":
                return Leaf(field_text, parsed_path, op, value)
            if len(value) > MAX_PATTERN:
                raise self.fail(where, f"a pattern is at most {MAX_PATTERN} characters")
            try:
                pattern = re.compile(value)
            except re.error as exc:
                raise self.fail(where, f"not a valid regular expression: {exc}") from None
            return Leaf(field_text, parsed_path, op, value, pattern=pattern)
        return Leaf(field_text, parsed_path, op)

    def scope(self, data: Any, path: str) -> tuple[Leaf, ...]:
        if not isinstance(data, list):
            raise self.fail(path, "must be a list (a conjunction) of eq or in predicates")
        out = []
        for i, item in enumerate(data):
            where = f"{path}[{i}]"
            leaf = self.leaf(item, where)
            if leaf.op not in ("eq", "in"):
                raise self.fail(f"{where}.op", "scope allows only eq and in")
            if leaf.path[0] != "start" or len(leaf.path) < 2:
                raise self.fail(f"{where}.field", "scope filters start-document keys only")
            operands = leaf.value if leaf.op == "in" else (leaf.value,)
            kinds = {category(v) for v in operands}
            if not kinds <= {"string", "number", "boolean"}:
                raise self.fail(f"{where}.value", "scope operands are text, numbers or booleans")
            if len(kinds) > 1:
                raise self.fail(f"{where}.value", "an in list in scope holds one type")
            out.append(leaf)
        return tuple(out)

    def group_by(self, data: Any, path: str) -> GroupBy:
        self.mapping(data, path, [], ["field", "fields", "name", "fallback", "invalid"])
        if ("field" in data) == ("fields" in data):
            raise self.fail(path, "needs exactly one of field or fields")
        texts: tuple[str, ...]
        if "field" in data:
            texts = (self.text(data["field"], f"{path}.field"),)
        else:
            texts = self.texts(data["fields"], f"{path}.fields")
            if not texts:
                raise self.fail(f"{path}.fields", "must not be empty")
        where = f"{path}.field" if "field" in data else f"{path}.fields"
        paths = tuple(self.path(t, where) for t in texts)
        if any(p == ("streams",) for p in paths):
            raise self.fail(where, "streams is a list and cannot be a group "
                                   "(unfolding a lane shows streams)")  # fmt: skip
        name = None
        if "name" in data:
            name = self.template(data["name"], f"{path}.name")
            outside = sorted(".".join(p) for p in name.paths - set(paths))
            if outside:
                raise self.fail(f"{path}.name", f"a lane name may only use the group's own "
                                                f"fields, not {', '.join(outside)}")  # fmt: skip
        fallback = self.text(data["fallback"], f"{path}.fallback") if "fallback" in data else None
        invalid = data.get("invalid", "exclude")
        if invalid not in INVALID_POLICIES:
            raise self.fail(f"{path}.invalid", f"must be one of {', '.join(INVALID_POLICIES)}")
        return GroupBy(paths, texts, name, fallback or "(unknown)", invalid)

    def track(self, data: Any, path: str) -> Track:
        self.mapping(data, path, ["id", "name"],
                     ["where", "require_streams", "group_by", "label", "lane_prefix"])  # fmt: skip
        prefix = data.get("lane_prefix", True)
        if not isinstance(prefix, bool):
            raise self.fail(f"{path}.lane_prefix", "must be true or false")
        return Track(
            id=self.text(data["id"], f"{path}.id", identity=True),
            name=self.text(data["name"], f"{path}.name"),
            where=self.predicate(data["where"], f"{path}.where") if "where" in data else ALWAYS,
            require_streams=self.texts(data["require_streams"], f"{path}.require_streams")
            if "require_streams" in data else (),
            group_by=self.group_by(data["group_by"], f"{path}.group_by")
            if "group_by" in data else None,
            label=self.template(data["label"], f"{path}.label") if "label" in data else None,
            lane_prefix=prefix,
        )  # fmt: skip

    def lanes(self, data: Any, path: str, track_ids: Sequence[str],
              budget: int | None) -> Lanes:  # fmt: skip
        self.mapping(data, path, [], ["order", "min_lanes", "max_lanes"])
        order = self.texts(data["order"], f"{path}.order") if "order" in data else ()
        unknown = [t for t in order if t not in track_ids]
        if unknown:
            raise self.fail(f"{path}.order", f"unknown track id(s) {', '.join(unknown)}")
        numbers = {}
        for key, low, high in (("min_lanes", 0, MIN_LANES_LIMIT), ("max_lanes", 1, budget)):
            if key not in data:
                continue
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, int):
                raise self.fail(f"{path}.{key}", "must be a whole number")
            if value < low or (high is not None and value > high):
                limit = f"{low}–{high}" if high is not None else f"at least {low}"
                hint = " (it can only lower the deployment's budget)" if key == "max_lanes" else ""
                raise self.fail(f"{path}.{key}", f"must be {limit}{hint}")
            numbers[key] = value
        return Lanes(order, numbers.get("min_lanes", 0), numbers.get("max_lanes"))


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def parse_preset(
    data: Any,
    marks: Marks,
    source: str = "",
    roots: Sequence[str] | None = None,
    lane_budget: int | None = None,
) -> Preset:
    """A validated preset from a file's loaded data. ``roots`` are the
    configured catalogue roots a preset may narrow to; ``lane_budget`` is the
    deployment's limit that ``lanes.max_lanes`` may only lower."""
    reader = _Reader(source, marks)
    reader.mapping(data, "", ["version", "id", "title", "tracks"],
                   ["roots", "scope", "lanes"])  # fmt: skip
    version = data["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != FORMAT_VERSION:
        raise reader.fail("version", f"must be the whole number {FORMAT_VERSION}")
    tracks_data = data["tracks"]
    if not isinstance(tracks_data, list) or not tracks_data:
        raise reader.fail("tracks", "must be a non-empty list")
    if len(tracks_data) > MAX_TRACKS:
        raise reader.fail("tracks", f"at most {MAX_TRACKS} tracks")
    tracks = [reader.track(t, f"tracks[{i}]") for i, t in enumerate(tracks_data)]
    ids = [t.id for t in tracks]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise reader.fail("tracks", f"repeated track id(s) {', '.join(repeated)}")
    preset_roots = None
    if "roots" in data:
        preset_roots = reader.texts(data["roots"], "roots") if data["roots"] else ()
        if roots is not None:
            outside = [r for r in preset_roots if r not in roots]
            if outside:
                raise reader.fail("roots", f"not configured roots: {', '.join(outside)}")
        if not preset_roots:
            raise reader.fail("roots", "must not be empty: leave it out for all roots")
    lanes = reader.lanes(data["lanes"], "lanes", ids, lane_budget) if "lanes" in data else Lanes()
    first = [t for o in lanes.order for t in tracks if t.id == o]
    ordered = (*first, *(t for t in tracks if t.id not in lanes.order))
    return Preset(
        id=reader.text(data["id"], "id", identity=True),
        title=reader.text(data["title"], "title"),
        tracks=ordered,
        roots=preset_roots,
        scope=reader.scope(data["scope"], "scope") if "scope" in data else (),
        lanes=lanes,
        source=source,
    )
