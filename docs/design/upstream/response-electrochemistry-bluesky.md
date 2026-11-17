# Reply: electrochemistry-bluesky (v0.2.0)

Reply to the [brief](brief-electrochemistry-bluesky.md), received
2026-11-17 from the electrochemistry-bluesky developer; released in
electrochemistry-bluesky v0.2.0. Summarised here.

| Brief item | Reply |
|---|---|
| 1. Timed flush | Rows are written at least every `flush_s` (default 10 s), set per session in `[tiled]`. With a responsive server they arrive within about `flush_s` plus 1 s, so a stall threshold of a few minutes is safe. An interim in electrochemistry-bluesky that uses `TiledWriter` internals, pinned to bluesky-tiled-plugins 2.0.13 (with Tiled 0.2.21) |
| 2. Set-up names | `instrument = "electrochemistry-k2460"`; `station = "k2460-<serial>"`, `"simulator"`, or the session's own name; `facility` only when the session sets it. `readout` descriptors in Tiled hint `voltage_v` and `current_a`; the start document's hints name `t_s` as the x axis |
| 3. Concurrent reads | New tables go to SQLite in WAL mode (0 of 32 parallel reads failed). Older stores' DuckDB tables stay readable, but parallel reads of those can still fail |
| 4. Read-only key | Documented: the local store's only key can write; shared readers need a server with scoped keys |

What event-timeline-tiled did with it: a view table entry for
`electrochemistry-k2460` (labels `measurement_id` · `sample_id`; possibly
stalled after 10 minutes, to allow sparse sampling steps beyond `flush_s`);
its views are the generic fallback's, which now prefers the hinted stream.
Timelines plot against event time, not `t_s`.
