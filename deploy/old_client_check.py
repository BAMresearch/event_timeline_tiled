"""MOUSE's Tiled client (0.2.8, with Bluesky 1.14.6) against the demo stack's Tiled.

    uv run --locked python deploy/old_client_check.py

With the demo stack running: writes a MOUSE-like measurement, an open run and
a processing run with registered HDF5 arrays using MOUSE's locked client
versions (in a throwaway environment inside the demo-seed image), then reads
them back through the adapter and ``ett-check-access``. Exit status 0 if all
three are found with the expected status and readable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
COMPOSE = ["docker", "compose", "-f", str(HERE / "compose.yaml"), "--profile", "demo"]
CLIENT = [
    "tiled[client]==0.2.8",
    "bluesky==1.14.6",
    "ophyd==1.11.0",
    "event-model==1.23.1",
    "h5py",
    "attrs",
    "numpy",
]
READ_BACK = """
import httpx, json, sys, time
runs = json.loads(sys.argv[1]); n = time.time()
clips = httpx.get("http://localhost:8000/clips", params={"from": n - 3600, "to": n}).json()["clips"]
by_uid = {c["uid"]: c for c in clips}
print(json.dumps({k: by_uid.get(v, {}).get("status") for k, v in runs.items()}))
"""


def main() -> None:
    write = subprocess.run(
        [*COMPOSE, "run", "--rm", "--no-deps", "-T",
         "-v", f"{HERE / 'checks'}:/check:ro", "-e", "PYTHONPATH=/src/src",
         "-e", "UV_CACHE_DIR=/tmp/uv", "--entrypoint", "uv", "demo-seed",
         "run", "-q", "--isolated", "--no-project", "--python", "/usr/local/bin/python3",
         *[arg for package in CLIENT for arg in ("--with", package)],
         "python", "/check/old_client_write.py"],
        capture_output=True, text=True, timeout=900,
    )  # fmt: skip
    if write.returncode:
        sys.exit(f"FAIL  writing with the 0.2.8 client: {write.stderr.strip()[-500:]}")
    runs = json.loads(write.stdout.strip().splitlines()[-1])
    print("ok    written with tiled 0.2.8 / bluesky 1.14.6")
    statuses = json.loads(
        subprocess.run(
            [*COMPOSE, "exec", "-T", "adapter", "python", "-c", READ_BACK, json.dumps(runs)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout  # fmt: skip
    )
    expected = {"closed": "success", "open": "open", "processing": "success"}
    check = subprocess.run(
        [*COMPOSE, "exec", "-T", "adapter", "ett-check-access",
         *[arg for uid in runs.values() for arg in ("--run", uid)]],
        capture_output=True, text=True,
    )  # fmt: skip
    print(f"{'ok  ' if statuses == expected else 'FAIL'}  found by the adapter: {statuses}")
    last = check.stdout.strip().splitlines()[-1] if check.stdout.strip() else check.stderr
    print(f"{'ok  ' if check.returncode == 0 else 'FAIL'}  ett-check-access: {last}")
    if statuses != expected or check.returncode:
        sys.exit(1)


if __name__ == "__main__":
    main()
