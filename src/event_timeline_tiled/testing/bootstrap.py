"""Create the demo stack's service accounts and API keys in a multi-user Tiled.

Signs in as a Tiled admin (password from ``--admin-password-file`` or the
``TILED_ADMIN_PASSWORD`` environment variable), creates two service
principals and writes one API key per principal, each into its own
directory (``--key-dir NAME=DIR``, written as ``DIR/NAME.key``, mode 0600):

- ``seeder``: may create and write nodes (used only by ``ett-demo-seed``);
- ``adapter``: ``read:metadata`` and ``read:data`` only, like the lab server's.

With ``--owner UID:GID`` (run as root) the key directories and files, and
every ``--chown-dir``, are handed to that user, so the long-lived services
need not run as root.

Idempotent: a key file that still authenticates is kept. Demo use only.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

# Tiled's default "user" role lacks "register", which registering external
# files (processing-run HDF5 arrays) needs; only the "admin" role grants it.
ROLES = {"seeder": "admin", "adapter": "user"}
KEYS = {
    "seeder": [
        "read:metadata",
        "read:data",
        "write:metadata",
        "write:data",
        "create:node",
        "register",
    ],
    "adapter": ["read:metadata", "read:data"],
}


def wait_for(url: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if httpx.get(f"{url}/api/v1/", timeout=5).status_code == 200:
                return
        except httpx.TransportError:
            pass
        if time.monotonic() > deadline:
            raise SystemExit(f"Tiled at {url} did not become ready within {timeout_s:.0f} s")
        time.sleep(2)


def key_works(url: str, key: str) -> bool:
    r = httpx.get(
        f"{url}/api/v1/auth/whoami", headers={"Authorization": f"Apikey {key}"}, timeout=10
    )
    return r.status_code == 200


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--tiled-url", required=True)
    p.add_argument("--provider", default="toy")
    p.add_argument("--admin-user", default="admin")
    p.add_argument("--admin-password-file", type=Path)
    p.add_argument("--key-dir", action="append", required=True, metavar="NAME=DIR")
    p.add_argument("--owner", metavar="UID:GID")
    p.add_argument("--chown-dir", action="append", default=[], type=Path)
    p.add_argument("--wait", type=float, default=180)
    args = p.parse_args(argv)
    url = args.tiled_url.rstrip("/")
    dirs = {name: Path(d) for name, _, d in (k.partition("=") for k in args.key_dir)}
    if set(dirs) != set(KEYS):
        sys.exit(f"bootstrap: give --key-dir for exactly {', '.join(sorted(KEYS))}")
    owner = tuple(int(x) for x in args.owner.split(":")) if args.owner else None
    wait_for(url, args.wait)
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
    for d in [*dirs.values(), *args.chown_dir]:
        if owner:
            os.chown(d, *owner)

    todo = {
        name: scopes
        for name, scopes in KEYS.items()
        if not (
            (f := dirs[name] / f"{name}.key").exists() and key_works(url, f.read_text().strip())
        )
    }
    if not todo:
        print("bootstrap: keys present and valid")
        return
    if args.admin_password_file:
        password = args.admin_password_file.read_text(encoding="utf-8").strip()
    else:
        password = os.environ.get("TILED_ADMIN_PASSWORD", "")
    if not password:
        sys.exit("bootstrap: no admin password (--admin-password-file or TILED_ADMIN_PASSWORD)")
    token = httpx.post(
        f"{url}/api/v1/auth/provider/{args.provider}/token",
        data={"username": args.admin_user, "password": password},
        timeout=30,
    )
    if token.status_code != 200:
        sys.exit(f"bootstrap: admin sign-in failed (HTTP {token.status_code})")
    headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
    with httpx.Client(base_url=f"{url}/api/v1/auth", headers=headers, timeout=30) as http:
        for name, scopes in todo.items():
            principal = http.post("/principal", params={"role": ROLES[name]})
            principal.raise_for_status()
            key = http.post(
                f"/principal/{principal.json()['uuid']}/apikey",
                # Tiled 0.2.21 requires expires_in (null = no expiry).
                json={
                    "scopes": scopes,
                    "note": f"demo {name}",
                    "expires_in": None,
                    "access_tags": None,
                },
            )
            key.raise_for_status()
            path = dirs[name] / f"{name}.key"
            path.unlink(missing_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(key.json()["secret"] + "\n")
            if owner:
                os.chown(path, *owner)
            print(f"bootstrap: created {name} key with scopes {', '.join(scopes)}")
