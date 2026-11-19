"""Adapter settings, read from the environment.

- ``ETT_TILED_URL``: base URL of the Tiled server (required).
- ``ETT_TILED_API_KEY`` or ``ETT_TILED_API_KEY_FILE``: the read-only API key,
  or a file holding it (one of them required).
- ``ETT_CATALOG_ROOTS``: comma-separated Tiled paths that hold runs; empty
  means the server root (default).
- ``ETT_OPEN_RUN_HORIZON_DAYS``: how far back open runs are searched (21).
- ``ETT_REQUEST_TIMEOUT_S``: timeout for one request to Tiled (10).
- ``ETT_ACTIVITY_REFRESH_S``: how often an open run's last event is re-read (60).
- ``ETT_MAX_RUNS``: most runs returned per root and window; more are
  reported as truncated (10000).
- ``ETT_QUEUES``: RE Managers whose status is shown, comma-separated
  ``instrument=zmq:tcp://host:60615`` (0MQ, straight to the RE Manager) or
  ``instrument=http:http://host:60610`` (Bluesky HTTP Server); empty: none.
  For HTTP, the API key (scopes ``read:status``, ``read:queue``) comes from
  the file named by ``ETT_QUEUE_<INSTRUMENT>_API_KEY_FILE`` (instrument in
  upper case, other characters as ``_``), else by ``ETT_QUEUE_API_KEY_FILE``
  (for every HTTP queue without its own; an empty file means no key).
- ``ETT_PRESETS_DIR``: a directory of preset files (``*.yaml``), loaded at
  start besides the shipped ``instruments``; empty: none
  (docs/technical/presets.md).
- ``ETT_DEFAULT_PRESET``: the preset used without a ``preset`` parameter, and
  listed first (``instruments``).
- ``ETT_MAX_LANES``, ``ETT_MAX_SUBLANES``, ``ETT_MAX_PLACEMENTS``: the most
  logical lanes, final timeline rows and placements one frame may have; more
  are left out and reported (100, 200, 20000).
- ``ETT_UNFOLD_MAX_RUNS``, ``ETT_UNFOLD_MAX_STREAMS``, ``ETT_UNFOLD_MAX_EVENTS``,
  ``ETT_UNFOLD_TIME_S``: unfolding one lane into its streams reads at most the
  latest so many runs, streams per run and events per stream, for at most so
  long per request (50, 16, 200000, 10); the rest stays folded and is
  reported.
- ``ETT_UNFOLD_REVALIDATE_S``: re-read a finished run's stream extents after
  this long (0: never, for producers that write all of a run before its stop
  document, as Bluesky's ``TiledWriter`` does).
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


def _optional_path(value: str | Path | None) -> Path | None:
    return Path(value) if value else None


_QUEUE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)=(?P<transport>zmq|http):(?P<address>\S+)$")


@frozen
class QueueConfig:
    """One RE Manager to show the status of (plan §5.5). Read-only use only."""

    instrument: str
    transport: str = field(validator=validators.in_(("zmq", "http")))
    address: str
    api_key: str | None = field(default=None, repr=False)

    def __attrs_post_init__(self) -> None:
        scheme = {"zmq": ("tcp",), "http": ("http", "https")}[self.transport]
        parts = urlsplit(self.address)
        if parts.scheme not in scheme or not parts.hostname or parts.username or parts.password:
            raise ValueError(
                f"queue {self.instrument!r}: {self.transport} address must be "
                f"{' or '.join(s + '://host:port' for s in scheme)} without credentials"
            )

    @staticmethod
    def env_name(instrument: str) -> str:
        return "ETT_QUEUE_" + re.sub(r"[^A-Z0-9]", "_", instrument.upper()) + "_API_KEY_FILE"


def parse_queues(value: str, env: Mapping[str, str]) -> tuple[QueueConfig, ...]:
    configs = []
    for entry in (part.strip() for part in value.split(",")):
        if not entry:
            continue
        match = _QUEUE.match(entry)
        if match is None:
            raise ValueError(f"ETT_QUEUES entry {entry!r} is not instrument=zmq|http:address")
        key = None
        key_file = env.get(QueueConfig.env_name(match["name"]), "")
        if not key_file and match["transport"] == "http":
            key_file = env.get("ETT_QUEUE_API_KEY_FILE", "")
        if key_file:
            key = Path(key_file).read_text(encoding="utf-8").strip() or None
        configs.append(QueueConfig(match["name"], match["transport"], match["address"], key))
    names = [c.instrument for c in configs]
    if len(set(names)) != len(names):
        raise ValueError("ETT_QUEUES names an instrument twice")
    return tuple(configs)


@frozen
class Settings:
    """Validated adapter configuration. The API key is never shown in ``repr``."""

    tiled_url: str = field(converter=_url)
    tiled_api_key: str = field(repr=False, validator=validators.min_len(1))
    catalog_roots: tuple[str, ...] = field(default=(), converter=_roots)
    open_run_horizon_days: float = field(default=21.0, converter=float, validator=_positive)
    request_timeout_s: float = field(default=10.0, converter=float, validator=_positive)
    activity_refresh_s: float = field(default=60.0, converter=float, validator=_positive)
    max_runs: int = field(default=10_000, converter=int, validator=validators.ge(1))
    queues: tuple[QueueConfig, ...] = ()
    presets_dir: Path | None = field(default=None, converter=_optional_path)
    default_preset: str = field(default="instruments", validator=validators.min_len(1))
    max_lanes: int = field(default=100, converter=int, validator=validators.ge(1))
    max_sublanes: int = field(default=200, converter=int, validator=validators.ge(1))
    max_placements: int = field(default=20_000, converter=int, validator=validators.ge(1))
    unfold_max_runs: int = field(default=50, converter=int, validator=validators.ge(1))
    unfold_max_streams: int = field(default=16, converter=int, validator=validators.ge(1))
    unfold_max_events: int = field(default=200_000, converter=int, validator=validators.ge(1))
    unfold_time_s: float = field(default=10.0, converter=float, validator=_positive)
    unfold_revalidate_s: float = field(default=0.0, converter=float, validator=validators.ge(0))

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
            activity_refresh_s=env.get("ETT_ACTIVITY_REFRESH_S", "60"),
            max_runs=env.get("ETT_MAX_RUNS", "10000"),
            queues=parse_queues(env.get("ETT_QUEUES", ""), env),
            presets_dir=env.get("ETT_PRESETS_DIR", "") or None,
            default_preset=env.get("ETT_DEFAULT_PRESET", "") or "instruments",
            max_lanes=env.get("ETT_MAX_LANES", "100"),
            max_sublanes=env.get("ETT_MAX_SUBLANES", "200"),
            max_placements=env.get("ETT_MAX_PLACEMENTS", "20000"),
            unfold_max_runs=env.get("ETT_UNFOLD_MAX_RUNS", "50"),
            unfold_max_streams=env.get("ETT_UNFOLD_MAX_STREAMS", "16"),
            unfold_max_events=env.get("ETT_UNFOLD_MAX_EVENTS", "200000"),
            unfold_time_s=env.get("ETT_UNFOLD_TIME_S", "10"),
            unfold_revalidate_s=env.get("ETT_UNFOLD_REVALIDATE_S", "0"),
        )
