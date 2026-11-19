"""What every endpoint module shares: parameter types, input checks, the
mapping of Tiled errors to HTTP errors, JSON-safe values and the messages for
an empty or unknown selection."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, Path, Query

from ..tiled_http import HealthStatus, SourceError

MAX_WINDOW_S = 400 * 86400
"""Longest window accepted (a little over a year)."""
UID_PATTERN = r"[A-Za-z0-9_~.-]{1,200}"
UID = Annotated[str, Path(pattern=f"^{UID_PATTERN}$")]
SELECTED = Annotated[str, Query(pattern=f"^({UID_PATTERN})?$")]
"""A dashboard selection: a run key or stream name, or empty for none."""
VIEW = Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]*$")]
MAX_POINTS = Annotated[int, Query(ge=10, le=20_000)]
AXIS = Annotated[int | None, Query(ge=1, le=2)]
X_AXIS = Annotated[Literal["time", "elapsed"], Query(alias="x")]
"""A time series' x: ``time`` (long rows, epoch ms) or ``elapsed`` (wide rows,
seconds since the run's start)."""
TZ = Annotated[str, Query(max_length=64, pattern=r"^[A-Za-z0-9_+/-]*$")]
"""An IANA time zone for readable times, such as Grafana's ``${__timezone}``;
empty or ``utc``: UTC."""


def zone(name: str) -> ZoneInfo:
    """The time zone ``name`` (:data:`TZ`); 422 if it is not known."""
    if not name or name.lower() == "utc":
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        raise HTTPException(422, f"unknown time zone {name!r}") from None


FROM = Annotated[float, Query(alias="from")]
TO = Annotated[float, Query(alias="to")]
PRESET = Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9-]{0,63}$")]
"""A preset id; omitted: the deployment's default preset."""
LANE = Annotated[str | None, Query(pattern=r"^([a-z][a-z0-9-]*\.[a-z][a-z0-9-]*\.[0-9a-f]{12})?$")]
"""A logical lane's id (:func:`event_timeline_tiled.lanes.lane_id`); empty: none."""
ROOT = Annotated[str | None, Query(max_length=1000)]
"""A run's catalogue root as ``/clips`` gives it (``/`` for the server root);
empty: not given."""

NO_SELECTION = "Select a run on the timeline or in Runs in range"
# A selection from a bookmark, a shared link or another Tiled: a message, not an error.
NOT_FOUND = "The selected run is not in Tiled (under the configured roots): select another"
NOT_WRITTEN = " (none in Tiled yet)"


def check_window(t_from: float, t_to: float) -> None:
    if not (math.isfinite(t_from) and math.isfinite(t_to)):
        raise HTTPException(422, "from and to must be finite epoch seconds")
    if not t_from < t_to:
        raise HTTPException(422, "from must be before to")
    if t_to - t_from > MAX_WINDOW_S:
        raise HTTPException(422, f"window longer than {MAX_WINDOW_S // 86400} days")


def upstream(exc: SourceError) -> HTTPException:
    """A failed Tiled request as an HTTP error: a category, never a body."""
    code = 404 if exc.status is HealthStatus.NOT_FOUND else 502
    return HTTPException(code, f"tiled {exc.status.value}: {exc.detail}")


def json_value(value: Any) -> Any:
    """A JSON-safe scalar: non-finite numbers become ``None``."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def ms(seconds: Any) -> int | None:
    """Epoch seconds as integer milliseconds, ``None`` if not a finite number."""
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        return None
    return round(seconds * 1000) if math.isfinite(seconds) else None


AMBIGUOUS = "The selected run's key is under several catalogue roots: select it on the timeline"


def unselected(uid: str) -> str:
    """The notice for no selection, or a selection this Tiled does not have."""
    return NOT_FOUND if uid else NO_SELECTION
