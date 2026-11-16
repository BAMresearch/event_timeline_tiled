# Queue status

The **Queue** panel shows, per configured RE Manager, its state (`idle`,
`executing_queue`, `paused`, …), the running plan and the number of queued
items (plan §5.5). The adapter only reads: it calls `status` and `queue_get`
of `bluesky-queueserver-api` 0.0.14 through a wrapper that exposes nothing
else (`event_timeline_tiled.queues.ReadOnlyQueue`; a test fails if any other
method becomes reachable). An RE Manager that does not answer is shown as
`unreachable`, never as `idle`.

Configure it in `deploy/.env`, one entry per instrument:

```sh
ETT_QUEUES=MOUSE=zmq:tcp://mouse-host:60615
```

## Two transports

| | 0MQ, straight to the RE Manager | HTTP, through Bluesky HTTP Server |
|---|---|---|
| Entry | `MOUSE=zmq:tcp://host:60615` | `MOUSE=http:http://host:60610` |
| Instrument side | nothing extra | one more service, `bluesky-httpserver` |
| Read-only | only by the adapter's wrapper: the 0MQ control port accepts every command from anyone who can reach it | also enforced by the server, with an API key limited to `read:status` and `read:queue` |
| Network | the adapter must reach the control port (default 60615) | the adapter must reach the HTTP port (default 60610) |

0MQ is the prototype default. Because the control port itself is not
read-only, prefer HTTP once a Bluesky HTTP Server runs next to the RE
Manager, and keep the 0MQ port reachable only from trusted hosts. If the RE
Manager uses 0MQ encryption, the 0MQ transport is not supported yet.

### HTTP key

On the HTTP server, create an API key with only the scopes `read:status`
and `read:queue` (how depends on the server's authentication set-up; see the
bluesky-httpserver documentation on API keys; not yet tested here), write it
to a file next to `adapter.key`
(same directory and permissions, see [Tiled access](tiled-access.md)), and
name the file in `deploy/.env`:

```sh
ETT_QUEUES=MOUSE=http:http://mouse-host:60610
ETT_QUEUE_MOUSE_API_KEY_FILE=/secrets/mouse-queue.key
```

The variable name is `ETT_QUEUE_<INSTRUMENT>_API_KEY_FILE`, with the
instrument in upper case and other characters replaced by `_`. Pass it to
the adapter container (add it under the adapter's `environment` in
`deploy/compose.yaml` for your deployment). The key is never sent to Grafana
or the browser.

## Demo stack

The demo profile runs Redis, a real RE Manager with simulated devices
(`deploy/queue/startup`, one plan `demo_measurement`) and `demo-queue`, which
keeps that disposable RE Manager busy so the panel changes. It writes no runs.
