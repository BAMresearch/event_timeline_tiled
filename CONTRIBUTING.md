# Contributing

- Read-only: the adapter and dashboards never write to Tiled, never start
  acquisition or processing, and never control a queue. Only tests and the
  demo stack write, into disposable catalogues they create.
- How runs are written belongs in the instrument repositories
  (`mouse_bluesky`, `electrochemistry_bluesky`) and MoDaCor; this package
  documents what it expects and tolerates runs that lack it.
- Instrument names appear only in the view table; the timeline, endpoints and
  dashboards stay instrument-agnostic.
- Credentials come from the environment or key files; never in the
  repository, dashboards, URLs or logs.
- Fixtures and examples contain no real sample names, proposals, user names,
  hosts or paths.
- Run the tests, ruff and mypy before opening a pull request
  ([testing](docs/technical/testing.md)). Commits follow Conventional
  Commits; do not bump versions by hand.

Coding agents: see [AGENTS.md](AGENTS.md).
