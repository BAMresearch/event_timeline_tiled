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
5. every panel query of the Timeline and Overview dashboards (collapsed rows included)
   returns without error with no run selected, with a MOUSE measurement and
   with the generic instrument's run selected; the timeline frame has clips,
   Data status has a row, the generic run has a time series, and the MOUSE
   measurement has related runs and a corrected curve.
6. signed in through Grafana's login, the browser's image route returns the
   MOUSE measurement's corrected image as a PNG.
7. the demo RE Manager is reachable through /queues (read-only).

Prints statuses only, never credentials. Exit status 0 if all pass.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).parent
# ETT_COMPOSE: the compose command ("docker compose", or "docker-compose" for the
# standalone binary); ETT_COMPOSE_FILES: colon-separated files below deploy/
# (CI's single-host job adds compose.host.yaml and ci/compose.host-demo.yaml).
COMPOSE = [
    *os.environ.get("ETT_COMPOSE", "docker compose").split(),
    *(arg for f in os.environ.get("ETT_COMPOSE_FILES", "compose.yaml").split(":")
      for arg in ("-f", str(HERE / f))),
    "--profile",
    "demo",
]  # fmt: skip


def env_file() -> dict[str, str]:
    values = {}
    for line in (HERE / ".env").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


ENV = env_file()
PORT = ENV.get("ETT_HTTP_PORT", "8080")
BASE = f"http://127.0.0.1:{PORT}"
SESSION_COOKIE = f"grafana_session_{PORT}"  # one per stack (compose.yaml)
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


def session_cookie() -> str:
    """A Grafana session cookie, as a browser gets it by signing in."""
    request = urllib.request.Request(BASE + "/login", method="POST")
    request.add_header("Content-Type", "application/json")
    request.data = json.dumps({"user": "admin", "password": ENV["GF_ADMIN_PASSWORD"]}).encode()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            cookies = response.headers.get_all("Set-Cookie") or []
    except OSError:
        return ""
    return "; ".join(c.split(";", 1)[0] for c in cookies if c.startswith("grafana_session"))


def image(path: str, cookie: str) -> tuple[int, str, bytes]:
    request = urllib.request.Request(BASE + path)
    request.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, "", b""
    except OSError:
        return 0, "", b""


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
                "url": "/health",
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
import os
key = open(os.environ["ETT_TILED_API_KEY_FILE"]).read().strip()
tiled = os.environ["ETT_TILED_URL"].rstrip("/")
r = httpx.post(tiled + "/api/v1/metadata/",
               headers={"Authorization": "Apikey " + key},
               json={"structure_family": "container", "metadata": {}, "specs": []})
print(r.status_code)
"""

SAMPLE_RUNS = """
import httpx
import os
key = open(os.environ["ETT_TILED_API_KEY_FILE"]).read().strip()
tiled = os.environ["ETT_TILED_URL"].rstrip("/")
r = httpx.get(tiled + "/api/v1/search/", params={"page[limit]": 300, "fields": "metadata"},
              headers={"Authorization": "Apikey " + key})
data = r.json()["data"] if r.status_code == 200 else []
def first(test):
    for item in data:
        start = (item["attributes"]["metadata"] or {}).get("start") or {}
        if test(start):
            return item["id"]
    return ""
print(first(lambda s: s.get("instrument") == "MOUSE" and s.get("sampleid")),
      first(lambda s: s.get("instrument") == "generic"))
"""


def panel_queries(
    run: str, stream: str, dashboard: str = "ett-timeline"
) -> list[tuple[str, int, str]]:
    """(panel title, rows, error) for every query of the timeline dashboard,
    with ``run`` and ``stream`` as the selection (empty: none)."""
    status, body = http(f"/api/dashboards/uid/{dashboard}")
    if status != 200:
        return [("dashboard", 0, f"HTTP {status}")]
    now = time.time()
    out = []
    panels = json.loads(body)["dashboard"]["panels"]
    for panel in [*panels, *(p for row in panels for p in row.get("panels", []))]:
        for target in panel.get("targets", []):
            url = target["url"]
            for name, value in (("run", run), ("stream", stream), ("root", ""), ("lane", ""),
                                ("unfold", ""), ("preset", "instruments")):  # fmt: skip
                url = url.replace(f"${{{name}:percentencode}}", value)
            # The browser's zone, which Grafana's frontend fills in.
            url = url.replace("${__timezone}", "Europe/Berlin")
            query = dict(target, url=url)
            window = {"from": str(int((now - 86400) * 1000)), "to": str(int(now * 1000))}
            status, body = http("/api/ds/query", {"queries": [query], **window})
            if status != 200:
                out.append((panel["title"], 0, f"HTTP {status}"))
                continue
            result = json.loads(body)["results"][target.get("refId", "A")]
            frames = result.get("frames", [])
            rows = sum(len(f["data"]["values"][0]) for f in frames if f["data"]["values"])
            out.append((panel["title"] or "notice", rows, (result.get("error") or "")[:120]))
    return out


QUEUES = """
import httpx
rows = httpx.get("http://127.0.0.1:8000/queues", timeout=30).json()
print(rows[0]["state"] if rows else "no RE Manager configured")
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

    run, generic = "", ""

    def seeded() -> bool:
        nonlocal run, generic
        result = in_adapter("python", "-c", SAMPLE_RUNS)
        found = result.stdout.split() if result.returncode == 0 else []
        run, generic = found if len(found) == 2 else ("", "")
        return bool(run and generic)

    wait("seeded runs readable with the adapter key", seeded)
    result = in_adapter("ett-check-access", "--run", run, "--denied", "no-such-node")
    lines = result.stdout.strip().splitlines() or [result.stderr.strip()[-200:]]
    check("ett-check-access in the adapter container", result.returncode == 0, lines[-1])
    result = in_adapter("python", "-c", WRITE_PROBE)
    status_text = result.stdout.strip()
    check("adapter key refused a write", status_text in {"401", "403"}, f"HTTP {status_text}")

    wait(
        "queue status of the demo RE Manager",
        lambda: in_adapter("python", "-c", QUEUES).stdout.strip() in {"idle", "executing_queue"},
        timeout_s=180,
    )

    for name, selection in [
        ("no run selected", ("", "")),
        ("MOUSE run selected", (run, "baseline")),
        ("generic run selected", (generic, "primary")),
    ]:
        results = panel_queries(*selection)
        errors = [f"{title}: {error}" for title, _, error in results if error]
        detail = "; ".join(errors) or f"{len(results)} queries"
        check(f"timeline dashboard queries, {name}", not errors, detail)
        rows: dict[str, int] = {}
        for title, n, _ in results:  # a panel may have several queries (two y axes)
            rows[title] = rows.get(title, 0) + n
        if name == "no run selected":
            clips = rows.get("Timeline", 0)
            check("timeline frame has clips", clips > 0, f"{clips} clips")
            check("data status has a row", rows.get("Data status", 0) > 0)
        if name == "generic run selected":
            series = rows.get("Run time series", 0)
            check("generic run has a time series", series > 0, f"{series} points")
        if name == "MOUSE run selected":
            related = rows.get("Related runs", 0)
            check("MOUSE run has related runs", related > 0, f"{related} rows")
            curve = rows.get("Corrected curve", 0)
            check("MOUSE run has a corrected curve", curve > 0, f"{curve} points")
        overview = panel_queries(*selection, dashboard="ett-overview")
        errors = [f"{title}: {error}" for title, _, error in overview if error]
        detail = "; ".join(errors) or f"{len(overview)} queries"
        check(f"overview dashboard queries, {name}", not errors, detail)
        latest = sum(n for title, n, _ in overview if title == "Latest runs on the lane")
        check(f"overview lists the latest runs, {name}", latest > 0, f"{latest} rows")

    cookie = session_cookie()
    names = [part.split("=", 1)[0] for part in cookie.split("; ") if part]
    check("signed in through Grafana's login", SESSION_COOKIE in names, ", ".join(names))
    status, kind, body = image(f"/run-images/{run}/image?size=128", cookie)
    check("corrected image through the signed-in route",
          status == 200 and kind == "image/png" and body.startswith(b"\x89PNG"),
          f"HTTP {status}, {kind or 'no type'}, {len(body)} bytes")  # fmt: skip

    if failures:
        sys.exit(f"{len(failures)} smoke check(s) failed: {', '.join(failures)}")
    print("all smoke checks passed")


if __name__ == "__main__":
    main()
