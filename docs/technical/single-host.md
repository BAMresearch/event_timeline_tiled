# Single host

The dashboards on the instrument PC itself, next to an RE Manager or a
Bluesky HTTP Server that listens on `127.0.0.1` only. `deploy/compose.host.yaml`,
on top of `deploy/compose.yaml`, puts the proxy, Grafana and the adapter on
the host's own network, every one listening on `127.0.0.1`:

| Service | Listens on | Setting |
|---|---|---|
| proxy (the dashboards) | `127.0.0.1:8080` | `ETT_HTTP_PORT` |
| Grafana, behind the proxy | `127.0.0.1:3000` | `ETT_GRAFANA_PORT` |
| adapter | `127.0.0.1:8000` | `ETT_ADAPTER_PORT` |

Grafana reaches the adapter at `http://127.0.0.1:<ETT_ADAPTER_PORT>` (the
datasource's URL, `ETT_ADAPTER_URL`), and the adapter reaches the queue at
whatever `ETT_QUEUES` names, such as `http://127.0.0.1:60610`. Nothing
listens on another interface.

## Tested versions

CI runs this layout with the smoke test (job `stack-single-host`) on:

- rootless Podman **4.9.3** (Ubuntu 24.04's own package) and **4.6.2**
  (`4:4.6.2-0ubuntu22.04+obs81.12` from the Kubic repository on Ubuntu
  22.04, as on the lab's hosts);
- Docker Compose **5.6.1**, the standalone binary, through Podman's
  Docker-compatible API. The files need Compose 2.24.4 or later (`!reset`,
  `!override`).

Nothing needs a Podman newer than 4.6.2. There is no `keep-id` user
mapping: under rootless Podman, a container's root (`ETT_ADAPTER_USER=0:0`)
is the host user who runs the stack, so the adapter reads that user's
mode-600 key files as they are. With the host's network, neither
slirp4netns nor pasta is involved. Docker works too, with
`ETT_ADAPTER_USER=$(id -u):$(id -g)` (never `0:0`, which is root there);
CI does not test that.

## 1. Check and set up the host (once)

Check, as the user who will run the stack:

```sh
podman --version                                   # 4.6.2 or later
podman info --format '{{.Host.Security.Rootless}} {{.Host.NetworkBackend}}'   # true …
grep "^$(id -un):" /etc/subuid /etc/subgid          # subordinate IDs exist
systemctl --user is-enabled podman.socket; systemctl --user is-active podman.socket
loginctl show-user "$(id -un)" --property=Linger
command -v docker-compose; docker-compose version   # 2.24.4 or later
```

Set up what is missing:

```sh
# Podman (Ubuntu 24.04: 4.9.3 from the archive; Ubuntu 22.04's own 3.4.4 is too old)
sudo apt install podman

# Podman's API for this user, now and at every boot
systemctl --user enable --now podman.socket

# Keep the user's services (the socket, /run/user/<uid>) after logout
sudo loginctl enable-linger "$(id -un)"

# Docker Compose, standalone: no Docker engine (Ubuntu's docker-compose-v2
# package depends on docker.io). Pinned and checked:
v=5.6.1
sha=47737f4236e6d3ccea7291995203f8f018058783b4c3a9469119811a0b5db8c0
mkdir -p ~/.local/bin
curl -fsSLo ~/.local/bin/docker-compose \
  "https://github.com/docker/compose/releases/download/v$v/docker-compose-linux-x86_64"
echo "$sha  $HOME/.local/bin/docker-compose" | sha256sum -c && chmod +x ~/.local/bin/docker-compose
```

Every compose command below runs with:

```sh
export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/podman/podman.sock"   # /run/user/<uid>/podman/podman.sock
```

## 2. Install and build (once per release)

```sh
git clone https://github.com/BAMresearch/event_timeline_tiled.git ~/code/event_timeline_tiled
cd ~/code/event_timeline_tiled && git checkout <release tag>
```

Then, with the settings of step 3 in the environment (compose checks them
for every command):

```sh
export DOCKER_BUILDKIT=0          # Podman's API serves the classic build endpoint
docker-compose -f deploy/compose.yaml -f deploy/compose.host.yaml build
docker-compose -f deploy/compose.yaml -f deploy/compose.host.yaml pull proxy
```

This is the only step that needs the network for images: the build pins
Grafana and its plugins, the proxy image is pinned by digest. Starting
(step 4) neither builds nor pulls, so a restart runs what was installed.

## 3. Settings

From the environment (or `deploy/.env`; the environment wins). No
`deploy/.env` is needed: the demo's settings are optional outside the demo
(CI starts this layout from a clean environment with `--env-file /dev/null`).
Secrets come from files of mode 600 owned by the user; never put them on a
command line.

| Variable | Value |
|---|---|
| `DOCKER_HOST` | Podman's socket (step 1) |
| `GF_ADMIN_PASSWORD` | Grafana's admin password, read from a file; used when Grafana first creates its database (later changes need `grafana cli admin reset-admin-password`) |
| `ETT_TILED_URL` | the Tiled server |
| `ETT_TILED_KEY_FILE` | **host** path of the Tiled API key file (mounted read-only into the adapter only) |
| `ETT_ADAPTER_USER` | `0:0` under rootless Podman (the host user) |
| `ETT_CATALOG_ROOTS` | containers holding runs; empty: the server root |
| `ETT_QUEUES` | `MOUSE=http:http://127.0.0.1:60610` ([queue status](queues.md)) |
| `ETT_QUEUE_KEY_FILE` | **host** path of the Bluesky HTTP Server key (scopes `read:status`, `read:queue`); used for every `http` queue |
| `ETT_HTTP_PORT`, `ETT_GRAFANA_PORT`, `ETT_ADAPTER_PORT` | 8080, 3000, 8000 |
| `ETT_PUBLIC_URL` | Grafana's root URL; default `http://127.0.0.1:<ETT_HTTP_PORT>/` |
| `ETT_PRESETS`, `ETT_DEFAULT_PRESET` | the preset directory (default `deploy/presets`) and the starting preset |

The Tiled key should hold only `read:metadata` and `read:data`
([Tiled access](tiled-access.md)). A **single-user** Tiled has only its one
key, which can write; until the server runs in multi-user mode, a
deployment may knowingly use that key. Point `ETT_TILED_KEY_FILE` at the
existing file rather than a copy, so there is one place to rotate it, and
check it with the acknowledgement:

```sh
docker-compose -f deploy/compose.yaml -f deploy/compose.host.yaml run --rm --no-deps \
  adapter ett-check-access --run <a recent run's uid> --accept-single-user-key
```

## 4. Start

```sh
docker-compose -f deploy/compose.yaml -f deploy/compose.host.yaml up --no-build --pull never
```

In the foreground (for a `screen` session); Ctrl-C stops the stack. Open
`http://127.0.0.1:8080/`.

## 5. A Viewer account for the screen (once)

With the stack running, create or update a Grafana account that can only
view, reading both passwords from mode-600 files (standard library only;
safe to run again):

```sh
python3 deploy/grafana_viewer.py --login <name> \
  --password-file ~/.config/<deployment>/grafana-viewer.password \
  --admin-password-file ~/.config/<deployment>/grafana-admin.password
```

Grafana's password policy is off in this stack, so a password needs only 4
characters; the account is a Viewer of the main organisation and never a
server admin. Grafana keeps no anonymous access.

## Security notes

- Only the host user can reach the containers' settings (`podman inspect`
  shows `GF_ADMIN_PASSWORD`); under rootless Podman that is the user who
  already holds the key files.
- The adapter never writes to Tiled (tested), but with a single-user key it
  could: a compromised adapter could write. That is the exposure the
  `--accept-single-user-key` acknowledgement accepts.
- The HTTP queue key is sent only in the `Authorization: ApiKey` header,
  and only `GET /api/status` and `GET /api/queue/get` are sent
  ([queue status](queues.md)).
