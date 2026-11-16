"""Adapter settings, read from the environment.

- ``ETT_TILED_URL``: base URL of the Tiled server (required).
- ``ETT_TILED_API_KEY`` or ``ETT_TILED_API_KEY_FILE``: the read-only API key,
  or a file holding it (one of them required).
- ``ETT_CATALOG_ROOTS``: comma-separated Tiled paths that hold runs; empty
  means the server root (default).
- ``ETT_OPEN_RUN_HORIZON_DAYS``: how far back open runs are searched (21).
- ``ETT_REQUEST_TIMEOUT_S``: timeout for one request to Tiled (10).
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from attrs import field, frozen, validators

# Catalogue roots are Tiled paths of plain keys: no empty, "." or ".." segments
# and nothing that changes a URL's meaning ("?", "#", "%", whitespace).
_SEGMENT = re.compile(r"^[A-Za-z0-9_~.-]+$")


def _roots(value: str | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, str):
        value = tuple(part.strip() for part in value.split(",") if part.strip())
    roots = []
    for root in value:
        root = root.strip("/")
        for segment in root.split("/") if root else ():
            if not _SEGMENT.match(segment) or segment in {".", ".."}:
                raise ValueError(f"catalogue root {root!r} has an invalid segment {segment!r}")
        roots.append(root)
    return tuple(roots)


def _url(value: str) -> str:
    """A base URL: http(s), a host, optionally a path prefix; nothing else."""
    value = value.rstrip("/")
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError:
        raise ValueError("ETT_TILED_URL is not a valid URL") from None
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("ETT_TILED_URL must be an http:// or https:// URL with a host")
    if parts.username is not None or parts.password is not None:
        raise ValueError("ETT_TILED_URL must not contain credentials")
    if parts.query or parts.fragment or "?" in value or "#" in value:
        raise ValueError("ETT_TILED_URL must not contain a query or fragment")
    return value


def _positive(_instance: object, attribute: object, value: float) -> None:
    if not (math.isfinite(value) and value > 0):
        raise ValueError(
            f"{getattr(attribute, 'name', 'value')} must be positive and finite, got {value!r}"
        )


@frozen
class Settings:
    """Validated adapter configuration. The API key is never shown in ``repr``."""

    tiled_url: str = field(converter=_url)
    tiled_api_key: str = field(repr=False, validator=validators.min_len(1))
    catalog_roots: tuple[str, ...] = field(default=(), converter=_roots)
    open_run_horizon_days: float = field(default=21.0, converter=float, validator=_positive)
    request_timeout_s: float = field(default=10.0, converter=float, validator=_positive)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        try:
            url = env["ETT_TILED_URL"]
        except KeyError:
            raise ValueError("ETT_TILED_URL is not set") from None
        key = env.get("ETT_TILED_API_KEY", "")
        key_file = env.get("ETT_TILED_API_KEY_FILE", "")
        if key and key_file:
            raise ValueError("set ETT_TILED_API_KEY or ETT_TILED_API_KEY_FILE, not both")
        if key_file:
            key = Path(key_file).read_text(encoding="utf-8").strip()
        if not key:
            raise ValueError("no Tiled API key: set ETT_TILED_API_KEY or ETT_TILED_API_KEY_FILE")
        return cls(
            tiled_url=url,
            tiled_api_key=key,
            catalog_roots=env.get("ETT_CATALOG_ROOTS", ""),
            open_run_horizon_days=env.get("ETT_OPEN_RUN_HORIZON_DAYS", "21"),
            request_timeout_s=env.get("ETT_REQUEST_TIMEOUT_S", "10"),
        )
