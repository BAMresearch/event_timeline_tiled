"""Processing results (processing-runs.md §1.2; plan §4, step 6):
``/runs/{uid}/related``, ``/runs/{uid}/curve`` and ``/runs/{uid}/image``
(also ``/run-images/{uid}/image`` for the proxy's signed-in route)."""

from __future__ import annotations

import hashlib
import math
import threading
from collections import OrderedDict
from typing import Annotated, Any

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..images import Scale, render
from ..layout import RunRecord
from ..related import Family, default_result, families, family_of, problems, usable
from ..run_state import Role, role_of, status_of
from ..tiled_http import HealthStatus, SourceError
from ..views import ResolvedView, ViewKind, label
from .common import MAX_POINTS, UID, VIEW, upstream
from .state import Adapter

RELATED_MAX_RUNS = 500
"""Processing runs of one measurement listed at most (per catalogue root)."""
RELATED_MAX_PARENTS = 50
"""Measurements of one processing run looked up at most."""
RESULT_CACHE_ENTRIES = 32
"""Curves of finished processing runs kept in memory."""
IMAGE_CACHE_BYTES = 32 * 1024 * 1024
"""PNGs of finished processing runs kept in memory."""
IMAGE_SIZE = Annotated[int, Query(ge=64, le=2048)]
IMAGE_READ_SIDE = 2048
"""An image is read from Tiled strided to at most this many pixels per side
(about 4 million values, 32 MiB as float64), whatever its size there."""
IMAGE_RENDERS = 2
"""Images rendered at the same time at most (the others wait)."""


class Results:
    """Finding and reading processing results. Outputs of finished processing
    runs are final (processing-runs.md §1.2), so their curves and images are
    kept."""

    def __init__(self, adapter: Adapter) -> None:
        self.adapter = adapter
        self._lock = threading.Lock()
        self._curves: OrderedDict[tuple[str, ...], dict[str, Any]] = OrderedDict()
        self._images: OrderedDict[tuple[str, ...], bytes] = OrderedDict()
        self._renders = threading.BoundedSemaphore(IMAGE_RENDERS)

    def processing_of(self, run: RunRecord) -> tuple[list[RunRecord], bool]:
        """Processing runs of a measurement, over all roots, and whether the
        list may be incomplete (a search reached its cap)."""
        out: list[RunRecord] = []
        cut = False
        for root in self.adapter.timeline.roots:
            found, truncated = self.adapter.layout.derived_runs(
                root, run.start_uid or run.key, RELATED_MAX_RUNS
            )
            out += [r for r in found if role_of(r.start) is Role.PROCESSING]
            cut = cut or truncated
        return out, cut

    def related(self, run: RunRecord) -> dict[str, Any]:
        """A measurement's processing runs, by family (the latest attempt and
        the latest usable result marked); or a processing run's measurements.
        If the search may be incomplete, nothing is marked latest."""
        try:
            if role_of(run.start) is Role.PROCESSING:
                return self._measurements_of(run)
            runs, cut = self.processing_of(run)
        except SourceError as exc:
            raise upstream(exc) from None
        found_families = [] if cut else families(runs)
        if cut:  # list them unranked: the latest may be among those not found
            rows = [_related_row("processing", r, r.key) for r in runs]
            notice = _cut_notice()
        else:
            rows = [_related_row("processing", r, r.key, f) for f in found_families for r in f.runs]
            notice = _results_notice(runs, found_families, default_result(found_families))
        default = default_result(found_families)
        return {"role": Role.ACQUISITION.value, "rows": rows, "truncated": cut,
                "default": default.key if default else None, "notice": notice}  # fmt: skip

    def _measurements_of(self, run: RunRecord) -> dict[str, Any]:
        listed = run.start.get("derived_from")
        parents = [p for p in listed if isinstance(p, str)] if isinstance(listed, list) else []
        cut = len(parents) > RELATED_MAX_PARENTS
        parents = list(dict.fromkeys(parents[:RELATED_MAX_PARENTS]))
        found: dict[str, RunRecord] = {}
        for root in self.adapter.timeline.roots:
            missing = [p for p in parents if p not in found]
            for record in self.adapter.layout.runs_by_start_uid(root, missing):
                found.setdefault(record.start_uid or record.key, record)
        rows = [_related_row("measurement", found.get(p), p) for p in parents]
        absent = sum(1 for p in parents if p not in found)
        notice = f"Processing run of {len(parents)} measurement(s)" + (
            f", {absent} not in Tiled" if absent else ""
        )
        if cut:
            notice += f", the first {RELATED_MAX_PARENTS} listed"
        if issues := problems(run):
            notice += f"; breaks the processing contract: {'; '.join(issues)}"
        return {"role": Role.PROCESSING.value, "rows": rows, "truncated": cut,
                "default": None, "notice": notice}  # fmt: skip

    def corrected(
        self, run: RunRecord, kind: ViewKind, view: str | None
    ) -> tuple[RunRecord, ResolvedView, bool] | str:
        """The run whose ``kind`` view to show, that view, and whether it was
        followed from a measurement to its latest usable result; or why
        there is none. ``view`` restricts the choice to a view of that name.
        A measurement is followed only if its processing runs were all found."""

        def pick(views: list[ResolvedView]) -> ResolvedView | None:
            return next((v for v in views if v.kind is kind and v.name == (view or v.name)
                         and v.available), None)  # fmt: skip

        chosen = pick(self.adapter.views_of(run))
        if chosen is not None:
            return run, chosen, False
        if role_of(run.start) is Role.PROCESSING:
            return f"this processing run has no {kind.value}" + (
                f" ({status_of(run.stop).value})" if run.stop is not None else " yet"
            )
        try:
            runs, cut = self.processing_of(run)
        except SourceError as exc:
            raise upstream(exc) from None
        if cut:
            return _cut_notice()
        found_families = families(runs)
        target = default_result(found_families)
        if target is None:
            return _results_notice(runs, found_families, None)
        chosen = pick(self.adapter.views_of(target))
        if chosen is None:
            return f"the latest usable result, {_identity(target)}, has no {kind.value}"
        return target, chosen, True

    def curve_rows(self, run: RunRecord, view: ResolvedView, max_points: int) -> dict[str, Any]:
        """Rows ``x, y, y_lower, y_upper`` for log–log axes: points with a
        non-finite or non-positive x or y are left out (and counted); the
        bounds are y ∓ the uncertainty, the lower one left out where it is
        not positive. Evenly decimated to ``max_points``."""
        final = run.stop is not None
        key = (run.root, run.key, view.name, str(max_points))
        if final:
            with self._lock:
                if key in self._curves:
                    self._curves.move_to_end(key)
                    return self._curves[key]
        body = _curve_body(self._read_curve(run, view), view, max_points)
        if final:
            with self._lock:
                self._curves[key] = body
                while len(self._curves) > RESULT_CACHE_ENTRIES:
                    self._curves.popitem(last=False)
        return body

    def _read_curve(self, run: RunRecord, view: ResolvedView) -> list[np.ndarray[Any, Any]]:
        [stream] = view.streams
        arrays = []
        for name in view.fields:
            info = self.adapter.layout.result_info(run, stream, name)
            if len(info.shape) != 1:
                raise SourceError(HealthStatus.ERROR, f"{stream}/{name} is not 1D")
            arrays.append(self.adapter.layout.result_array(run, stream, name, info))
        if len({a.shape for a in arrays}) != 1:
            raise SourceError(HealthStatus.ERROR, f"{stream}: fields of different lengths")
        return arrays

    def curve(self, run: RunRecord, view: str | None, max_points: int) -> dict[str, Any]:
        found = self.corrected(run, ViewKind.CURVE, view)
        if isinstance(found, str):
            return no_curve(found[:1].upper() + found[1:])
        target, chosen, followed = found
        source, note = _source(target, chosen, followed)
        try:
            curve = self.curve_rows(target, chosen, max_points)
        except SourceError as exc:
            problem = f"{note}: could not be read ({exc.status.value}: {exc.detail})"
            return {"view": chosen.name, "kind": chosen.kind.value, "source": source,
                    "rows": [], "truncated": False, "notice": problem}  # fmt: skip
        return {"view": chosen.name, "kind": chosen.kind.value, "source": source, **curve,
                "notice": f"{note}: {curve['description']}"}  # fmt: skip

    def image(
        self, run: RunRecord, view: str | None, scale: Scale, size: int, if_none_match: str | None
    ) -> Response:
        found = self.corrected(run, ViewKind.IMAGE, view)
        if isinstance(found, str):
            raise HTTPException(404, found)
        target, chosen, followed = found
        final = target.stop is not None
        key = (target.root, target.key, chosen.name, scale.value, str(size))
        etag = '"' + hashlib.sha256("\0".join(key).encode()).hexdigest()[:32] + '"'
        headers = {"Cache-Control": "private, no-store"}
        if final:
            # A measurement can get a newer result: revalidate; a result never changes.
            age = "no-cache" if followed else "max-age=86400"
            headers = {"Cache-Control": f"private, {age}", "ETag": etag}
            if if_none_match == etag:
                return Response(status_code=304, headers=headers)
        with self._lock:
            png = self._images.get(key)
        if png is None:
            png = self._render(target, chosen, scale, size)
            if final:
                with self._lock:
                    self._images[key] = png
                    while sum(map(len, self._images.values())) > IMAGE_CACHE_BYTES:
                        self._images.popitem(last=False)
        return Response(png, media_type="image/png", headers=headers)

    def _render(self, run: RunRecord, view: ResolvedView, scale: Scale, size: int) -> bytes:
        [stream] = view.streams
        [name] = view.fields
        layout = self.adapter.layout
        with self._renders:
            try:
                info = layout.result_info(run, stream, name)
                if len(info.shape) != 2 or 0 in info.shape:
                    raise SourceError(HealthStatus.ERROR, f"{stream}/{name} is not 2D")
                read_side = min(2 * size, IMAGE_READ_SIDE)
                step = max(1, math.ceil(max(info.shape) / read_side))
                array = layout.result_array(run, stream, name, info, step)
            except SourceError as exc:
                raise upstream(exc) from None
            return render(array, scale, size).png


def no_curve(notice: str) -> dict[str, Any]:
    return {"view": None, "kind": ViewKind.CURVE.value, "source": None, "rows": [],
            "truncated": False, "notice": notice}  # fmt: skip


def _curve_body(
    arrays: list[np.ndarray[Any, Any]], view: ResolvedView, max_points: int
) -> dict[str, Any]:
    names = view.fields
    x, y = arrays[0], arrays[1]
    sigma = arrays[2] if len(arrays) > 2 else None
    finite = np.isfinite(x) & np.isfinite(y)
    plotted = finite & (x > 0) & (y > 0)
    index = np.flatnonzero(plotted)
    every = max(1, math.ceil(len(index) / max_points))
    index = index[::every]
    rows = []
    lower_dropped = bad_sigma = 0
    with np.errstate(over="ignore", invalid="ignore"):
        for i in index:
            lower = upper = None
            if sigma is not None:
                s_i = float(sigma[i])
                if not math.isfinite(s_i) or s_i < 0:
                    bad_sigma += 1  # no bounds: an uncertainty is finite and ≥ 0
                else:
                    hi, lo = float(y[i]) + s_i, float(y[i]) - s_i
                    upper = hi if math.isfinite(hi) else None
                    if math.isfinite(lo) and lo > 0:
                        lower = lo
                    else:
                        lower_dropped += 1
            rows.append({"x": float(x[i]), "y": float(y[i]), "y_lower": lower, "y_upper": upper})
    units = dict(view.units)

    def named(f: str) -> str:
        return f"{f} [{units[f]}]" if f in units else f

    description = f"{named(names[1])} against {named(names[0])}"
    if sigma is not None:
        description += f", bounds ± {names[2]}"
    non_finite = int(len(x) - finite.sum())
    non_positive = int(finite.sum() - plotted.sum())
    if non_positive:
        description += f"; {non_positive} points not positive (not on log axes)"
    if non_finite:
        description += f"; {non_finite} points not finite"
    if lower_dropped:
        description += f"; lower bound left out at {lower_dropped} points"
    if bad_sigma:
        description += f"; no bounds at {bad_sigma} points (uncertainty negative or not finite)"
    if every > 1:
        description += f"; every {every}th point"
    return {"x": names[0], "y": names[1], "uncertainty": names[2] if sigma is not None
            else None, "units": units, "rows": rows, "truncated": every > 1,
            "points": len(x), "plotted": len(rows), "decimation": every,
            "non_finite": non_finite, "non_positive": non_positive,
            "lower_bound_dropped": lower_dropped, "uncertainty_invalid": bad_sigma,
            "description": description}  # fmt: skip


def _related_row(
    relation: str, run: RunRecord | None, uid: str, family: Family | None = None
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "relation": relation, "uid": uid, "label": "(not in Tiled)", "family": None,
        "attempt": None, "status": None, "usable": None, "latest_attempt": False,
        "latest_usable": False, "mark": None, "start": None, "stop": None, "reason": None,
        "problems": None,
    }  # fmt: skip
    if run is None:
        return row
    attempt, reason = run.start.get("attempt"), (run.stop or {}).get("reason")
    processing = role_of(run.start) is Role.PROCESSING
    issues = problems(run) if processing else []
    return row | {
        "uid": run.key,
        "label": label(run),
        "family": family_of(run.start) if processing else None,
        "attempt": attempt if isinstance(attempt, int) and not isinstance(attempt, bool)
        else None,
        "status": status_of(run.stop).value,
        "usable": usable(run) if processing else None,
        "latest_attempt": family is not None and family.latest_attempt is run,
        "latest_usable": family is not None and family.latest_usable is run,
        "mark": _mark(family, run),
        "start": run.start_time,
        "stop": run.stop_time,
        "reason": reason if isinstance(reason, str) and reason else None,
        "problems": "; ".join(issues) or None,
    }  # fmt: skip


def _source(run: RunRecord, view: ResolvedView, followed: bool) -> tuple[dict[str, Any], str]:
    note = f"{view.title} of {_identity(run)}"
    if followed:
        note += ", the latest usable result of the selected measurement"
    elif role_of(run.start) is Role.PROCESSING and not usable(run):
        issues = problems(run)
        why = "; ".join(issues) if issues else status_of(run.stop).value
        note += f": not a usable result ({why})"
    source = {"uid": run.key, "label": label(run), "family": family_of(run.start),
              "usable": usable(run), "followed": followed}  # fmt: skip
    return source, note


def _identity(run: RunRecord) -> str:
    """A processing run as operators can tell it apart: label, family, UID."""
    return f"{label(run)} ({family_of(run.start)}, {run.key[:8]})"


def _cut_notice() -> str:
    return (f"More than {RELATED_MAX_RUNS} processing runs of this measurement: which is the "
            "latest is not known here; select a result in Related runs")  # fmt: skip


def _mark(family: Family | None, run: RunRecord) -> str | None:
    """``latest usable``, ``latest attempt``, both, or ``None``: for a table."""
    if family is None:
        return None
    marks = [m for m, r in (("latest usable", family.latest_usable),
                            ("latest attempt", family.latest_attempt)) if r is run]  # fmt: skip
    return " · ".join(marks) or None


def _results_notice(runs: list[RunRecord], found: list[Family], default: RunRecord | None) -> str:
    """One line on a measurement's processing results."""
    if not runs:
        return "No processing run of this measurement"
    open_runs = sum(1 for r in runs if r.stop is None)
    tail = f"; {open_runs} still open" if open_runs else ""
    if default is None:
        latest = max((f.latest_attempt for f in found if f.latest_attempt), default=None,
                      key=lambda r: (r.stop_time or 0.0, r.start_uid or ""))  # fmt: skip
        last = f"; latest attempt {status_of(latest.stop).value}" if latest else ""
        return f"No usable processing result of this measurement{last}{tail}"
    family = next(f for f in found if f.latest_usable is default)
    later = family.latest_attempt
    note = ""
    if later is not None and later is not default:
        note = f"; a later attempt {status_of(later.stop).value}"
    return f"Latest usable result: {_identity(default)}{note}{tail}"


def router(adapter: Adapter, results: Results) -> APIRouter:
    api = APIRouter()

    @api.get("/runs/{uid}/related")
    def run_related(uid: UID) -> dict[str, Any]:
        """A measurement's processing runs, or a processing run's measurements."""
        return results.related(adapter.find(uid))

    @api.get("/runs/{uid}/curve")
    def run_curve(uid: UID, view: VIEW = None, max_points: MAX_POINTS = 2000) -> dict[str, Any]:
        """The run's curve view; for a measurement, that of its latest usable
        processing result."""
        return results.curve(adapter.find(uid), view, max_points)

    @api.get("/runs/{uid}/image")
    @api.get("/run-images/{uid}/image")
    def run_image(
        uid: UID,
        request: Request,
        view: VIEW = None,
        scale: Scale = Scale.LOG,
        size: IMAGE_SIZE = 768,
    ) -> Response:
        """The run's image view as a PNG; for a measurement, that of its latest
        usable processing result. ``/run-images/...`` is the same, for the
        proxy's signed-in image route (plan §6)."""
        run = adapter.find(uid)
        return results.image(run, view, scale, size, request.headers.get("if-none-match"))

    return api
