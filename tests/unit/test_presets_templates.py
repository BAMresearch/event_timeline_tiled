"""Templates for labels and lane names (presets.md §6.4)."""

from typing import Any

import pytest

from event_timeline_tiled.presets import MISSING, parse_template, render


def fill(template: Any, values: dict[str, Any]) -> str:
    return render(parse_template(template), lambda path: values.get(".".join(path), MISSING))


def test_parts_are_trimmed_and_empty_ones_dropped() -> None:
    parts = ["{start.instrument|(unknown)}", "{start.station}", "{role}"]
    assert fill(parts, {"start.instrument": "MOUSE", "role": "acquisition"}) == (
        "MOUSE · acquisition"
    )
    assert fill(parts, {"start.instrument": "  ", "start.station": "S1", "role": "processing"}) == (
        "(unknown) · S1 · processing"
    )
    assert fill(parts, {}) == "(unknown)"
    assert fill(["{start.a}", "{start.b}"], {}) == ""  # all empty


def test_values_are_inserted_as_they_are() -> None:
    assert fill(["{start.a}", "{start.b}"], {"start.a": "north · south", "start.b": "x"}) == (
        "north · south · x"
    )
    assert fill("{start.a}  and  {start.b}", {"start.a": " a ", "start.b": "b"}) == "a   and  b"


def test_defaults_braces_and_value_text() -> None:
    assert fill("{start.a|n/a · none}", {}) == "n/a · none"
    assert fill("{{literal}} {start.a}", {"start.a": "x"}) == "{literal} x"
    assert fill("{start.n}", {"start.n": 1.0}) == fill("{start.n}", {"start.n": 1}) == "1"
    assert fill("{start.b}", {"start.b": True}) == "true"
    assert fill("{start.m}", {"start.m": {"b": 2, "a": [1]}}) == '{"a": [1], "b": 2}'
    assert fill("{start.s}", {"start.s": "Grüße"}) == "Grüße"


@pytest.mark.parametrize(
    ("template", "reason"),
    [
        ("a } b", "lone"), ("a | b", "only allowed inside"), ("{start.a", "unclosed"),
        ("{start.a|x|y}", "may not contain '|'"), ("{start.a|{x}}", "inside a placeholder"),
        ("{nope}", "starts with one of"), ([], "non-empty list"), ([1], "non-empty list"),
    ],
)  # fmt: skip
def test_malformed_templates(template: Any, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        parse_template(template)
