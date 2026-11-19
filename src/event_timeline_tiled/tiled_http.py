"""Read-only HTTP access to Tiled: requests, paging, errors, health.

This module is the only place that sends requests to Tiled. It knows Tiled's
HTTP API but nothing about Bluesky; the Bluesky-in-Tiled layout and the
catalogue-specific query choices are in :mod:`event_timeline_tiled.layout`.

The API key is sent as ``Authorization: Apikey <key>`` and never in a URL.
Response bodies are never copied into health details: a wrong upstream or a
maintenance page is reported by status and a fixed description only.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import parse_qsl, quote, urlsplit

import httpx
from attrs import frozen
from tiled.query_registration import default_query_registry

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


class SourceError(Exception):
    """A request to Tiled failed. ``status`` is the health category; ``detail`` is
    a fixed description or an HTTP status, never a response body or a credential."""

    def __init__(self, status: HealthStatus, detail: str) -> None:
        super().__init__(f"{status.value}: {detail}")
        self.status = status
        self.detail = detail


class Query(Protocol):
    """A Tiled query object (``tiled.queries``)."""

    def encode(self) -> dict[str, Any]: ...


PAGE_LIMIT = 300
"""Tiled's maximum page size for searches (0.2.21 rejects 301)."""


def query_params(queries: Iterable[Query]) -> dict[str, list[str]]:
    """Encode Tiled query objects as search parameters, as Tiled's own client does."""
    params: dict[str, list[str]] = {}
    for query in queries:
        name = default_query_registry.query_type_to_name[type(query)]
        for field, value in query.encode().items():
            if value is not None:
                text = str(value).lower() if isinstance(value, bool) else str(value)
                params.setdefault(f"filter[{name}][condition][{field}]", []).append(text)
    return params


_HTTP_STATUS = {
    401: HealthStatus.UNAUTHORISED,
    403: HealthStatus.FORBIDDEN,
    404: HealthStatus.NOT_FOUND,
    # Gone: Tiled's answer to a broken link. (A registered HDF5 file that has
    # gone missing gives 500 in Tiled 0.2.21, not 410: review 06, finding 15.)
    410: HealthStatus.NOT_FOUND,
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

    def single_user(self) -> bool | None:
        """Whether Tiled runs in single-user mode: authentication required and no
        providers (one shared key, which can write). ``None`` if unknown. Never raises."""
        try:
            response = self._http.get("/")
            response.raise_for_status()
            auth = _json_object(response).get("authentication")
        except httpx.HTTPError, MalformedResponse:
            return None
        if not isinstance(auth, dict) or not isinstance(auth.get("providers"), list):
            return None
        return auth.get("required") is True and not auth["providers"]

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET ``/api/v1{path}`` and return the JSON object, or raise :class:`SourceError`."""
        try:
            response = self._http.get(path, params=params)
        except httpx.TransportError as exc:
            raise SourceError(HealthStatus.UNREACHABLE, type(exc).__name__) from None
        except httpx.HTTPError as exc:
            raise SourceError(HealthStatus.ERROR, type(exc).__name__) from None
        if response.status_code != 200:
            status = _HTTP_STATUS.get(response.status_code, HealthStatus.ERROR)
            raise SourceError(status, f"HTTP {response.status_code}")
        try:
            return _json_object(response)
        except MalformedResponse as exc:
            raise SourceError(HealthStatus.ERROR, str(exc)) from None

    def search(
        self,
        path: str,
        queries: Sequence[Query] = (),
        fields: Sequence[str] = ("metadata",),
        max_items: int | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """All entries of one container matching ``queries`` (AND), paged at
        :data:`PAGE_LIMIT`. Returns ``(items, truncated)``: at most ``max_items``
        items, and ``truncated`` true only if more matched. To tell "exactly
        ``max_items``" from "more", it asks for one item more than it returns.

        Tiled 0.2.21's ``links.next`` keeps only the paging parameters
        (``page[cursor]``, ``page[limit]``) and drops the filters and ``fields``.
        So each next page repeats this request's own parameters with just the
        ``page[...]`` parameters taken from that link, on this client's base URL.
        """
        url = f"/search/{tiled_path(path)}"
        params: dict[str, Any] = {
            "page[limit]": PAGE_LIMIT,
            "fields": list(fields) or "",
            **query_params(queries),
        }
        items: list[dict[str, Any]] = []
        while True:
            if max_items is not None:
                params["page[limit]"] = min(PAGE_LIMIT, max_items + 1 - len(items))
            body = self.get_json(url, params)
            data, links = body.get("data"), body.get("links")
            if not isinstance(data, list) or not isinstance(links, dict):
                raise SourceError(HealthStatus.ERROR, "response is not a Tiled search page")
            items.extend(item for item in data if isinstance(item, dict))
            if max_items is not None and len(items) > max_items:
                return items[:max_items], True
            next_link = links.get("next")
            if not next_link or not data:
                return items, False
            paging = {
                k: v
                for k, v in _multi(parse_qsl(urlsplit(str(next_link)).query)).items()
                if k.startswith("page[")
            }
            if not paging:
                raise SourceError(HealthStatus.ERROR, "next link without paging parameters")
            params = {k: v for k, v in params.items() if not k.startswith("page[")} | paging

    def get_bytes(self, path: str, params: dict[str, Any], max_bytes: int) -> bytes:
        """GET ``/api/v1{path}`` as a binary body of at most ``max_bytes``, or raise
        :class:`SourceError`. The body is read in pieces and refused once it is
        too long, so a wrong size estimate cannot exhaust memory."""
        try:
            with self._http.stream("GET", path, params=params) as response:
                if response.status_code != 200:
                    status = _HTTP_STATUS.get(response.status_code, HealthStatus.ERROR)
                    raise SourceError(status, f"HTTP {response.status_code}")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        raise SourceError(HealthStatus.ERROR, "response larger than expected")
                return bytes(body)
        except httpx.TransportError as exc:
            raise SourceError(HealthStatus.UNREACHABLE, type(exc).__name__) from None
        except httpx.HTTPError as exc:
            raise SourceError(HealthStatus.ERROR, type(exc).__name__) from None

    def table_column(self, path: str, column: str, max_bytes: int) -> list[Any]:
        """One whole column of a table node, as JSON of at most ``max_bytes``
        (the transfer is cut off beyond it: the server's read is not)."""
        raw = self.get_bytes(
            f"/table/full/{tiled_path(path)}",
            {"column": column, "format": "application/json"},
            max_bytes=max_bytes,
        )
        try:
            body = json.loads(raw)
        except ValueError:
            raise SourceError(HealthStatus.ERROR, "response is not JSON") from None
        values = body.get(column) if isinstance(body, dict) else None
        if not isinstance(values, list):
            raise SourceError(HealthStatus.ERROR, "table response lacks the requested column")
        return values

    def table_columns(self, path: str, columns: Sequence[str]) -> dict[str, list[Any]]:
        """Whole columns of a table node (Tiled cannot select rows)."""
        body = self.get_json(
            f"/table/full/{tiled_path(path)}",
            {"column": list(columns), "format": "application/json"},
        )
        if not all(isinstance(body.get(c), list) for c in columns):
            raise SourceError(HealthStatus.ERROR, "table response lacks requested columns")
        return {c: body[c] for c in columns}

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


def _multi(pairs: list[tuple[str, str]]) -> dict[str, list[str] | str]:
    """Collect repeated query parameters into lists."""
    out: dict[str, list[str] | str] = {}
    for key, value in pairs:
        if key in out:
            previous = out[key]
            out[key] = [*previous, value] if isinstance(previous, list) else [previous, value]
        else:
            out[key] = value
    return out
