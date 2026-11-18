"""Preset files: a YAML subset with JSON's scalar types (presets.md §3)."""

import pytest

from event_timeline_tiled.presets import PresetError, load, yaml_subset


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("no", "no"), ("off", "off"), ("on", "on"), ("yes", "yes"), ("Yes", "Yes"),
        ("True", "True"), ("NULL", "NULL"), ("2026-11-18", "2026-11-18"),
        ("12abc", "12abc"), ("v1.2", "v1.2"), ("'012'", "012"), ('"~"', "~"),
        ("true", True), ("false", False), ("null", None), ("1", 1), ("-3", -3),
        ("0", 0), ("1.5", 1.5), ("1.0", 1.0), ("1e5", 100000.0), ("-2.5E-3", -0.0025),
    ],
)  # fmt: skip
def test_only_json_scalars_are_typed(text: str, value: object) -> None:
    data, _ = load(f"a: {text}\n")
    assert data["a"] == value and type(data["a"]) is type(value)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("~", "not null here"), ("012", "leading zero"), ("-012", "leading zero"),
        (".nan", "finite"), (".inf", "finite"), ("-.Inf", "finite"), ("+1", "not a JSON"),
        (".5", "not a JSON"), ("1.", "not a JSON"), ("0x1F", "not a JSON"),
        ("1_000", "not a JSON"),
    ],
)  # fmt: skip
def test_misleading_scalars_are_refused_with_a_hint(text: str, reason: str) -> None:
    with pytest.raises(PresetError, match=reason) as caught:
        load(f"a: {text}\n", "p.yaml")
    assert (caught.value.line, caught.value.column, caught.value.path) == (1, 4, "a")
    assert "quote it" in str(caught.value)


def test_nested_values_keep_their_types() -> None:
    data, marks = load("a: {b: [no, 1, {c: null}]}\n")
    assert data == {"a": {"b": ["no", 1, {"c": None}]}}
    assert marks["a.b[2].c"] == (1, 20)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("a: 1\na: 2\n", "duplicate key 'a'"),
        ("1: a\n", "must be text"),
        ("[a]: 1\n", "must be text"),
        ("a: !!str 1\n", "explicit YAML tags"),
        ("a: !custom x\n", "explicit YAML tags"),
        ("a: &x 1\nb: *x\n", "anchors and aliases"),
        ("a: &x 1\n", "anchors and aliases"),
        ("<<: {a: 1}\n", "merge keys"),
        ("a: 1\n---\nb: 2\n", "exactly one YAML document"),
        ("", "exactly one YAML document"),
        ("a: [1\n", "not valid YAML"),
    ],
)  # fmt: skip
def test_refused_forms(text: str, reason: str) -> None:
    with pytest.raises(PresetError, match=reason):
        load(text, "p.yaml")


def test_a_quoted_merge_key_is_an_ordinary_key() -> None:
    assert load("'<<': 1\n")[0] == {"<<": 1}


def test_limits_are_checked_while_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(PresetError, match="larger than 256 KiB"):
        load("a: " + "x" * (256 * 1024) + "\n")
    deep = "a: " + "[" * 40 + "]" * 40 + "\n"
    with pytest.raises(PresetError, match="nested deeper than 32"):
        load(deep)
    monkeypatch.setattr(yaml_subset, "MAX_NODES", 5)
    with pytest.raises(PresetError, match="more than 5 YAML nodes"):
        load("a: [1, 2, 3, 4, 5]\n")


def test_errors_name_the_file_line_and_column() -> None:
    with pytest.raises(PresetError) as caught:
        load("a: 1\nb:\n  c: 012\n", "presets/x.yaml")
    assert str(caught.value).startswith("presets/x.yaml:3:6: b.c: '012'")
