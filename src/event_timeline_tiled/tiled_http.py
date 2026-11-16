"""Read-only HTTP access to Tiled.

This module is the only place that talks to Tiled. In step 3 it grows into the
layout module of the plan (runs, streams, window searches); for now it checks
reachability and read access for the health endpoint.

The API key is sent as ``Authorization: Apikey <key>`` and never in a URL.
Response bodies are never copied into health details: a wrong upstream or a
maintenance page is reported by status and a fixed description only.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any
from urllib.parse import quote

import httpx
from attrs import frozen

from .settings import Settings


class HealthStatus(StrEnum):
    OK = "ok"
    UNREACHABLE = "unreachable"
    UNAUTHORISED = "unauthorised"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    ERROR = "error"


@frozen
class RootCheck:
    root: str
    status: HealthStatus
    latency_ms: float | None
    detail: str


@frozen
class KeyInfo:
    """What Tiled reports about the API key in use (``status`` 0: unreachable)."""

    status: int
    scopes: tuple[str, ...] | None


@frozen
class Sample:
    ok: bool
    detail: str


class MalformedResponse(Exception):
    """Tiled answered, but not with the structure this adapter expects."""


_HTTP_STATUS = {
    401: HealthStatus.UNAUTHORISED,
    403: HealthStatus.FORBIDDEN,
    404: HealthStatus.NOT_FOUND,
}


def tiled_path(path: str) -> str:
    """Percent-encode each segment of a Tiled path (segments are validated in settings)."""
    return "/".join(quote(segment, safe="") for segment in path.split("/") if segment)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        raise MalformedResponse("response is not JSON") from None
    if not isinstance(body, dict):
        raise MalformedResponse("response is not a JSON object")
    return body


def _search_page(response: httpx.Response) -> list[Any]:
    """The ``data`` list of a Tiled search response, or :class:`MalformedResponse`."""
    body = _json_object(response)
    data = body.get("data")
    if not isinstance(data, list) or not isinstance(body.get("links"), dict):
        raise MalformedResponse("response is not a Tiled search page")
    return data


class TiledReader:
    """Thin read-only client. Create one per process and close it on shutdown."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        self._http = httpx.Client(
            base_url=f"{settings.tiled_url}/api/v1",
            headers={"Authorization": f"Apikey {settings.tiled_api_key}"},
            timeout=settings.request_timeout_s,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def server_version(self) -> str | None:
        """Tiled's ``library_version``, or ``None`` if it cannot be read. Never raises."""
        try:
            response = self._http.get("/")
            response.raise_for_status()
            version = _json_object(response).get("library_version")
        except httpx.HTTPError, MalformedResponse:
            return None
        return version if isinstance(version, str) else None

    def check_root(self, root: str) -> RootCheck:
        """Search one catalogue root for a single entry: proves reachability and read access.

        A ``200`` counts as ``ok`` only if the body is a Tiled search page.
        """
        start = time.perf_counter()
        try:
            response = self._http.get(
                f"/search/{tiled_path(root)}", params={"page[limit]": 1, "fields": ""}
            )
        except httpx.TransportError as exc:
            return RootCheck(root, HealthStatus.UNREACHABLE, None, type(exc).__name__)
        except httpx.HTTPError as exc:
            return RootCheck(root, HealthStatus.ERROR, None, type(exc).__name__)
        latency = round((time.perf_counter() - start) * 1000, 1)
        if response.status_code != 200:
            status = _HTTP_STATUS.get(response.status_code, HealthStatus.ERROR)
            return RootCheck(root, status, latency, f"HTTP {response.status_code}")
        try:
            _search_page(response)
        except MalformedResponse as exc:
            return RootCheck(root, HealthStatus.ERROR, latency, str(exc))
        return RootCheck(root, HealthStatus.OK, latency, "")

    # Access checks (ett-check-access) ---------------------------------------------------

    def whoami_key(self, first_eight: str) -> KeyInfo:
        """The scopes Tiled holds for the key starting with ``first_eight``."""
        try:
            response = self._http.get("/auth/whoami")
        except httpx.HTTPError:
            return KeyInfo(0, None)
        if response.status_code != 200:
            return KeyInfo(response.status_code, None)
        try:
            keys = _json_object(response).get("api_keys")
        except MalformedResponse:
            return KeyInfo(response.status_code, None)
        for key in keys if isinstance(keys, list) else []:
            if isinstance(key, dict) and key.get("first_eight") == first_eight:
                scopes = key.get("scopes")
                if isinstance(scopes, list):
                    return KeyInfo(200, tuple(str(x) for x in scopes))
        return KeyInfo(200, None)

    def metadata_status(self, path: str) -> int:
        """HTTP status of reading one node's metadata (0: unreachable)."""
        try:
            return self._http.get(f"/metadata/{tiled_path(path)}").status_code
        except httpx.HTTPError:
            return 0

    def read_sample(self, path: str, max_depth: int = 3) -> Sample:
        """Read one element of the first array, or one column of the first table's
        first partition, found below ``path`` (breadth-first). Proves ``read:data``
        on real data rather than on metadata only."""
        queue = [(tiled_path(path), 0)]
        while queue:
            node, depth = queue.pop(0)
            try:
                response = self._http.get(
                    f"/search/{node}",
                    params={"page[limit]": 20, "fields": ["structure_family", "structure"]},
                )
            except httpx.HTTPError as exc:
                return Sample(False, type(exc).__name__)
            if response.status_code != 200:
                return Sample(False, f"listing {node or '/'}: HTTP {response.status_code}")
            try:
                items = _search_page(response)
            except MalformedResponse as exc:
                return Sample(False, str(exc))
            for item in items:
                child = f"{node}/{quote(str(item.get('id')), safe='')}".lstrip("/")
                attributes = item.get("attributes") or {}
                family = attributes.get("structure_family")
                structure = attributes.get("structure") or {}
                if family == "array" and structure.get("shape"):
                    one = ",".join("0:1" for _ in structure["shape"])
                    url = f"/array/full/{child}"
                    params: dict[str, Any] = {"slice": one, "format": "application/octet-stream"}
                elif family == "table" and structure.get("columns"):
                    url = f"/table/partition/{child}"
                    params = {
                        "partition": 0,
                        "column": structure["columns"][0],
                        "format": "application/vnd.apache.arrow.file",
                    }
                else:
                    if family in {"container", "composite"} and depth < max_depth:
                        queue.append((child, depth + 1))
                    continue
                try:
                    data = self._http.get(url, params=params)
                except httpx.HTTPError as exc:
                    return Sample(False, type(exc).__name__)
                ok = data.status_code == 200 and len(data.content) > 0
                return Sample(ok, f"{family} {child}: HTTP {data.status_code}")
        return Sample(False, f"no array or table within {max_depth} levels")
