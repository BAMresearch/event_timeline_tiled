# User guide

The dashboards are for operators following the queue: which runs are
recorded on which instrument, and with what status. They show **recorded
runs**: what has reached Tiled. They do not show the queue itself yet; use
`qserver status` for that until the queue-status panel arrives (plan §5.5).

Sign in to Grafana with your Viewer account and open **Timeline**.

## Timeline

Laid out like a video editor:

- **Bottom: the timeline.** One lane per instrument (and station), with
  processing runs on their own lane below it. Each block is one run, from
  its start to its stop. Overlapping runs on one lane are spread over
  sub-lanes (`MOUSE /2`). Very short runs are drawn a little wider than they
  are, so they stay visible; their real times are in the run details.
- **Top left:** *Runs in range* (newest first), *Failed and aborted* with
  the recorded reason, and for the selected run its details and streams.
- **Top right:** the selected run's data in its default view (for MOUSE
  measurements: the readings of `mouse_eiger_measure`, `snapshot` and
  `baseline`), and a time series when the run has one.
- **Top right corner:** *Data age*, the seconds since the adapter last read
  Tiled successfully.

**Selecting a run:** click its block on the timeline and choose *Select run*
(or *Zoom to run* to fit the time range to it), or click its label in a
table. The selection stays in the URL, so a link or a reload keeps it.

### Colours

| Colour | Meaning |
|---|---|
| green | `success`: the run's stop document says it finished successfully |
| red | `failed` |
| orange | `aborted` |
| purple | `unknown`: a stop document with an unrecognised exit status |
| blue | `open`: no stop document yet; drawn up to the last successful read |
| yellow | `possibly stalled`: open, but no event for longer than the instrument's threshold (MOUSE 20 min, others 30 min) |
| grey | `stale`: Tiled could not be read; this is the last data that could |

Green means what the run recorded, not that the measurement is complete: a
MOUSE measurement stopped with `RE.stop()` is recorded as `success` with
fewer than three readings (see *Streams*).

An open run is never called "running": Tiled cannot tell whether it is
acquiring, paused or crashed. *Possibly stalled* only says that nothing new
has arrived for a while.

Runs left open for more than 21 days before the end of the time range are
not shown.

## Timeline health

Per catalogue root: whether the adapter can read Tiled now, how long that
took, when the timeline last read it successfully and how old its data is,
and the Tiled and adapter versions. Any status other than `ok` means the
timeline cannot be trusted:

| Status | Meaning |
|---|---|
| `unreachable` | No connection to Tiled (network, server down) |
| `unauthorised` | Tiled rejected the adapter's key |
| `forbidden` | The key is valid but may not read this root |
| `not_found` | The configured root does not exist |
| `error` | Any other answer from Tiled |
