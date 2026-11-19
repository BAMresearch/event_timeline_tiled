"""Create or update a Grafana Viewer account: one-off and idempotent.

For an operator screen (docs/technical/single-host.md): the account can only
view dashboards. Run it on the host, against the running stack:

    python3 deploy/grafana_viewer.py --login mouse \\
        --password-file ~/.config/mouse-dashboard/grafana-viewer.password \\
        --admin-password-file ~/.config/mouse-dashboard/grafana-admin.password

Through Grafana's admin API (Grafana 13.3.1), signed in as the Grafana admin:
creates the user if the login is new, else sets its password; then makes it a
Viewer of the main organisation and not a Grafana server admin. Both passwords
are read from files that only their owner may read (mode 600); they are sent
only in request bodies and the Authorization header, never printed. Without
Grafana's password policy (this stack leaves it off), a password needs 4
characters at least. Standard library only. Exit status 0 on success.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

MAIN_ORG = 1


def secret(path: Path) -> str:
    """The file's text without its line ending; refuses a file others may read."""
    try:
        info = path.stat()
    except OSError as exc:
        sys.exit(f"FAIL  {path}: {exc.strerror}")
    if not stat.S_ISREG(info.st_mode):
        sys.exit(f"FAIL  {path}: not a regular file")
    if info.st_mode & 0o077:
        sys.exit(f"FAIL  {path}: readable by group or others (chmod 600 {path})")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        sys.exit(f"FAIL  {path}: not owned by this user")
    value = path.read_text(encoding="utf-8").rstrip("\r\n")
    if not value:
        sys.exit(f"FAIL  {path}: empty")
    return value


class Grafana:
    def __init__(self, url: str, user: str, password: str) -> None:
        self.url = url.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self._auth = f"Basic {token}"

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.url + path, data=data, method=method)
        request.add_header("Authorization", self._auth)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            try:
                answer = json.loads(exc.read() or b"null")
            except ValueError:
                answer = None
            return exc.code, answer
        except urllib.error.URLError as exc:
            sys.exit(f"FAIL  Grafana at {self.url}: {exc.reason}")


def _message(answer: Any) -> str:
    return str(answer.get("message", "")) if isinstance(answer, dict) else ""


def ensure_viewer(grafana: Grafana, login: str, name: str, password: str) -> str:
    """Create or update ``login`` as a Viewer; returns what was done."""

    def need(ok: bool, what: str, status: int, answer: Any) -> None:
        if not ok:
            hint = " (is the admin password the one Grafana started with?)" if status == 401 else ""
            sys.exit(f"FAIL  {what}: HTTP {status} {_message(answer)}{hint}")

    query = urllib.parse.urlencode({"loginOrEmail": login})
    status, found = grafana.call("GET", f"/api/users/lookup?{query}")
    need(status in (200, 404), "look up the user", status, found)
    if status == 404:
        status, made = grafana.call(
            "POST", "/api/admin/users", {"name": name, "login": login, "password": password}
        )
        need(status == 200 and isinstance(made, dict), "create the user", status, made)
        user_id, done = made["id"], "created"
    else:
        user_id, done = found["id"], "updated"
        # Grafana 13.3.1 answers 500 to setting the password it already has.
        if Grafana(grafana.url, login, password).call("GET", "/api/user")[0] == 200:
            done = "kept the password of"
        else:
            status, answer = grafana.call(
                "PUT", f"/api/admin/users/{user_id}/password", {"password": password}
            )
            need(status == 200, "set the password", status, answer)
    status, answer = grafana.call(
        "PUT", f"/api/admin/users/{user_id}/permissions", {"isGrafanaAdmin": False}
    )
    need(status == 200, "drop server admin rights", status, answer)
    status, answer = grafana.call(
        "PATCH", f"/api/orgs/{MAIN_ORG}/users/{user_id}", {"role": "Viewer"}
    )
    if status == 404:  # not in the main organisation yet
        status, answer = grafana.call(
            "POST", f"/api/orgs/{MAIN_ORG}/users", {"loginOrEmail": login, "role": "Viewer"}
        )
    need(status == 200, "make the user a Viewer", status, answer)
    return done


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    port = os.environ.get("ETT_HTTP_PORT", "8080")
    p.add_argument("--url", default=f"http://127.0.0.1:{port}",
                   help="the stack (default: http://127.0.0.1:$ETT_HTTP_PORT, 8080)")  # fmt: skip
    p.add_argument("--login", required=True, help="the Viewer's login name")
    p.add_argument("--name", help="its display name (default: the login)")
    p.add_argument("--password-file", required=True, type=Path)
    p.add_argument("--admin-user", default="admin")
    p.add_argument("--admin-password-file", required=True, type=Path)
    args = p.parse_args(argv)
    if args.login == args.admin_user:
        sys.exit("FAIL  the Viewer must not be the admin account")
    password = secret(args.password_file.expanduser())
    grafana = Grafana(args.url, args.admin_user, secret(args.admin_password_file.expanduser()))
    done = ensure_viewer(grafana, args.login, args.name or args.login, password)
    print(f"ok    {done} Grafana user {args.login!r}: Viewer, no server admin rights")


if __name__ == "__main__":
    main()
