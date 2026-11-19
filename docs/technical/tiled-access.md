# Tiled access for the timeline

Written for whoever administers the lab Tiled server and deploys the
timeline. Checked against Tiled 0.2.21 in multi-user mode; the demo stack
does the same steps automatically (`ett-demo-bootstrap`).

## Who needs what

| Account | Needs | Must not have |
|---|---|---|
| **Timeline adapter** (one service principal per deployment) | read run metadata and data under the configured catalogue roots | any write, create, delete, register or admin scope |
| Grafana | nothing in Tiled: it only talks to the adapter | any Tiled credential |
| Operators (Grafana Viewer accounts) | nothing in Tiled | — |
| MoDaCor's publisher (later, not this project) | create nodes, write, `register` external files, in its own container | read/write elsewhere |

The adapter's Tiled credential is the only one this project uses. It stays
on the adapter's host; Grafana and browsers never see it.

## What limits the key

Tiled grants the **intersection** of three things: the scopes on the API key,
the principal's role, and (if the server has one) the access policy.

- **Scopes** stop the key from writing, whatever the role allows: with only
  `read:metadata` and `read:data` it can never create, write, register or
  administer.
- **Without an access policy**, those two scopes let the key read *the whole
  server*, not only the timeline's runs. `ETT_CATALOG_ROOTS` is the adapter's
  configuration, not a permission. If the server holds anything the timeline
  host should not read, the server needs an access policy.
- **With an access policy** (for example Tiled's tag-based policy), the
  administrator grants this principal read access to the containers in
  `ETT_CATALOG_ROOTS` and nothing else, in that policy's own configuration.
  Where the policy uses access tags, the key can be narrowed further with
  `access_tags=[...]` in `create_api_key`. A search can then succeed while
  returning fewer runs, so check real runs, not only the search (below).

## 1. Prepare the key's directory (adapter host)

The adapter runs as uid **10001**, group **10001**, in its container. On a
Linux host with ordinary (rootful) Docker, give that group read access to a
directory that nobody else can enter. The group needs no name on the host.

```sh
sudo install -d -m 750 -o "$USER" -g 10001 /srv/event-timeline/secrets
```

Directory `750` and file `640`, both group 10001: the deployment user
writes, the adapter reads, other users get nothing. The adapter needs to
*enter* the directory, so `700` on the directory fails even if the file is
readable (tested on Linux; Docker Desktop on macOS hides this).

Rootless Docker maps container ids into the user's sub-id range, so the
group is the mapped one (`subgid start + 10000`, from `/etc/subgid`), not
10001. This has not been tested here.

## 2. Create the principal and key (Tiled admin)

Sign in as a Tiled admin, create a service principal with the ordinary
`user` role, and give it a key with the two read scopes. The secret is
written straight into a file that only you can read; it is never printed.

```python
import os
from tiled.client import from_uri

c = from_uri("https://tiled.lab.example")      # the lab server's URL
c.login()                                      # as a Tiled admin
admin = c.context.admin

principal = admin.create_service_principal(role="user")
key = admin.create_api_key(
    principal["uuid"],
    scopes=["read:metadata", "read:data"],
    expires_in=365 * 24 * 3600,                # one year; or None for no expiry
    note="event-timeline-tiled adapter",
)
fd = os.open("adapter.key", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(key["secret"] + "\n")
print(principal["uuid"], key["first_eight"])   # keep these for rotation
```

Use a new principal for each deployment (lab operator screen, a beamline
stack), so each can be revoked on its own. If the server has an access
policy, grant the principal its read access now.

## 3. Install it (adapter host)

Move the file into the prepared directory and hand it to the adapter's group:

```sh
mv adapter.key /srv/event-timeline/secrets/adapter.key
sudo chgrp 10001 /srv/event-timeline/secrets/adapter.key
chmod 640 /srv/event-timeline/secrets/adapter.key
```

and point the stack at it in `deploy/.env`:

```sh
ETT_TILED_URL=https://tiled.lab.example
ETT_CATALOG_ROOTS=            # the containers holding runs; empty = server root
ETT_SECRETS=/srv/event-timeline/secrets
```

The directory is mounted read-only at `/secrets`. Never put the key in
`.env`, a dashboard, a URL, a command line or a log.

On a [single host](single-host.md) under rootless Podman, the adapter
runs as the host user instead, and reads a mode-600 file owned by that user
from wherever `ETT_TILED_KEY_FILE` points.

## 4. Check it before use

`ett-check-access` runs in the adapter's image with the adapter's settings,
so it also proves that the container can read the key file. It is
read-only: it does **not** try to write (a wrongly privileged key would then
really write); it checks the key's scopes as Tiled reports and enforces
them, then reads.

```sh
docker compose -f deploy/compose.yaml run --rm --no-deps adapter \
  ett-check-access --run <a run UID or path> [--run ...] [--denied <path>]
```

| Check | Passes when |
|---|---|
| key accepted | Tiled accepts the key (a `PermissionError` on the key file means step 1 or 3 is wrong) |
| key scopes | the key holds exactly `read:metadata` and `read:data` |
| search, per catalogue root | the root can be searched |
| `--run`: metadata and data | the run's metadata and a small piece of real data (the first table or array below it) can be read |
| `--denied` | the node cannot be read (`401`, `403` or `404`) |

A **single-user** Tiled (authentication required, no providers, no
`/auth/whoami`) has one key, which can write; `key scopes` fails there.
Until the server runs in multi-user mode, a deployment may knowingly use
that key: `--accept-single-user-key` turns the failure into a pass that
says the key can write. It is off by default, and ignored (with a note) on
a multi-user server, where the scopes are checked as always.

Give `--run` one recent measurement and one processing run from each
catalogue root. With an access policy, also give `--denied` a run the
timeline must *not* see (for example from another group's container): it
proves the policy, not just the key, is in effect. Exit status 0 means
every check passed. After deployment, the **Timeline health** dashboard
shows `ok` for each root.

## 5. Rotate or revoke

1. Create a new key for the same principal (step 2, `create_api_key` with
   the principal's UUID).
2. Replace `adapter.key` in the directory (step 3) and run
   `docker compose -f deploy/compose.yaml restart adapter`. The *directory*
   is mounted, so the container sees the new file even when it was replaced
   by a rename; do not mount the single file instead.
3. Run the check (step 4), then revoke the old key:
   `admin.revoke_api_key(principal_uuid, first_eight="<old first eight>")`.

To cut the timeline off immediately, revoke the key; the health dashboard
then shows `unauthorised`.

## Exposing the timeline beyond this host

The demo publishes Grafana on `127.0.0.1` only, over plain HTTP. Before
operators open it from other machines, a deployment needs a TLS terminator
in front of the proxy, Grafana's public URL and `cookie_secure` set to match,
and operator accounts with the Viewer role. This is specified when the first
lab deployment is set up (plan §10.2); until then, keep the loopback binding.

## MoDaCor's publisher (later)

Processing runs register their HDF5 files as external data, which needs the
`register` scope. Tiled's default roles grant `register` only to `admin`.
Give MoDaCor its own principal, preferably with a custom role holding
`read:metadata`, `read:data`, `create:node`, `write:metadata`, `write:data`
and `register`; otherwise the `admin` role with a key limited to those scopes
(a scoped key does not inherit the role's key-management scopes), and an
access policy confining it to the processing container. The Tiled server
must also be able to read MoDaCor's output directory (listed in
`readable_storage`). See [processing runs](../design/processing-runs.md).
