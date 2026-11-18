# Codex brief 08: follow-up on the presets design (step 8)

## Context

You reviewed draft 1 of `docs/design/presets.md` in review 07
(`response-07-presets.md`); its findings were folded into draft 2
(`disposition-07.md`, commit `c5e4f85`). The maintainer then made two
decisions that change parts you reviewed; draft 3 (the working tree on
branch `step-8-presets`) carries them:

1. **Today's lane names and order may change**, because nobody uses them
   yet. So `instruments` is no longer a built-in strategy on today's code
   (your finding 2) but an ordinary preset file shipped with the package
   (`presets.md` §4.1), and there is one lane mechanism: `clips.lane_key`
   and `lane_names` go. To express it, the format gains a `root` field path,
   lane-name templates (`group_by.name`) with `{path|default}` placeholders
   and separator clean-up, and a per-track `lane_prefix`. The changed names
   are listed in §4.1; `/clips` `lane_key` changes meaning (§10).
2. **Unfolding one lane into its streams** (§6.5): with `unfold=<lane id>`,
   the frame draws each run of that lane once per stream, from the stream's
   first to its last event, on a sub-lane per stream; a dashboard variable
   and data links "Unfold lane into streams" and "Fold" drive it (§11). Read
   cost is bounded by `ETT_UNFOLD_MAX_RUNS`; finished runs' extents are kept.
   Unfolding several lanes, or all, stays deferred.

Review only these changes and what they touch; do not repeat review 07.

Read first: `AGENTS.md`; `docs/design/presets.md` (draft 3: §2, §4, §4.1,
§5.1, §6.2, §6.4, §6.5, §10, §11, §12–§15); `git diff c5e4f85 --
docs/design/` for exactly what changed; `docs/design/reviews/response-07-presets.md`
(findings 2, 3, 9, 10, 12, 13, 16); `docs/technical/endpoints.md` (`/clips`,
`/clips/frame`, `/clips/status`); `src/event_timeline_tiled/clips.py`,
`timeline.py`, `layout.py` (`event_columns`, `last_event_time`, `streams`);
`grafana/generator/`.

## Task

Independent critical review. Do not change any file except your response
file and do not commit. You may run the tests and small throwaway probes
(disposable Tiled 0.2.21 catalogues as `tests/conftest.py` builds them).

1. **The default preset as a file.** Can §4.1's YAML express what the
   timeline needs: a lane per root, facility, instrument, station and role,
   processing next to acquisition by tuple order, runs without an instrument,
   several roots, equal names in two facilities? Is anything lost that
   operators relied on, beyond the names §4.1 lists? Should the default be
   overridable by a deployment after all?
2. **Lane-name templates.** `{path|default}`, the restriction to the group's
   own fields, the clean-up of white space and ` · ` separators, the
   200-character limit, ` (2)` disambiguation: well-defined for every input
   (all parts empty, separators inside values, a default containing ` · `)?
   Is the same template language right for clip labels, where the clean-up
   and defaults now also apply?
3. **Contract changes.** `/clips` `lane_key` and `lane` now carry the first
   placement's id and name. Is that the right replacement, and is anything
   else (frame fields, `/clips/status`, the trend's "runs like it", health)
   affected by removing the fixed lane key?
4. **Unfolding: meaning.** Extents from each stream's first and last event
   time; streams in name order; zero-event and unreadable streams; open runs;
   sub-lanes and overlap within a stream sub-lane; what a stream clip's
   status, label and links should be; how unfolding interacts with a run in
   several tracks, `min_lanes`, budgets and the latest-runs table.
5. **Unfolding: cost and caching.** Only the `time` column of each stream
   (Tiled 0.2.21 cannot select rows); `ETT_UNFOLD_MAX_RUNS` (latest 200 in
   the window); keeping finished runs' extents (are they final? a run whose
   stop document arrives while events are still being flushed?); open runs
   once per activity interval. Is the bound sound for a 14-day window with
   long runs, and for processing runs whose streams hold only external
   arrays (their `internal` table has `seq_num` and `time`)?
6. **Unfolding: dashboard.** A hidden URL-synced `unfold` variable set and
   cleared by State timeline data links (Grafana 13.3.1): do the links keep
   `run`, `lane`, `preset`, `stream` and the time range without duplicating
   parameters? Is there a declarative way to show "Fold" only when unfolded,
   or must both links always show?
7. **Anything else** these two decisions break in draft 2, or that review
   07's accepted findings now need differently.

## Response

Write to `docs/design/reviews/response-08-presets-follow-up.md`: a verdict in
one paragraph; findings as a numbered list (severity, section or file and
line, problem, evidence with the versions checked, proposed change); what
you ran and what you could not verify.
