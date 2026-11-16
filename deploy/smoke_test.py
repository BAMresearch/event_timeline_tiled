"""Smoke test of a running demo stack (CI and local). Standard library only.

    docker compose -f deploy/compose.yaml --profile demo up -d --build
    uv run --locked python deploy/smoke_test.py

Checks, through the published proxy and inside the containers:

1. Grafana answers, refuses anonymous API and image requests;
2. the provisioned health query (Grafana -> Infinity -> adapter -> Tiled)
   returns ``ok`` for every row;
3. the seeder has written runs, and ``ett-check-access`` passes for one of
   them inside the adapter container (key file readable, read-only scopes,
   real data readable);
4. the adapter's key is refused a valid node-create request (this is the
   disposable demo Tiled, so a wrongly privileged key would only write there).

Prints statuses only, never credentials. Exit status 0 if all pass.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).parent
COMPOSE = ["docker", "compose", "-f", str(HERE / "compose.yaml"), "--profile", "demo"]


def env_file() -> dict[str, str]:
    values = {}
    for line in (HERE / ".env").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


ENV = env_file()
BASE = f"http://127.0.0.1:{ENV.get('ETT_HTTP_PORT', '8080')}"
AUTH = "Basic " + base64.b64encode(f"admin:{ENV['GF_ADMIN_PASSWORD']}".encode()).decode()


def http(path: str, body: dict[str, object] | None = None, auth: bool = True) -> tuple[int, bytes]:
    request = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET")
    if auth:
        request.add_header("Authorization", AUTH)
    if body is not None:
        request.add_header("Content-Type", "application/json")
        request.data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except OSError:  # URLError, connection errors and timeouts
        return 0, b""


def in_adapter(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*COMPOSE, "exec", "-T", "adapter", *command], capture_output=True, text=True, timeout=120
    )


def wait(what: str, check: Callable[[], object], timeout_s: float = 300) -> None:
    deadline = time.monotonic() + timeout_s
    while not check():
        if time.monotonic() > deadline:
            sys.exit(f"FAIL  timed out waiting for {what}")
        time.sleep(5)
    print(f"ok    {what}")


def health_rows() -> list[dict[str, object]]:
    query = {
        "queries": [
            {
                "refId": "A",
                "datasource": {"uid": "timeline-adapter"},
                "type": "json",
                "source": "url",
                "parser": "backend",
                "format": "table",
                "url": "http://adapter:8000/health",
            }
        ],
        "from": "now-1h",
        "to": "now",
    }
    status, body = http("/api/ds/query", query)
    if status != 200:
        return []
    frames = json.loads(body)["results"]["A"].get("frames", [])
    if not frames:
        return []
    names = [field["name"] for field in frames[0]["schema"]["fields"]]
    columns = frames[0]["data"]["values"]
    return [dict(zip(names, row, strict=True)) for row in zip(*columns, strict=True)]


# The probe runs inside the adapter container with the adapter's own key file;
# the request body follows Tiled 0.2.21's PostMetadataRequest.
WRITE_PROBE = """
import httpx
key = open("/secrets/adapter.key").read().strip()
r = httpx.post("http://tiled:8000/api/v1/metadata/",
               headers={"Authorization": "Apikey " + key},
               json={"structure_family": "container", "metadata": {}, "specs": []})
print(r.status_code)
"""

FIRST_RUN = """
import httpx
key = open("/secrets/adapter.key").read().strip()
r = httpx.get("http://tiled:8000/api/v1/search/", params={"page[limit]": 1, "fields": ""},
              headers={"Authorization": "Apikey " + key})
data = r.json()["data"] if r.status_code == 200 else []
print(data[0]["id"] if data else "")
"""


def main() -> None:
    failures = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"{'ok  ' if ok else 'FAIL'}  {name}{f': {detail}' if detail else ''}")
        if not ok:
            failures.append(name)

    wait("Grafana via the proxy", lambda: http("/api/health", auth=False)[0] == 200)
    status = http("/api/dashboards/uid/ett-health", auth=False)[0]
    check("anonymous dashboard API refused", status == 401, f"HTTP {status}")
    status = http("/run-images/anything", auth=False)[0]
    check("anonymous image route refused", status == 401, f"HTTP {status}")

    wait("health ok through Grafana", lambda: (rows := health_rows()) and all(
        row.get("status") == "ok" for row in rows
    ))  # fmt: skip

    run = ""

    def seeded() -> bool:
        nonlocal run
        result = in_adapter("python", "-c", FIRST_RUN)
        run = result.stdout.strip() if result.returncode == 0 else ""
        return bool(run)

    wait("seeded runs readable with the adapter key", seeded)
    result = in_adapter("ett-check-access", "--run", run, "--denied", "no-such-node")
    lines = result.stdout.strip().splitlines() or [result.stderr.strip()[-200:]]
    check("ett-check-access in the adapter container", result.returncode == 0, lines[-1])
    result = in_adapter("python", "-c", WRITE_PROBE)
    status_text = result.stdout.strip()
    check("adapter key refused a write", status_text in {"401", "403"}, f"HTTP {status_text}")

    if failures:
        sys.exit(f"{len(failures)} smoke check(s) failed: {', '.join(failures)}")
    print("all smoke checks passed")


if __name__ == "__main__":
    main()
