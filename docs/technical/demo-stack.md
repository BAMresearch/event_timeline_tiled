# Demo stack

Everything runs locally, without the lab network: a disposable multi-user
Tiled on PostgreSQL, filled with synthetic runs, behind the same proxy,
Grafana and adapter as a real deployment.

```sh
cp deploy/.env.example deploy/.env      # replace every "change-me"
docker compose -f deploy/compose.yaml --profile demo up -d --build
```

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

Everything long-lived runs as uid 10001. Docker Compose only: the proxy
relies on Docker's embedded DNS, and Podman is untested.

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

## Against another Tiled

Leave out `--profile demo`. In `deploy/.env` set `ETT_TILED_URL` and
`ETT_CATALOG_ROOTS`, and install the read-only key as described in
[Tiled access](tiled-access.md).
