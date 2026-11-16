# User guide

The dashboard is for operators following the queue: which runs are recorded
on which instrument, and with what status. It shows **recorded runs**: what
has reached Tiled. It does not show the queue itself yet; use `qserver status`
for that until the queue-status panel arrives (plan §5.5).

At this stage the deployment provides one dashboard, **Timeline health**. It
lists, per catalogue root, whether the adapter can read Tiled, how long the
check took, and the Tiled and adapter versions. Any status other than `ok`
means the timeline cannot be trusted:

| Status | Meaning |
|---|---|
| `unreachable` | No connection to Tiled (network, server down) |
| `unauthorised` | Tiled rejected the adapter's key |
| `forbidden` | The key is valid but may not read this root |
| `not_found` | The configured root does not exist |
| `error` | Any other answer from Tiled |

The timeline, run details and viewer panels arrive in steps 3 and 4 of the
[plan](../design/plan.md).
