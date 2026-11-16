# Disposition of Codex review 04 (scaffold and demo stack)

Review: [response-04-scaffold.md](response-04-scaffold.md). Verdict: a sound
foundation with no blocker; changes needed before the lab instructions are
used. All findings accepted; two parts are moved to the start of step 3, where
the tests that use them are written. Everything below was re-run on
2026-11-16: 55 tests, ruff, ruff format, `mypy --strict`, `mkdocs build
--strict`, and the demo stack rebuilt from empty volumes with the new smoke
test passing.

| Finding | Disposition | Where |
|---|---|---|
| 1. Key directory unreadable on Linux | Accepted, reproduced in a Linux container: directory `700` → `PermissionError` for uid 10001. Now directory `750` and file `640`, group 10001 (tested: adapter reads, other users refused); the image's `app` group is fixed at gid 10001; directory mount kept for atomic replacement; rootless Docker noted as untested | `tiled-access.md` §1, §3, §5; `python.Dockerfile` |
| 2. Malformed responses break or fake `/health` | Accepted: search pages and the version response are validated; malformed → `error` with a fixed description (no body copied); a failed version lookup no longer hides the root rows; other HTTP errors are caught | `tiled_http.py`; `tests/unit/test_tiled_http.py` (HTML, JSON list, wrong shape, 401, 403, 404, 503, transport, bad version) |
| 3. Processing fixtures short of contract v1 | Accepted: `modacor` tracing object; uncertainty states what it is the uncertainty of and its meaning; image has `dims` and per-axis name and units; a successful run without outputs must give a reason; event before stream datums as in §1.1; tests assert all of it | `testing/fixtures.py`; `tests/contract/test_fixtures.py` |
| 4. Window cases missing | Accepted, moved to the start of step 3 with the search tests that use them: acquisition failure, UID mismatch, MOUSE stopped-success marker, success then failed retry with coherent `request_id`/`attempt`, stop-time tie, partial-output failure. Lab descriptor capture kept | plan §9 step 3 |
| 5. Live acquisition bypassed the RunEngine | Accepted: live MOUSE measurements come from the RunEngine (`baseline`, `snapshot`, three readings in real time); the generic operando run is a separate process with its own RunEngine, backfill retimed like the history. The direct `event_model` live path is removed. Also found: the seeder's "history already written" check used `Key("instrument")` instead of `Key("start.instrument")` and would have duplicated history after a restart; fixed and checked | `testing/fixtures.py`, `testing/seed.py`, `compose.yaml` (`demo-operando`) |
| 6. curl check leaks the key, globbing, wrong body | Accepted: the curl block is replaced by `ett-check-access`, which reads the key in-process like the adapter and prints statuses only. It does **not** probe with a write (decision D-005); it checks the key's own scopes from `/auth/whoami`. The key file is created `0600` from the start. The write refusal is checked only on the disposable demo, with the 0.2.21 request schema | `access_check.py`; `tiled-access.md` §2, §4; `deploy/smoke_test.py` |
| 7. Policy boundary; prove data reads | Accepted: the guide says that without a policy the key reads the whole server and that `ETT_CATALOG_ROOTS` is not a permission; `--run` reads metadata and real data (first table or array), `--denied` checks a sibling the policy must hide. The grant itself is left to the server's policy configuration | `tiled-access.md`; `tiled_http.py` (`read_sample`) |
| 8. Secret substitution unsafe | Accepted, the restricted-alphabet option for this disposable stack: values must be ≥16 of `A-Za-z0-9-_`, otherwise Tiled refuses to start; rendered config created `0600` | `deploy/tiled/entrypoint.py`, `.env.example`, `demo-stack.md` |
| 9. Podman claim rests on Docker DNS | Accepted: Docker only until Podman is tested (decision D-004) | `compose.yaml`, `nginx.conf`, plan §10, `decisions.md` |
| 10. Multi-user smoke test not a CI gate | Accepted: `deploy/smoke_test.py` (anonymous refusals, health through Grafana, `ett-check-access` in the container, write refused); CI generates secrets, starts the demo stack, runs it, prints logs on failure and always removes the stack with `down -v`. PostgreSQL search tests in step 3 must fail, not skip, without PostgreSQL | `.github/workflows/ci.yml`; `testing.md`; plan §9 step 3 |
| 11. Tiled delta beyond DuckDB | Accepted: recorded in D-002; query plans re-measured and an isolated 0.2.8-client check in step 3; catalogue migrations before upgrading an existing server in the lab-session list | `decisions.md`; plan §9 step 3, §10.2 |
| 12. Settings validation | Accepted: numbers must be finite; the URL is parsed (http/https, host, no credentials, query or fragment, valid port); root segments limited to `A-Za-z0-9_~.-`, no `.`, `..` or empty segments, and percent-encoded in requests. Attrs validators for fixture containers: deferred to step 3, with the data containers | `settings.py`; `tests/unit/test_settings.py` |
| 13. TLS and Viewer accounts before exposure; root seeders | Accepted: loopback binding kept; requirements for exposure listed in the guide and the lab-session list. Keys now in separate volumes (the adapter never mounts `seeder.key`); a one-shot root bootstrap hands key and processed volumes to uid 10001, and the seeders run as that user | `compose.yaml`; `testing/bootstrap.py`; `tiled-access.md`; plan §10.2 |

Also changed while verifying: `PYTHONUNBUFFERED=1` in the Python images
(the seeder's stdout messages were held back in the container's buffer).

Not verified: GitHub Actions itself (the CI job was exercised locally step
by step), rootless Docker, Podman, an access-policy deployment, and a real
Tiled 0.2.8 client against 0.2.21.
