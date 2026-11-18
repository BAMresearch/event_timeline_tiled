"""Lanes and placements: a preset applied to the window's runs (presets.md §6).

A *run* is one ``(root, uid)``; a *placement* is a run in one logical lane,
one per matching ``(track, group)``. A lane's key is the preset, the track
and the typed group value (with a marker for a missing component); its id is
a stable digest of that key, safe in URLs; its base name is rendered from the
key alone. Pure functions: what Tiled knows comes in through the arguments.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from attrs import frozen

from .clips import Clip, Names
from .layout import RunRecord
from .presets import MISSING, Context, Match, Preset, Track, render, resolve
from .presets.predicates import Leaf, Predicate, category
from .presets.templates import SEPARATOR, value_text

NAME_LIMIT = 180
"""Characters of a lane's base name; final names add ``(2)`` or ``/2``."""
LABEL_LIMIT = 200
_EXCLUDED = object()


@frozen
class Lane:
    id: str
    preset: str
    track: str
    key: tuple[Any, ...]
    """Canonical group components; ``None`` marks a missing one."""
    base_name: str
    order: tuple[Any, ...]


@frozen
class Placement:
    clip: Clip
    lane: Lane
    label: str


@frozen
class Arrangement:
    """A preset applied to one window's runs."""

    preset: str
    evaluated_at: float
    lanes: list[Lane]
    """In display order."""
    placements: list[Placement]
    """In lane order, then by start."""
    runs: int
    """Runs in the window (candidates, after the search's own cap)."""
    out_of_scope: int
    """Runs the adapter's own check of ``scope`` left out."""
    matched: int
    """Runs with at least one placement."""
    streams_unknown: int
    """Runs left out of a track that requires streams not known yet."""
    invalid_groups: int
    """Placements whose group had a list or mapping value."""
    lanes_cut: int
    """Lanes left out by the lane budget."""
    placements_cut: int
    """Placements left out by the placement budget."""

    def first_lane(self, root: str, uid: str) -> Lane | None:
        """The run's first lane in the preset's track order."""
        return next((p.lane for p in self.placements
                     if p.clip.root == root and p.clip.uid == uid), None)  # fmt: skip

    def lane(self, lane_id: str) -> Lane | None:
        return next((lane for lane in self.lanes if lane.id == lane_id), None)

    def runs_in(self, lane_id: str) -> list[Clip]:
        """The lane's runs, each once."""
        return [p.clip for p in self.placements if p.lane.id == lane_id]


def needs_streams(preset: Preset) -> bool:
    """Whether evaluating ``preset`` needs the runs' stream names."""

    def uses(predicate: Predicate) -> bool:
        if isinstance(predicate, Leaf):
            return predicate.path == ("streams",)
        if hasattr(predicate, "items"):
            return any(uses(p) for p in predicate.items)
        return uses(predicate.item)

    return any(t.require_streams or uses(t.where) for t in preset.tracks)


def context(clip: Clip, run: RunRecord, streams: tuple[str, ...] | None) -> Context:
    return Context(clip.uid, clip.root, run.start, run.stop, clip.role.value, clip.status.value,
                   streams)  # fmt: skip


def _canonical(value: Any, invalid: str) -> Any:
    """A group component: ``None`` for missing, ``_EXCLUDED`` for a list or
    mapping the track excludes, else a typed, normalised value."""
    if value is MISSING or value is None or (isinstance(value, str) and not value.strip()):
        return None
    kind = category(value)
    if kind in ("list", "mapping", "other"):
        return None if invalid == "missing" else _EXCLUDED
    if kind == "number":
        if not math.isfinite(value):
            return str(value)
        return int(value) if float(value).is_integer() else float(value)
    return value


def _sort_key(component: Any) -> tuple[Any, ...]:
    """Booleans, numbers, text (case-insensitively, then exactly), missing last."""
    if component is None:
        return (3,)
    if isinstance(component, bool):
        return (0, component)
    if isinstance(component, int | float):
        return (1, component)
    return (2, str(component).casefold(), str(component))


def _encode(component: Any) -> list[Any]:
    if component is None:
        return ["missing"]
    if isinstance(component, bool):
        return ["boolean", component]
    if isinstance(component, int | float):
        return ["number", component]
    return ["string", component]


def lane_id(preset: str, track: str, key: tuple[Any, ...]) -> str:
    """A stable, URL-safe id: the preset, the track and a digest of the key."""
    payload = json.dumps([_encode(c) for c in key], ensure_ascii=False, separators=(",", ":"))
    return f"{preset}.{track}.{hashlib.sha256(payload.encode()).hexdigest()[:12]}"


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _base_name(track: Track, key: tuple[Any, ...]) -> str:
    group = track.group_by
    if group is None:
        return _cut(track.name, NAME_LIMIT)
    by_path = dict(zip(group.fields, key, strict=True))

    def shown(component: Any) -> str:
        return group.fallback if component is None else value_text(component)

    name = ""
    if group.name is not None:
        name = render(group.name, lambda path: MISSING if by_path.get(path) is None
                      else by_path[path])  # fmt: skip
    if not name:
        name = SEPARATOR.join(shown(c) for c in key) or group.fallback
    if track.lane_prefix:
        name = f"{track.name}{SEPARATOR}{name}"
    return _cut(name, NAME_LIMIT)


def _render_label(track: Track, ctx: Context) -> str:
    assert track.label is not None
    return render(track.label, lambda path: resolve(ctx, path))


def arrange(
    preset: Preset,
    clips: Sequence[Clip],
    records: Mapping[tuple[str, str], RunRecord],
    evaluated_at: float,
    streams_of: Callable[[RunRecord], tuple[str, ...] | None],
    max_lanes: int,
    max_placements: int,
) -> Arrangement:
    """Every placement of the window's runs in ``preset``'s lanes, within the
    lane and placement budgets (the excess is counted, not hidden)."""
    with_streams = needs_streams(preset)
    lanes: dict[str, Lane] = {}
    placements: list[Placement] = []
    out_of_scope = streams_unknown = invalid = 0
    matched: set[tuple[str, str]] = set()
    lane_limit = min(max_lanes, preset.lanes.max_lanes or max_lanes)
    for clip in clips:
        run = records[(clip.root, clip.uid)]
        ctx = context(clip, run, streams_of(run) if with_streams else None)
        if not preset.in_scope(ctx):
            out_of_scope += 1
            continue
        for index, track in enumerate(preset.tracks):
            match = track.matches(ctx, evaluated_at)
            if match is Match.STREAMS_UNKNOWN:
                streams_unknown += 1
            if match is not Match.YES:
                continue
            key: tuple[Any, ...] = ()
            if track.group_by is not None:
                key = tuple(_canonical(resolve(ctx, p), track.group_by.invalid)
                            for p in track.group_by.fields)  # fmt: skip
                if any(c is _EXCLUDED for c in key):
                    invalid += 1
                    continue
                raw = [resolve(ctx, p) for p in track.group_by.fields]
                if any(category(v) in ("list", "mapping") for v in raw):
                    invalid += 1
            ident = lane_id(preset.id, track.id, key)
            lane = lanes.get(ident)
            if lane is None:
                lane = Lane(ident, preset.id, track.id, key, _base_name(track, key),
                            (index, *(_sort_key(c) for c in key)))  # fmt: skip
                lanes[ident] = lane
            label = clip.label
            if track.label is not None:
                label = _cut(_render_label(track, ctx), LABEL_LIMIT) or clip.label
            placements.append(Placement(clip, lane, label))
            matched.add((clip.root, clip.uid))
    ordered = sorted(lanes.values(), key=lambda lane: (lane.order, lane.id))
    kept = {lane.id for lane in ordered[:lane_limit]}
    placements = sorted(
        (p for p in placements if p.lane.id in kept),
        key=lambda p: (p.lane.order, p.lane.id, p.clip.start, p.clip.root, p.clip.uid),
    )
    cut = max(0, len(placements) - max_placements)
    return Arrangement(
        preset=preset.id,
        evaluated_at=evaluated_at,
        lanes=[lane for lane in ordered if lane.id in kept],
        placements=placements[:max_placements],
        runs=len(clips),
        out_of_scope=out_of_scope,
        matched=len(matched),
        streams_unknown=streams_unknown,
        invalid_groups=invalid,
        lanes_cut=len(ordered) - len(kept),
        placements_cut=cut,
    )


def final_names(lanes: Sequence[Lane]) -> tuple[dict[str, str], Names]:
    """Unique display names for logical lanes, in lane order, and the
    allocator, so that sub-lanes and padding names never collide with them."""
    names = Names()
    return {lane.id: names.claim(lane.base_name) for lane in lanes}, names
