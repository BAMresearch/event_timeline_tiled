# Codex brief 01: review the plan and architecture

## Context

`~/Code/event_timeline_tiled` is a new, nearly empty prototype
repository for a read-only timeline viewer of Bluesky runs and event streams
stored in Tiled, laid out like a video editor (NLE): timeline at the bottom,
details and graph viewer top right, run summary and channel selector top left.
It is built as a Grafana dashboard in a container we ship, possibly with a
small Python adapter in front of Tiled.

Fixed by the maintainer (do not argue these, but flag real risks in them):

- First users are operators following the QueueServer queue: what is running
  where, and its status.
- Built-in Grafana panels and published plugins first; customisation later.
- MOUSE (`~/Code/mouse_bluesky`) first. Electrochemistry
  (`~/Code/electrochemistry_bluesky`) gets a Tiled interface soon
  and must then fit without redesign. With both, one central Tiled with a
  PostgreSQL catalogue.

Read first:

- `docs/design/plan.md` (revision 2, the plan under review)
- `AGENTS.md` (draft contributor and agent instructions)
- The earlier Dash attempt being replaced:
  `~/Code/bluesky-web-plotting/design_documentation/` (all files)
  and `src/bluesky_web_plots/timeline/`
- Conventions to follow: `~/Code/electrochemistry_bluesky`
  (`pyproject.toml`, `CONTRIBUTING.md`, `docs/technical/development.md`,
  `.github/workflows/ci.yml`) and `~/Code/MoDaCor/AGENTS.md`
- How MOUSE writes runs: `mouse_bluesky/qserver_profile/profile_mouse/startup/`
  and `mouse_bluesky/src/mouse_bluesky/plans/`

## Task

Give an independent critical review. Do not write code and do not change any
file except your response file.

1. Plan section 4.1: try Infinity straight to Tiled first, expect an adapter by
   step 3. Is that the right order, or should the adapter come first? Any
   option the plan missed?
2. Are the adapter endpoints (4.2) and the per-instrument view table (4.3)
   cut right? Is anything MOUSE-specific leaking outside the view table, which
   would make adding electrochemistry later a redesign?
3. Plan section 3: `facility` and `instrument` keys (plus `station`) in the
   start document are agreed. Check the plan's description of what MOUSE runs
   hold today (streams, start-document keys, detector frames not in Tiled)
   against `mouse_bluesky`, and flag anything wrong.
4. Plan section 3a. It is decided that each MoDaCor processing is a
   Bluesky-style run of its own, linked to the raw run by `derived_from`. Review
   the proposed processing-run contract (start/stop keys, streams, reprocessing)
   for gaps. Then review the recommendation to start processing from a Tiled
   watcher rather than a RunEngine callback, against MoDaCor's server API
   (`~/Code/MoDaCor/docs/reference/server-api.md`, especially
   "Processing" and "Bluesky integration notes";
   `src/modacor/server/`), its `TiledSink`
   (`src/modacor/io/tiled/tiled_sink.py`), and the MOUSE QueueServer start-up
   (`mouse_bluesky/qserver_profile/profile_mouse/startup/`). Say what MoDaCor
   would have to add, and whether the proposed home of the watcher (generic
   runner in MoDaCor's server, MOUSE rules in MoDaCor-examples) fits MoDaCor's
   `AGENTS.md`.
5. For operators following the queue, is the version 1 dashboard (section 5)
   the right minimum? What would an operator miss first (stalled runs, queue
   state, failed runs or processing, Tiled being down)?
6. Did the plan carry over the right decisions from the bluesky-web-plotting
   design documents (section 2), and drop the right ones?
7. Is the step order (section 6) sound? What must the spike prove first?
8. Does `AGENTS.md` match the sibling repositories' conventions, and does it
   miss rules this project needs?
9. Anything else that is wrong, risky or over-built for a prototype.

## Response

Write to `docs/design/reviews/response-01-architecture.md`:

- A verdict in one paragraph.
- Findings as a numbered list, each with: severity (blocker / should fix /
  suggestion), the plan section, the problem, the evidence (file and line, or
  documentation with its version), and the proposed change.
- Separate what you verified from what you believe but did not verify.
