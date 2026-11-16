# Disposition of Codex review 03 (Grafana feasibility)

Review: [response-03-grafana-feasibility.md](response-03-grafana-feasibility.md),
against Grafana OSS 13.3.1 and Infinity 4.1.6. Result: plan revision 7 and
`AGENTS.md`. Docker was not running, so no gate was tested in a container or
browser; every gate in plan step 1 is still open.

| Correction | Disposition | Where |
|---|---|---|
| 1. State timeline is conditional on the gate; pins; benchmark | Accepted: pins recorded; acceptance list extended (alignment, relative ranges, refresh, navigation, 20 lanes / 2,000 clips) | plan §8, §9 step 1 |
| 2. Explicit `/clips/frame`, milliseconds for Grafana times | Accepted. Multi-lane transport: first try one long table split by a single "Partition by values" on the adapter's final `lane` column (a split, not grouping); fallback fixed lane-slot queries | plan §6.1, §8; AGENTS |
| 3. Sub-lanes for overlaps; tiny clips | Accepted: adapter allocates sub-lanes; companion runs table | plan §8 |
| 4. Crosshair is a hover guide; two tooltip links | Accepted | plan §8 |
| 5. Per-instrument views: conditional rendering needs Auto grid | Accepted: v1 uses a fixed Table (default view) top right; the choice between an Auto grid viewer and per-view-kind detail dashboards is made in step 6. Business Variable panel tried in the panel gate for the top-left selector | plan §8 |
| 6. Authenticated image route | Accepted: same-origin reverse proxy with a session check via Grafana `/api/user`, Text panel with sanitising on; operator screens sign in (no anonymous access) | plan §6, §9 step 1.3, §10, §14; AGENTS |
| 7. XY chart: log–log yes, error bars no; provisioning | Accepted: I±σ bounding curves plus table, Business Charts only if true error bars are required; image build with plugins at build time, stable UIDs, read-only provisioning | plan §7, §10 |

Not adopted: anonymous Viewer kiosks (they conflict with the image route;
operator screens sign in instead).
