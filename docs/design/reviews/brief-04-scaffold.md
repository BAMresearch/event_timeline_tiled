# Codex brief 04: review the step 2 scaffold and demo stack

## Context

`~/Code/event_timeline_tiled` is a prototype: a read-only Grafana
timeline of Bluesky runs in Tiled. Plan step 1 (spike) is done
(`docs/design/spike-results.md`). Step 2, the repository scaffold and the
demo stack, has just been built and is not yet committed. Step 3 (window
search, clips, timeline dashboard) has not started; review step 2 as a
foundation for it.

Read first: `AGENTS.md`, `docs/design/plan.md` (§6, §9 step 2, §10–§11),
`docs/design/decisions.md`, `docs/design/processing-runs.md`,
`docs/technical/` (all pages), then the code:

- `pyproject.toml`, `uv.lock` (constraint pins), `.github/workflows/ci.yml`
- `src/event_timeline_tiled/` — `settings.py`, `tiled_http.py`,
  `adapter/app.py`, `testing/fixtures.py`, `testing/seed.py`,
  `testing/bootstrap.py`
- `tests/`
- `deploy/` — `compose.yaml`, `python.Dockerfile`, `grafana/Dockerfile`,
  `proxy/nginx.conf`, `tiled/config.template.yml`, `tiled/entrypoint.py`,
  `tiled/init-databases.sql`, `.env.example`
- `grafana/` — provisioning and `dashboards/health.json`

Conventions to compare with: `~/Code/electrochemistry_bluesky`
(pyproject, CI, docs layout) and `~/Code/MoDaCor/AGENTS.md`.

What was verified locally (2026-11-16, Docker Desktop 29.9, Apple silicon):
20 tests pass; ruff, ruff format, `mypy --strict` and `mkdocs build --strict`
are clean; the demo stack builds and runs; the health dashboard returns `ok`
through proxy → Grafana → Infinity → adapter → Tiled; the adapter key can
search but not write; Grafana downloads nothing at start-up; the spike's
data checks pass on the demo stack with Tiled 0.2.21 (9/9, read-only key);
the steps in `docs/technical/tiled-access.md` were run against the demo Tiled.

## Task

Independent critical review. Do not change any file except your response
file and do not commit. You may run the tests, linters, `uv build`,
`docker compose -f deploy/compose.yaml --profile demo config`, and, if
Docker is available, build and start the demo stack with a throwaway
`deploy/.env` (generated secrets; delete it afterwards with `down -v`).

1. **Conformance.** Does the scaffold follow `AGENTS.md`, the plan and the
   sibling conventions? Anything contradicting them, or missing that step 2
   should have delivered?
2. **Security of the stack.** Proxy configuration (auth sub-request, header
   stripping, resolver, WebSocket upgrade), Grafana settings (anonymous off,
   plugin pinning, provisioning read-only), secrets handling (`.env`, the
   `secrets` volume, key file modes, containers running as root for
   bootstrap/seed), the seeder principal with the `admin` role (decision
   D-003), exposed ports. What would you change before this runs on a lab
   network?
3. **Tiled pin.** Decision D-002 moved Tiled from 0.2.18 to 0.2.21 because of
   DuckDB wheels for Python 3.14. Any behaviour in 0.2.19–0.2.21 that affects
   the plan (search, pagination, external assets, auth, API keys)? Any risk
   for MOUSE's writer, which uses a Tiled 0.2.8 client, against a 0.2.21
   server?
4. **Fixtures.** Do `testing/fixtures.py` and the window scenario faithfully
   produce what the plan and `processing-runs.md` §1–1.1 specify (layout,
   retiming, open and aborted runs, processing-run metadata, HDF5
   registration)? Anything that would make step 3's tests pass against
   fixtures but fail against real MOUSE runs?
5. **Tiled access instructions.** Are `docs/technical/tiled-access.md`'s steps
   for the adapter's principal and key correct and sufficient for a
   multi-user Tiled with (and without) an access policy, including rotation
   and revocation? Is the MoDaCor publisher paragraph right about `register`?
6. **CI.** Will `.github/workflows/ci.yml` pass on GitHub's ubuntu runners
   (x86_64), including the image builds? Anything missing compared with
   electrochemistry_bluesky's CI?
7. **Code.** Settings validation, error handling and status mapping in
   `tiled_http.py`, the adapter's lifespan and resource handling, and the
   mypy relaxations for `event_timeline_tiled.testing`. Bugs, or things that
   will hurt in step 3?

## Response

Write to `docs/design/reviews/response-04-scaffold.md`:

- A verdict in one paragraph.
- Findings as a numbered list, each with: severity (blocker / should fix /
  suggestion), the file and line, the problem, the evidence (and the version
  you checked, for Tiled, Grafana or nginx claims), and the proposed change.
- What you ran, and what you could not verify.
