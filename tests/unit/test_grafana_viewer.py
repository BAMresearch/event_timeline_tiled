"""deploy/grafana_viewer.py against a fake Grafana admin API: idempotent, a
Viewer only, and the passwords never printed."""

from __future__ import annotations

import base64
import importlib.util
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).parents[2] / "deploy" / "grafana_viewer.py"
ADMIN_PW, VIEWER_PW = "admin-secret", "cheese"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("grafana_viewer", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGrafana:
    """Users by login: password, org role, server admin. Grafana 13.3.1 refuses
    to set the password a user already has (HTTP 500)."""

    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []


@pytest.fixture
def grafana() -> Iterator[tuple[FakeGrafana, str]]:
    fake = FakeGrafana()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def _user(self) -> tuple[str, str]:
            login, _, password = (
                base64.b64decode(self.headers["Authorization"].split()[1]).decode().partition(":")
            )
            return login, password

        def _send(self, status: int, body: Any) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self) -> None:
            fake.calls.append((self.command, self.path))
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"null")
            login, password = self._user()
            if self.path == "/api/user":  # signed in as that user
                ok = fake.users.get(login, {}).get("password") == password
                return self._send(200 if ok else 401, {"login": login})
            if (login, password) != ("admin", ADMIN_PW):
                return self._send(401, {"message": "invalid username or password"})
            by_id = {u["id"]: u for u in fake.users.values()}
            parts = self.path.split("?")[0].strip("/").split("/")
            if self.path.startswith("/api/users/lookup"):
                name = self.path.split("loginOrEmail=")[1]
                if name in fake.users:
                    return self._send(200, {"id": fake.users[name]["id"]})
                return self._send(404, {"message": "user not found"})
            if self.path == "/api/admin/users":
                user = {"id": len(fake.users) + 2, "password": body["password"], "role": "Viewer",
                        "admin": False}  # fmt: skip
                fake.users[body["login"]] = user
                return self._send(200, {"id": user["id"]})
            user = by_id[int(parts[3] if parts[1] == "admin" else parts[4])]
            if parts[-1] == "password":
                if body["password"] == user["password"]:
                    return self._send(500, {"message": "Failed to update user password"})
                user["password"] = body["password"]
            elif parts[-1] == "permissions":
                user["admin"] = body["isGrafanaAdmin"]
            else:
                user["role"] = body["role"]
            return self._send(200, {"message": "ok"})

        do_GET = do_POST = do_PUT = do_PATCH = _handle

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield fake, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _files(tmp_path: Path, viewer: str) -> tuple[Path, Path]:
    admin, view = tmp_path / "admin.pw", tmp_path / "viewer.pw"
    admin.write_text(ADMIN_PW + "\n")
    view.write_text(viewer + "\n")
    for f in (admin, view):
        f.chmod(0o600)
    return admin, view


def test_creates_then_keeps_then_updates_a_viewer(
    grafana: tuple[FakeGrafana, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake, url = grafana
    module = _load()
    admin, view = _files(tmp_path, VIEWER_PW)
    args = ["--url", url, "--login", "mouse", "--password-file", str(view),
            "--admin-password-file", str(admin)]  # fmt: skip
    for expect in ("created", "kept the password of"):
        module.main(args)
        assert expect in capsys.readouterr().out
    view.write_text("mousy\n")
    module.main(args)
    out = capsys.readouterr().out
    assert "updated" in out
    assert fake.users["mouse"] == {"id": 2, "password": "mousy", "role": "Viewer", "admin": False}
    assert VIEWER_PW not in out and "mousy" not in out and ADMIN_PW not in out


def test_refuses_password_files_others_can_read(tmp_path: Path) -> None:
    module = _load()
    admin, view = _files(tmp_path, VIEWER_PW)
    view.chmod(0o640)
    with pytest.raises(SystemExit, match="readable by group or others"):
        module.main(["--url", "http://127.0.0.1:9", "--login", "mouse", "--password-file",
                     str(view), "--admin-password-file", str(admin)])  # fmt: skip
    with pytest.raises(SystemExit, match="must not be the admin"):
        module.main(["--login", "admin", "--password-file", str(view),
                     "--admin-password-file", str(admin)])  # fmt: skip
