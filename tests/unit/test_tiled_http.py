"""Status mapping of the Tiled reader against stubbed upstream responses."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from event_timeline_tiled.adapter import create_app
from event_timeline_tiled.settings import Settings
from event_timeline_tiled.tiled_http import TiledReader

SEARCH_PAGE = {"data": [], "links": {"self": "x", "next": None}, "meta": {"count": 0}}
ROOT = {"library_version": "0.2.21"}


def _reader(
    search: Callable[[httpx.Request], httpx.Response],
    root: Callable[[httpx.Request], httpx.Response] = lambda _r: httpx.Response(200, json=ROOT),
    roots: tuple[str, ...] = (),
) -> tuple[Settings, TiledReader, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        if request.url.path.startswith("/api/v1/search"):
            return search(request)
        return root(request)

    settings = Settings(tiled_url="http://tiled.test", tiled_api_key="k", catalog_roots=roots)
    return settings, TiledReader(settings, transport=httpx.MockTransport(handler)), seen


def _health(settings: Settings, reader: TiledReader) -> list[dict[str, Any]]:
    with TestClient(create_app(settings, reader)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    rows: list[dict[str, Any]] = response.json()
    return rows


def _html(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="<html>maintenance</html>")


@pytest.mark.parametrize(
    "search, status, detail",
    [
        (lambda _r: httpx.Response(200, json=SEARCH_PAGE), "ok", ""),
        (_html, "error", "response is not JSON"),
        (lambda _r: httpx.Response(200, json=[1, 2]), "error", "response is not a JSON object"),
        (lambda _r: httpx.Response(200, json={"data": 1}), "error", "not a Tiled search page"),
        (lambda _r: httpx.Response(401), "unauthorised", "HTTP 401"),
        (lambda _r: httpx.Response(403), "forbidden", "HTTP 403"),
        (lambda _r: httpx.Response(404), "not_found", "HTTP 404"),
        (lambda _r: httpx.Response(503, text="<html>secret</html>"), "error", "HTTP 503"),
    ],
)
def test_search_status_mapping(
    search: Callable[[httpx.Request], httpx.Response], status: str, detail: str
) -> None:
    [row] = _health(*_reader(search)[:2])
    assert (row["status"], row["ok"]) == (status, status == "ok")
    assert detail in row["detail"]
    assert "<html>" not in str(row)


@pytest.mark.parametrize(
    "root",
    [
        _html,
        lambda _r: httpx.Response(200, json=["0.2.21"]),
        lambda _r: httpx.Response(200, json={"library_version": 3}),
        lambda _r: httpx.Response(500),
    ],
)
def test_bad_version_response_does_not_hide_root_rows(
    root: Callable[[httpx.Request], httpx.Response],
) -> None:
    ok = lambda _r: httpx.Response(200, json=SEARCH_PAGE)  # noqa: E731
    rows = _health(*_reader(ok, root, roots=("a", "b"))[:2])
    assert [(r["root"], r["status"], r["tiled_version"]) for r in rows] == [
        ("a", "ok", None),
        ("b", "ok", None),
    ]


def test_transport_failure_is_unreachable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    [row] = _health(*_reader(refuse, refuse)[:2])
    assert (row["status"], row["detail"]) == ("unreachable", "ConnectError")


def test_root_segments_are_encoded() -> None:
    settings, reader, seen = _reader(
        lambda _r: httpx.Response(200, json=SEARCH_PAGE), roots=("runs/a~b.c",)
    )
    _health(settings, reader)
    assert "/api/v1/search/runs/a~b.c" in [path.split("?")[0] for path in seen]


def _whoami(body: Any, status: int = 200) -> TiledReader:
    settings = Settings(tiled_url="http://tiled.test", tiled_api_key="abcdefgh-rest")
    transport = httpx.MockTransport(lambda _r: httpx.Response(status, json=body))
    return TiledReader(settings, transport=transport)


def test_whoami_reports_the_scopes_of_this_key_only() -> None:
    body = {
        "api_keys": [
            {"first_eight": "zzzzzzzz", "scopes": ["admin:apikeys"]},
            {"first_eight": "abcdefgh", "scopes": ["read:metadata", "read:data"]},
        ]
    }
    info = _whoami(body).whoami_key("abcdefgh")
    assert (info.status, info.scopes) == (200, ("read:metadata", "read:data"))


@pytest.mark.parametrize(
    "body, status, expect",
    [
        ({"api_keys": []}, 200, (200, None)),
        ([1], 200, (200, None)),
        ({}, 401, (401, None)),
    ],
)
def test_whoami_without_scopes(body: Any, status: int, expect: tuple[int, None]) -> None:
    info = _whoami(body, status).whoami_key("abcdefgh")
    assert (info.status, info.scopes) == expect


def test_check_access_flags_extra_scopes() -> None:
    from event_timeline_tiled.access_check import run_checks

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/whoami"):
            keys = [
                {"first_eight": "abcdefgh", "scopes": ["read:metadata", "read:data", "register"]}
            ]
            return httpx.Response(200, json={"api_keys": keys})
        return httpx.Response(200, json=SEARCH_PAGE)

    settings = Settings(tiled_url="http://tiled.test", tiled_api_key="abcdefgh-rest")
    lines: list[str] = []
    ok = run_checks(
        settings,
        [],
        [],
        TiledReader(settings, transport=httpx.MockTransport(handler)),
        lines.append,
    )
    assert not ok
    assert any(line.startswith("FAIL  key scopes") and "remove register" in line for line in lines)
    assert not any("abcdefgh-rest" in line for line in lines)

    # The single-user acknowledgement changes nothing on a multi-user server.
    lines.clear()
    reader = TiledReader(settings, transport=httpx.MockTransport(handler))
    assert not run_checks(settings, [], [], reader, lines.append, accept_single_user=True)
    assert any(line.startswith("NOTE  --accept-single-user-key ignored") for line in lines)
    assert any(line.startswith("FAIL  key scopes") for line in lines)
