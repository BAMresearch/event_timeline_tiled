"""The one error a preset file can raise: where, and what is wrong."""

from __future__ import annotations


class PresetError(ValueError):
    """A preset that cannot be loaded. ``path`` is the schema path inside the
    file (``tracks[1].where.op``); ``line`` and ``column`` count from 1."""

    def __init__(
        self,
        message: str,
        source: str = "",
        path: str = "",
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        self.message, self.source, self.path = message, source, path
        self.line, self.column = line, column
        where = source
        if line is not None:
            where += f":{line}:{column}"
        parts = [p for p in (where, path) if p]
        super().__init__(": ".join([*parts, message]))
