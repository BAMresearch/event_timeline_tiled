# Brief: read-only HTTP access to the MOUSE queue

For: whoever works on `mouse_bluesky` (a person or a coding agent).
Self-contained; the changes requested here are in that repository and on
the instrument PC. Nothing here asks for changes to event-timeline-tiled.

## Context

event-timeline-tiled is a Grafana dashboard of Bluesky runs. Besides runs
from Tiled, it shows each instrument's RE Manager: its state (`idle`,
`executing_queue`, `paused`, …), the running plan, since when it runs, and
the number of queued items. The dashboard will run on **a separate server**,
not on the instrument PC, so it needs to read the MOUSE queue over the lab
network.

It only reads. Its adapter makes exactly two requests, through
`bluesky-queueserver-api` 0.0.14 (`REManagerAPI` over HTTP), every refresh:

| Request | bluesky-httpserver route | Scope it needs |
|---|---|---|
| `status` | `GET /api/status` | `read:status` |
| `queue_get` | `GET /api/queue/get` | `read:queue` |

The API key goes in the `Authorization` header. The adapter's own tests
fail if it sends anything else.

## What we found

Checked on `mouse_bluesky` at `b901bfc`, with bluesky-queueserver 0.0.26
and bluesky-httpserver 0.0.15 (source read, not yet run):

- There is no HTTP server. The RE Manager is controlled over 0MQ only,
  and the scripts connect to `tcp://127.0.0.1:60615`.
- `start-mouse-re-manager.sh` does not set `--zmq-control-addr` or
  `--zmq-info-addr`. The bluesky-queueserver 0.0.26 defaults are
  `tcp://*:60615` and `tcp://*:60625`: both listen on **every interface**.
  The 0MQ control port accepts every command, unauthenticated, from anyone
  who can reach it: add, clear or start the queue, open the environment,
  stop the manager. Unless a firewall blocks it, that is anyone on the lab
  network.
- 0MQ cannot give a read-only remote reader, but bluesky-httpserver can.
  Every route requires a scope. An API key carries only the scopes it was
  created with, and never more than its owner's role allows.
- Two built-in shortcuts in bluesky-httpserver 0.0.15 are **not suitable**:
  - the single-user API key (`QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY`):
    its role, `unauthenticated_single_user`, has every `write:` scope;
  - anonymous access (`allow_anonymous_access`): it is open to anyone, and
    by default it gives only `read:status`, not the `read:queue` we need.

## Requested

1. **Run bluesky-httpserver next to the RE Manager** on the instrument
   PC, as a service started and stopped with it (same mechanism as
   `start-mouse-re-manager.sh`, or a systemd unit). Pin the version, at
   least 0.0.15, in `pyproject.toml` and `uv.lock`. Point it at the local
   RE Manager in its config file (`qserver_zmq_configuration`):
   `control_address: tcp://127.0.0.1:60615` and
   `info_address: tcp://127.0.0.1:60625`.

2. **Authentication with a read-only service account.**
   - Configure an authentication provider: the site's LDAP or PAM if
     available (`LDAPAuthenticator`, `PAMAuthenticator`), otherwise
     `DictionaryAuthenticator`, with the password taken from the
     environment and not from the file.
   - Use an API access policy that gives one account for the dashboard,
     for example `timeline-reader`, only `read:status` and `read:queue`.
     With `DictionaryAPIAccessControl`, give it a custom role holding
     exactly those two scopes. The built-in `observer` role also has
     `read:history`, `read:console` and others, which is more than needed.
   - Keep operator and admin accounts as you need them for other clients.
     Do not enable anonymous access, and do not hand out a single-user key.
   - The server keeps identities and keys in its database
     (`database.uri`, default `sqlite:///./bluesky-httpserver.sqlite` in the
     working directory). Give it a fixed path that survives restarts and
     is backed up.

3. **One API key for the dashboard.** Log in as `timeline-reader` and
   create it with `POST /api/auth/apikey`, body
   `{"scopes": ["read:status", "read:queue"], "expires_in": null, "note": "event-timeline-tiled"}`.
   If you prefer keys that expire, choose a lifetime and document the
   rotation.
   - Send the key to the dashboard's operator over a secure channel.
   - Never put it in the repository, a log or a URL.
   - Write down the key's `first_eight` (from `GET /api/auth/apikey`) so
     that it can be revoked later with `DELETE /api/auth/apikey`.

4. **Reachable from the dashboard server, over HTTPS.**
   - The HTTP server listens on the lab network: uvicorn `host` set to the
     lab interface, port 60610 or a port of your choice.
   - The key travels in a request header, so use TLS, either from a
     reverse proxy in front of the server (nginx or Caddy, with a
     certificate the site trusts) or with uvicorn's own certificate
     options.
   - If the instrument PC has a firewall, open this port only to the
     dashboard server.

5. **Close the 0MQ ports to the network.** Start the RE Manager with
   `--zmq-control-addr tcp://127.0.0.1:60615` and
   `--zmq-info-addr tcp://127.0.0.1:60625`. The mouse_bluesky scripts and
   the HTTP server then still connect locally. Before changing this, check
   whether anything controls the queue from another host. If something
   does, list it in your report and move it to HTTP with its own account,
   rather than leaving the port open.

6. **Document** the HTTP server in `design_documentation/operations.md`:
   - how to start, stop and check it;
   - where its config, database and certificate live;
   - the accounts, roles and scopes;
   - how to create, list and revoke keys.

## Tests

- With the dashboard key, from another host:
  - `GET /api/status` and `GET /api/queue/get` return `200`;
  - `POST /api/queue/item/add`, `POST /api/queue/start` and
    `GET /api/history/get` are refused with `401` or `403`.
- Without a key, or with a wrong key, every route returns `401`.
- From another host, ports 60615 and 60625 do not answer.
- Locally, `mouse-bluesky enqueue …` and the vacuum scripts still work
  over `tcp://127.0.0.1:60615`.
- A unit test or config check: the HTTP server config has no anonymous
  access, no single-user key, and the dashboard role has only the two
  scopes.

## When done

Report:
- the HTTPS URL as seen from the lab network (host and port), and the
  certificate's issuer;
- the bluesky-httpserver version and the authentication provider used;
- how the dashboard key was created: its scopes, expiry and `first_eight`.
  The key itself goes over a secure channel, not in the report;
- whether anything used the 0MQ ports from another host.

event-timeline-tiled then sets `ETT_QUEUES=MOUSE=http:https://<host>:<port>`
with the key file `ETT_QUEUE_MOUSE_API_KEY_FILE`, and runs its queue
panel check against the live server. That check is the "not yet tested
against a live server" item in its queue documentation.
