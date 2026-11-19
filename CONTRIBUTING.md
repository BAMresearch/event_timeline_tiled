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

## Releases

Releases come from `main` through a release pull request (python-semantic-release,
configured in `pyproject.toml`); deployments install from the tag
([single host](docs/technical/single-host.md)):

1. After every check passes on `main`, CI opens or updates the PR
   `chore(release): vX.Y.Z` from the commits since the last tag: `feat` gives a
   minor version, `fix` and `perf` a patch (major versions only after 1.0).
   It sets `__version__` and adds the version's section to `CHANGELOG.md`;
   edit the notes on its branch if needed.
2. The same job starts CI on the release branch, whose checks appear on the PR
   (checks of a PR opened with the workflow token do not start by themselves).
   If they are missing, run CI on `release/next` from the Actions tab. Then
   merge it.
3. The merge tags `vX.Y.Z` and creates the GitHub release from the changelog
   section. Nothing is published to PyPI.

Coding agents: see [AGENTS.md](AGENTS.md).
