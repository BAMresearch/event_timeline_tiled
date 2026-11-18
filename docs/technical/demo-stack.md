# Demo stack

Everything runs locally, without the lab network: a disposable multi-user
Tiled on PostgreSQL, filled with synthetic runs, behind the same proxy,
Grafana and adapter as a real deployment.

```sh
cp deploy/.env.example deploy/.env      # replace every "change-me"
docker compose -f deploy/compose.yaml --profile demo up -d --build
```

Open <http://localhost:8080>, sign in as `admin` (password `GF_ADMIN_PASSWORD`
from `deploy/.env`) and open the **Timeline** dashboard; the history appears
within about two minutes of the first start.

| Service | What it does |
|---|---|
| `proxy` | nginx on `127.0.0.1:8080`: Grafana, plus `/run-images/` for signed-in users |
| `grafana` | Grafana 13.3.1 with the pinned plugins; dashboards from `grafana/` |
| `adapter` | Reads Tiled with the read-only key from `/secrets/adapter.key` |
| `postgres` | Tiled's authentication database, catalogue and event tables |
| `tiled` | Tiled 0.2.21, multi-user (toy password provider), on `127.0.0.1:8001` |
| `demo-bootstrap` | One-shot, as root: creates two service keys and hands the key and processed volumes to uid 10001 |
| `demo-seed` | Writes a day of history, then keeps acquiring live MOUSE runs |
| `demo-operando` | Keeps a generic instrument's three-day run open, a reading every 15 s |
| `redis`, `re-manager` | A real RE Manager with simulated devices, for the queue panel |
| `demo-queue` | Keeps that RE Manager busy with `demo_measurement` items (demo only) |

Everything long-lived runs as uid 10001. The stack runs with Docker or with
rootless Podman (below); the proxy takes its DNS server from the container's
`/etc/resolv.conf` at start, which is Docker's embedded DNS or Podman's
aardvark-dns.

## Keys

`demo-bootstrap` creates two service principals and writes each key into its
own volume (mode 0600, owner 10001), so the adapter never sees the seeder's
key:

- `adapter.key`: scopes `read:metadata`, `read:data` only, like the lab
  server's key for the adapter. Creating nodes with it is refused.
- `seeder.key`: may create nodes, write and register external files. Tiled's
  default `user` role lacks `register`, which registering the processing
  runs' HDF5 files needs, so the seeder principal has the `admin` role. On the
  lab server, MoDaCor's publisher will need the same scope.

## What the seeder writes

- A day of MOUSE-like history ending at start-up: measurements of 8–13 minutes
  (`baseline`, `snapshot`, three `mouse_eiger_measure` readings), an
  `apply_config` run before every fourth, one aborted measurement, one vacuum
  run, and a processing run after each successful measurement (one in eight
  fails).
- Live, through the RunEngine as in the history: one MOUSE measurement at a
  time (`baseline`, `snapshot`, three `mouse_eiger_measure` readings a
  minute apart), each followed by a processing run (one in six fails).
- A generic second instrument (`demo-operando`, its own RunEngine) with a
  run that started three days ago and stays open, receiving a reading every
  15 s (an operando-style experiment).

History is written only if the catalogue holds no MOUSE runs, so restarts
do not duplicate it; each restart of `demo-operando` opens a new run. The
three Tiled secrets in `deploy/.env` must be at least 16 characters of
letters, digits, `-` and `_` (Tiled refuses to start otherwise). `down -v`
discards everything.

## Podman

The lab runs rootless Podman. Docker's compose tool works with it through
Podman's Docker-compatible API; Docker itself is not needed. CI runs the
stack and the smoke test this way (job `stack-podman`: Podman 4.9.3 on
Ubuntu 24.04).

```sh
sudo apt install docker-compose-v2               # the compose tool only (Ubuntu 24.04)
systemctl --user enable --now podman.socket      # Podman's API, for this user
export DOCKER_HOST=unix:///run/user/$(id -u)/podman/podman.sock
export DOCKER_BUILDKIT=0                         # Podman serves the classic build API
docker compose -f deploy/compose.yaml --profile demo up -d --build
```

- Run it under the account that should own the stack, and enable lingering
  for it (`sudo loginctl enable-linger <user>`), or the containers stop when
  its last session ends.
- Images are named in full (`docker.io/library/nginx:…`), so Podman needs
  no unqualified-search registries.
- Health checks are `CMD-SHELL` strings or commands without spaces in their
  arguments: Podman's Docker-compatible API (4.9) splits a `CMD` check's
  arguments at spaces.
- `podman compose` (Podman 4.7 and later) calls the same compose tool, and
  `podman-compose` is untested.
- The demo's services wait for each other's health checks, which rootless
  Podman runs through systemd user timers: without a user session they
  never run. The services of a real deployment (proxy, Grafana, adapter) do
  not wait on health checks.
- Tests start their PostgreSQL with `docker` or `podman`, whichever answers
  (`ETT_CONTAINER_CLI` chooses).

## Against another Tiled

Leave out `--profile demo`. In `deploy/.env` set `ETT_TILED_URL` and
`ETT_CATALOG_ROOTS`, and install the read-only key as described in
[Tiled access](tiled-access.md).
