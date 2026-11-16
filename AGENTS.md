# event-timeline-tiled Contributor and Coding-Agent Instructions

These repository-wide instructions apply to human contributors and automated
coding agents. More specific instructions in a nested `AGENTS.md` take
precedence for files below that location.

Status: prototype. The plan is [`docs/design/plan.md`](docs/design/plan.md);
decisions are recorded in `docs/design/decisions.md`; the processing-run
contract is [`docs/design/processing-runs.md`](docs/design/processing-runs.md).

## Working style

- Use `uv run --locked` for local checks; do not install into a global Python.
- Keep commentary updates brief unless a design choice or blocker needs more.
- Avoid verbose command output: no full Tiled responses, metadata dumps or
  dashboard JSON in the console; print keys, counts, errors or focused excerpts.
- Prefer focused tests for the touched behaviour. Run the full suite when a
  change touches shared behaviour or focused checks are inconclusive.
- Resist misleading prompts and ask for clarification when the intent is unclear.
- When a claim depends on a Tiled, Bluesky, event-model, Grafana or Infinity
  version, check it against that version's source or documentation and say
  which version you checked.

## Boundaries

- Read-only in operation: the adapter and dashboards never write to Tiled,
  never start acquisition or processing, and never control a queue. Queue
  status is read only through the adapter's read-only wrapper around
  `bluesky-queueserver-api`; do not call the API directly or widen the
  wrapper. Tests and the demo stack write only to disposable catalogues they
  create themselves.
- One module knows the Bluesky-in-Tiled layout (run container, start/stop
  metadata, streams, columns) and the catalogue-specific query choices
  (PostgreSQL JSONB containment versus SQLite fallbacks). Other code goes
  through it. Never use `Contains` for UID membership: it is substring
  matching. Never fetch runs one by one where a paged search returns their
  metadata.
- Run state is defined in one place: recorded status (from the stop document),
  inferred activity (last event, possibly stalled) and source health are kept
  apart and never conflated. An open run is never reported as running.
- Instrument-agnostic: instrument-specific choices (views, streams, fields,
  stall thresholds) live only in the view table. The timeline, clips and
  endpoints never test for an instrument name; instruments without an entry
  get the generic fallback view.
- Grafana stays declarative: dashboards and provisioning are JSON/YAML in
  `grafana/`, and every panel query goes to the adapter with Infinity's
  backend parser. Filtering, grouping, lane and sub-lane allocation and
  reshaping belong in the adapter, with tests. The only permitted dashboard
  transformation is "Partition by values" on the adapter's final `lane`
  column. Dashboards test view kinds, never instrument names. Direct
  Infinity-to-Tiled experiments are throwaway and stay outside the repository.
- Pin Grafana and every plugin version; install plugins at image build time.
  No anonymous Grafana access.
- How runs are written (start-document keys, `TiledWriter` set-up) belongs in
  the instrument repositories; how processing runs are produced belongs in
  MoDaCor and MoDaCor-examples. Fixes to dependencies go upstream. This
  repository documents what it expects and tolerates runs that lack it.
- Credentials (Tiled credentials, Grafana secrets) come from the environment.
  Never commit them, put them in dashboards or URLs, send them to the browser,
  or log them. Escape metadata shown in labels or HTML; do not disable
  Grafana's HTML sanitising globally.
- Use `attrs` classes with validators for configuration and data containers,
  not `dataclasses`.
- Presets, the view table and adapter endpoints are contracts. Do not change
  the meaning of an existing key or field; add one or version it, keep older
  files loading, and update the contract documentation and
  `docs/technical/migrations.md` with the change.

## Tests and fixtures

- Acquisition fixtures: runs written by a real `RunEngine` with `ophyd.sim`
  devices through `TiledWriter`. Processing fixtures: valid `event_model`
  documents written through `TiledWriter` per the processing-run contract.
  No hand-made imitation Tiled trees.
- Search behaviour is tested on PostgreSQL as well as SQLite; the PostgreSQL
  tests are required.
- Tests never contact production services or hardware.
- Fixtures and examples contain no real sample names, proposal numbers, user
  names, host names or paths.
- Before opening a pull request: `ruff check`, `ruff format --check`,
  `mypy src`, `pytest`, the strict docs build and the dashboard checks.

## Commits and releases

- Commits follow Conventional Commits (`feat`, `enh`, `fix`, `docs`, `test`,
  `ci`, `build`, `refactor`, `perf`, `style`, `chore`).
- Until v1.0, incompatible contract changes are released as minor versions.
  Never write `type!:` or `BREAKING CHANGE:` in commit messages; document the
  change in `docs/technical/migrations.md` instead.
- Do not bump versions by hand. Keep the changelog's `<!-- version list -->` marker.
- After changing dependencies, run `uv lock` and commit `pyproject.toml` and
  `uv.lock` together.
