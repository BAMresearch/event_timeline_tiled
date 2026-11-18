"""A run's start and stop documents as bounded, flat, plain-text rows."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ..layout import RunRecord
from .common import json_value

METADATA_MAX_ROWS = 2000
"""Metadata rows per run at most (start and stop together)."""
METADATA_MAX_DEPTH = 8
"""Nesting flattened into dotted keys; deeper values are shown as JSON text."""
METADATA_MAX_CHARS = 2000
"""Characters per metadata value at most; longer values end in "…"."""
METADATA_CUT = "Some values were cut: long ones end in …, deeply nested ones are …"
"""The value of the last metadata row when anything was cut."""


def plain(value: Any, depth: int = 0, cut: list[bool] | None = None) -> Any:
    """``value`` as plain JSON data: non-finite numbers become ``None``,
    unknown types their ``str``, nesting beyond the limit ``"…"`` (then
    ``cut[0]`` is set, if given)."""
    if depth > METADATA_MAX_DEPTH:
        if cut is not None:
            cut[0] = True
        return "…"
    if isinstance(value, Mapping):
        return {str(k): plain(v, depth + 1, cut) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [plain(v, depth + 1, cut) for v in value]
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return json_value(value)
    return str(value)


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | bool | int | float)


class _Rows:
    """Flattened metadata rows within the row and value-length limits."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.truncated = False

    def add(self, document: str, key: str, value: Any) -> bool:
        if len(self.rows) >= METADATA_MAX_ROWS:
            self.truncated = True
            return False
        if isinstance(value, str) and len(value) > METADATA_MAX_CHARS:
            value, self.truncated = value[: METADATA_MAX_CHARS - 1] + "…", True
        self.rows.append({"document": document, "key": key, "value": value})
        return True

    def flatten(self, document: str, prefix: str, value: Any, depth: int = 0) -> bool:
        """Mappings become dotted keys; lists of scalars are joined with ``, ``
        (strings as they are, other values as JSON); other lists and nesting
        beyond the limit are JSON text. Returns ``False`` once full."""
        if isinstance(value, Mapping) and depth < METADATA_MAX_DEPTH:
            for key in sorted(value, key=str):
                name = f"{prefix}.{key}" if prefix else str(key)
                if not self.flatten(document, name, value[key], depth + 1):
                    return False
            return True
        if isinstance(value, Mapping) or (
            isinstance(value, list | tuple) and not all(_scalar(v) for v in value)
        ):
            cut = [False]
            text = json.dumps(
                plain(value, depth, cut), allow_nan=False, default=str, ensure_ascii=False
            )
            self.truncated = self.truncated or cut[0]
            return self.add(document, prefix, text)
        if isinstance(value, list | tuple):
            parts = [
                v if isinstance(v, str) else json.dumps(plain(v), ensure_ascii=False) for v in value
            ]
            return self.add(document, prefix, ", ".join(parts))
        return self.add(document, prefix, plain(value))


def metadata_rows(run: RunRecord) -> tuple[list[dict[str, Any]], bool]:
    """The start and stop documents as ``(rows, truncated)``."""
    rows = _Rows()
    for document, content in (("start", run.start), ("stop", run.stop or {})):
        if not rows.flatten(document, "", content):
            break
    if rows.truncated:  # shown as the table's last row
        rows.rows.append({"document": "", "key": "(cut)", "value": METADATA_CUT})
    return rows.rows, rows.truncated
