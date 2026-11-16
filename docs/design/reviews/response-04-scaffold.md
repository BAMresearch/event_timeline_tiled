# Review 04: scaffold and demo stack

Reviewed 2026-11-16. **Verdict:** a sound prototype foundation, with no
demonstrated blocker to continuing step 3, but several changes needed before
using the lab deployment instructions. The architecture, dependency tooling,
package layout, declarative Grafana provisioning and separation of the
read-only adapter from disposable writers follow the repository instructions
and sibling conventions well. All 20 tests and the requested static checks
pass; the demo works end to end, and the images also build for Linux x86_64.
The important weaknesses are the Linux key-directory instructions, health
responses that can fail or falsely report success, and fixtures that cover
less of the promised acquisition and processing contracts than their tests
suggest. The Tiled pin is reasonable, but its compatibility and database
changes deserve a more complete record. The missing timeline, queue wrapper,
view table and full freshness contract are appropriately step-3 work.

## Findings

1. **Should fix — the documented key directory is inaccessible to the
   adapter on ordinary Linux bind mounts.**
   `docs/technical/tiled-access.md:80–92`; `deploy/compose.yaml:43–44`;
   `deploy/python.Dockerfile:22`.
   The directory belongs to the deployment user and is mode `0700`, but the
   process traversing the mounted directory runs as uid 10001. Making the
   file `0644` does not grant traversal of its parent. I reproduced
   `PermissionError` inside Linux with a directory owned by uid 501 and a
   reader running as uid 10001. Docker Desktop's bind mount presented the
   directory as owned by the container user, so the macOS test concealed the
   problem. Specify and test an ownership/group or ACL arrangement that
   grants uid 10001 traversal and file read access while retaining host-side
   privacy, including any rootless user mapping. A single-file mount is
   another option, but then document container recreation after atomic file
   replacement; restarting a container need not replace a mounted inode.

2. **Should fix — malformed upstream responses break or falsely satisfy
   `/health`.**
   `src/event_timeline_tiled/tiled_http.py:53–59,62–77`;
   `src/event_timeline_tiled/adapter/app.py:33`.
   `server_version()` catches HTTP errors but does not catch JSON decoding
   errors or check the decoded type. With an HTTPX mock transport, a `200`
   HTML response and a `200` JSON list each made `/health` return `500`.
   Conversely, with a valid version response and HTML at `/search/`, health
   reported `ok`: `check_root()` accepts any `200` without validating a
   Tiled search response. A wrong upstream or maintenance proxy can
   therefore produce either outcome. Validate the small response structures,
   classify malformed responses with a sanitised error, and let a failed
   version lookup remain optional rather than suppressing every root row.
   Add focused cases for malformed JSON, wrong JSON types, search failures,
   `403`, `404` and `5xx`; retain the existing transport/authentication tests.

3. **Should fix — the processing fixtures are not yet fixtures of the full
   version-1 contract.**
   `src/event_timeline_tiled/testing/fixtures.py:195–225,245–290`;
   `tests/contract/test_fixtures.py:47–60`.
   Starts omit the required `modacor` tracing object. The uncertainty has
   units but no stated meaning, and the image has no explicit axis fields or
   axis metadata. `with_outputs=False` with `exit_status="success"`, used by
   `proc_two_parents`, emits `outputs: []` with an empty reason, contrary to
   the contract's explicit no-op explanation. Stream datums also precede
   their event, whereas §1.1 specifies the event first. Event-model 1.23.1
   validates document structure, not these additional project requirements;
   the current test checks only a subset. The registered arrays themselves
   round-trip correctly through Bluesky 1.14.6's `TiledWriter` and Tiled
   0.2.21, including `(1, 2000)` float32 curves. Complete the metadata and
   ordering, distinguish deliberately malformed cases, and assert the
   project contract, uncertainty meaning and axes in the fixture tests.

4. **Should fix — several promised step-3 boundary cases are missing from
   the retained window scenario.**
   `src/event_timeline_tiled/testing/fixtures.py:319–375`;
   `tests/contract/test_fixtures.py:15–71`.
   The scenario correctly includes the three window boundaries, spanning
   and crossing runs, 20/22-day open runs, an abort, a missing instrument,
   multiple parents and a late processing run. It does not include an
   acquisition failure, a start-UID/key mismatch, or a distinguishable MOUSE
   stopped-success case. `fewer_events` is just a successful short run;
   neither it nor normal completion supplies a synthetic `destination`
   distinction. The two attempts for `inside` both default to `attempt=1`,
   and the failure precedes the success, so latest-attempt and latest-usable
   selection return the same final result. There is no stop-time tie or
   partial-output failure. Add these cases using real writer-produced runs,
   with synthetic completion markers, coherent request/attempt identities,
   a success followed by a failed retry, and a UID tie-break case. This is
   fixture preparation, not a request to implement window search in step 2.
   Keep the planned lab descriptor capture: the simplified `roi`/`stats`
   fields and missing acquisition metadata are not verified MOUSE schemas.

5. **Should fix — live acquisition bypasses the required RunEngine fixture
   path and produces a different MOUSE layout.**
   `src/event_timeline_tiled/testing/fixtures.py:144–157`;
   `src/event_timeline_tiled/testing/seed.py:125–155`.
   History uses a real RunEngine, ophyd simulation and SupplementalData;
   live acquisition composes event-model documents directly. Live MOUSE
   consequently has neither baseline nor snapshot and emits twelve
   `mouse_eiger_measure` readings rather than the normal three described in
   plan §3.2. That weakens the daily test of stream selection and violates
   the repository's explicit acquisition-fixture rule, even though it still
   creates real Tiled trees. Drive live acquisition through an ophyd-backed
   RunEngine plan with the intended streams. A generic high-cadence stream
   can remain a separate, clearly identified scenario.

6. **Should fix — the key-check instructions expose a credential in process
   arguments and contain a broken search command.**
   `docs/technical/tiled-access.md:40–41,62–73`.
   Expanding `KEYH` into `curl -H "$KEYH"` puts the API key into curl's
   argument vector. Reading it from a file first does not prevent this.
   Initial `open("adapter.key", "w")` also commonly creates a world-readable
   file under umask `022`, before the later private-directory instructions.
   Separately, the unescaped `page[limit]` URL triggers curl URL globbing:
   the command exits with code 3 without issuing a request. Finally, the
   write-refusal request uses a JSON:API wrapper, whereas Tiled 0.2.21's
   `PostMetadataRequest` expects top-level `structure_family`, `metadata`
   and `specs`. The scoped key rejects it before body validation, but a
   mistakenly privileged key can produce `422` instead of exercising a
   valid create. Replace this block with a small Python check that reads the
   key in-process, prints only statuses, uses the actual request schema and
   fails clearly on an unexpected result. Create credentials in their
   private directory with restrictive permissions from the outset. If curl
   is retained, use a protected header input rather than a literal header
   argument, and `--globoff` or encoded brackets.

7. **Should fix — clarify the access-policy boundary and verify actual data
   access, not just a successful search.**
   `docs/technical/tiled-access.md:48–76`;
   `src/event_timeline_tiled/tiled_http.py:63`.
   Without a policy, these two read scopes permit reading the catalogue
   generally; `ETT_CATALOG_ROOTS` is application configuration, not a Tiled
   permission restriction. With a policy, a search can succeed with filtered
   or empty contents, and neither search nor `/health` proves `read:data`
   works on the arrays the viewer needs. Tiled 0.2.21's
   `get_scopes_from_api_key` and `TagBasedAccessPolicy.allowed_scopes` confirm
   the role/key/policy intersection and access-tag restriction. Explain the
   unrestricted-read consequence when no policy is installed. Provide a
   policy-specific grant example or require the administrator to supply it,
   and check a known permitted run, an actual data read, and a known denied
   sibling under that policy. The create/revoke Python method signatures and
   directory-mounted rotation sequence are otherwise correct for 0.2.21;
   keep the principal UUID and key prefix for revocation. D-003 and the
   publisher's need for `register` are correct: the default `user` role lacks
   it, while a suitably scoped admin-principal key does not inherit the
   administrative key-management scopes. See the checked
   [role definitions](https://github.com/bluesky/tiled/blob/v0.2.21/tiled/authn_database/core.py)
   and [client administration methods](https://github.com/bluesky/tiled/blob/v0.2.21/tiled/client/context.py).

8. **Should fix — secret substitution is not safe YAML or URI construction.**
   `deploy/tiled/entrypoint.py:18`;
   `deploy/tiled/config.template.yml:11,18,24`.
   Substituting arbitrary environment strings directly into YAML changes
   their interpretation. Using the template with the synthetic password
   `a: b` causes a YAML parse error; `hash #suffix` silently changes the
   parsed password. PostgreSQL passwords are also interpolated unescaped
   into database URIs, so URI delimiters can corrupt the connection string.
   Hex secrets from the example avoid this, but the configuration does not
   require them. Parse the template structure, assign password values as
   strings, construct escaped connection URLs, and serialise YAML safely;
   alternatively explicitly validate and document a restricted alphabet for
   this disposable stack. Create the rendered secret file as mode `0600`
   initially rather than changing its mode after writing.

9. **Should fix — the claimed Podman support depends on Docker-only DNS.**
   `deploy/compose.yaml:1`; `deploy/proxy/nginx.conf:6,17–18,30–31`.
   Variable `proxy_pass` targets rely on the configured nginx resolver, which
   is fixed at Docker's `127.0.0.11`. Podman does not promise that address;
   its network DNS configuration is supplied by its own networking setup.
   On such a deployment the proxy cannot resolve Grafana or the adapter.
   This is a source/configuration inference, not a reproduced Podman result.
   Make the resolver configurable or derive it from the container's resolver
   configuration, and exercise Podman before advertising support; otherwise
   explicitly scope the scaffold to Docker until that work is done. Checked
   nginx 1.27.5 and the official
   [Docker DNS documentation](https://docs.docker.com/engine/network/#dns-services)
   and [Podman network inspection documentation](https://docs.podman.io/en/latest/markdown/podman-network-inspect.1.html).

10. **Suggestion — make the demonstrated multi-user smoke test a CI gate.**
    `.github/workflows/ci.yml:28–48`; `tests/conftest.py:16–28`.
    CI currently runs SQLite with the single-user shared key and only builds
    the multi-user PostgreSQL stack. It cannot catch startup, scoped-key,
    registration, proxy-session or Infinity runtime regressions. The Linux
    x86_64 builds succeeded in this review, so I found no demonstrated
    architecture-specific build failure; this is a coverage gap. Start the
    demo with generated secrets, poll readiness, check a provisioned health
    query and denied writes, and always clean up with `down -v`. Required
    PostgreSQL search tests can reasonably arrive with search in step 3, as
    the scaffold explicitly says, but must then fail rather than silently
    skip when PostgreSQL is unavailable. Relative to the sibling CI, the
    Linux-only Python matrix is intentional; source-distribution hygiene
    checks and an installed entry-point invocation are useful additions.

11. **Suggestion — record the Tiled compatibility delta, beyond DuckDB.**
    `docs/design/decisions.md:12–21`; `pyproject.toml:74–84`.
    The pin is defensible and I found no source-level reason to revert it.
    The checked releases contain changes relevant to future work:
    [0.2.19](https://github.com/bluesky/tiled/releases/tag/v0.2.19)
    changes access blobs to tags and catalogue migrations, replaces the
    PostgreSQL metadata GIN index, adds deep search, and improves write
    retries. Ordinary `/search` remains shallow, so explicit roots are still
    right. The new index and query-plan settings mean the 0.2.18 spike's
    performance conclusions should be remeasured. See the checked
    [index change](https://github.com/bluesky/tiled/pull/1521) and
    [query-plan change](https://github.com/bluesky/tiled/pull/1543).
    [0.2.20](https://github.com/bluesky/tiled/releases/tag/v0.2.20)
    fixes exhausted CompositeClient pagination, initial data-source loading
    and SQL asset/pool handling.
    [0.2.21](https://github.com/bluesky/tiled/releases/tag/v0.2.21)
    makes a missing registered HDF5 dataset `410`, changes Tiled's client to
    `httpx2`, and fixes SQLite JSON comparisons, including explicit null
    versus missing keys. Retain `KeyPresent` for open-run discovery and map
    missing assets separately from source unavailability. PostgreSQL `Eq`
    still uses JSONB containment; `Contains` remains unsuitable for UID
    membership.

    For MOUSE, I compared the
    [0.2.8 create requests](https://github.com/bluesky/tiled/blob/v0.2.8/tiled/client/container.py)
    with the installed 0.2.21 server. The older client sends `access_blob`,
    which 0.2.21 explicitly translates, and the server strips newer asset
    `size` fields for older client User-Agents. This is positive compatibility
    evidence, not proof that an actual 0.2.8 writer completes acquisition.
    Preserve its `python-tiled/0.2.8` User-Agent and add an isolated old-client
    write/read check, followed by the planned lab check. See the checked
    [0.2.21 compatibility helpers](https://github.com/bluesky/tiled/blob/v0.2.21/tiled/server/_backcompat.py).
    Document full catalogue migration requirements before upgrading an
    existing server; the disposable demo's fresh databases do not test that.
    The newer retry protection is client-side and does not upgrade MOUSE's
    old client's timeout/retry behaviour.

12. **Suggestion — finish settings validation before using these settings
    in time arithmetic and path-based searches.**
    `src/event_timeline_tiled/settings.py:21–44`.
    `_positive` rejects NaN but accepts positive infinity; I confirmed
    `request_timeout_s="inf"` loads successfully. The URL expression accepts
    user information, query strings and fragments, while roots are
    interpolated into request URLs without segment encoding. A root
    `../metadata` produces a request to `/api/v1/metadata` rather than
    `/search/`; `?` and `#` also change its interpretation. These are trusted
    configuration inputs rather than a demonstrated remote attack, but they
    can silently target the wrong resource. Require finite positive numbers,
    parse and validate the base URL, reject embedded credentials, and encode
    catalogue path segments or reject ambiguous configuration explicitly.
    Add attrs validators for public fixture/data containers where shape,
    parent membership or positive sizes are required. The limited mypy
    relaxations for untyped fixture calls/decorators are reasonable and leave
    the adapter strict; runtime contract assertions are the more useful
    improvement than widening `Any` exceptions.

13. **Suggestion — document a concrete lab-facing TLS and Viewer-account
    deployment before changing the loopback bindings.**
    `deploy/compose.yaml:19,26–27,80–81`;
    `deploy/grafana/Dockerfile:5–11`; `docs/technical/demo-stack.md:6–9`.
    The current loopback-only ports are an appropriate local-demo default.
    Simply exposing the HTTP proxy to the lab would carry Grafana passwords
    and session cookies without transport protection. Specify the TLS
    terminator, public URL, trusted forwarded scheme/host handling, secure
    Grafana session cookies, and creation of operator Viewer accounts. Keep
    the toy Tiled provider and bootstrap admin confined to the demo, and
    restrict the real adapter key with the lab's policy. Grafana 13.3.1
    defaults `cookie_secure` to false; sanitisation remains enabled and
    anonymous access is explicitly disabled here. Checked the pinned
    [Grafana defaults](https://github.com/grafana/grafana/blob/v13.3.1/conf/defaults.ini).
    The root bootstrap/seed processes have only disposable volume access and
    no host-control mounts; this is acceptable for the prototype. A one-shot
    ownership initialiser would allow the long-lived seeder to run as uid
    10001. Separate key volumes would further avoid mounting `seeder.key`
    into the adapter at all, although its current `0600` root ownership
    prevents that adapter process from reading it.

## Other checks and scope

The nginx 1.27.5 image's authentication subrequest behaved as intended:
anonymous and invalid-session image requests returned `401`, a signed-in
request reached the adapter (`404`, since step 2 has no image endpoint), and
reusing a logged-out session returned `401`. The internal auth location,
body suppression and stripping of browser Authorization/Cookie headers on
the adapter hop are correct in the configuration. Non-successful auth
responses fail closed, consistent with the checked
[nginx auth module](https://nginx.org/en/docs/http/ngx_http_auth_request_module.html).
The WebSocket upgrade map is present. Grafana's plugins are pinned and
installed at build time, preinstallation/automatic update are disabled,
provisioning is mounted read-only, dashboard and datasource editing are
disabled, and Infinity is restricted to the adapter with a backend query.
The demo bootstrap's admin-principal seeder key is limited to the six
declared scopes; the adapter key has only the two read scopes. Environment
files and keys are ignored by Git, and no actual credential was found in
the reviewed configuration. No change to D-003 is required for this demo.

The Python tooling and technical/user/design docs split match the sibling
conventions. The plan's `python-semantic-release` automation/configuration
has not been implemented; this remains deferred release work. The changelog
marker is retained. The synchronous health handler correctly runs outside
the async event loop, and normal lifespan shutdown closes the shared HTTPX
client. Resource ownership is simple enough for the current single reader;
additional step-3 resources will need explicit exceptional-shutdown cleanup.

## What I ran

- `uv run --locked pytest`: **20 passed**, with two dependency deprecation
  warnings (SQLAlchemy loader strategy and Alembic path separator).
- `uv run --locked ruff check .`, `ruff format --check .`, `mypy src` and
  `mkdocs build --strict`: **passed** against the original scaffold.
- `uv build`: **passed**. Inspected the wheel and sdist: `py.typed` is
  present; the wheel excludes repository deployment/docs/tests; no secret
  or generated data files appeared in either archive.
- Compose configuration validation with `deploy/.env.example`, both with
  and without `--profile demo`: **passed**.
- Built and started a separate `ett-scaffold-review` project on Docker
  Desktop **29.9.3**, with generated secrets in a private temporary env file
  and separate ports/volumes. The existing development stack was left
  alone. A provisioned Grafana datasource query returned a health frame
  containing `ok` through the full proxy/Grafana/Infinity/adapter/Tiled path.
  The adapter key searched successfully, read a registered float32 HDF5
  curve, and received `401` for a valid node-create request. Exercised the
  proxy session cases described above.
- Built all Compose build targets again with
  `DOCKER_DEFAULT_PLATFORM=linux/amd64`; adapter, Tiled and Grafana image
  inspection confirmed **linux/amd64**, and the demo tools built too.
- Ran disposable/in-memory reproductions for health response parsing,
  settings/path handling, curl globbing, Linux directory ownership and YAML
  secret substitution. These added no tests or code to the repository.
- Read the installed pinned Tiled 0.2.21, Bluesky 1.14.6 and event-model
  1.23.1 sources, the linked Tiled release/source changes, Grafana 13.3.1
  defaults, and nginx 1.27.5 auth-module source/documentation.
- Removed the isolated stack with `down -v` and deleted its temporary
  credential file. No commit and no implementation changes were made.

## What I could not verify

No production service or hardware was contacted. I did not run GitHub
Actions itself, run the Python suite on a native Ubuntu runner, perform an
in-place Tiled database upgrade, or run an actual Tiled 0.2.8 client against
0.2.21. The x86_64 image builds ran under Docker Desktop emulation. I did
not exercise a configured tag/external access policy, actual session expiry
by time, WebSocket traffic, header stripping with an echo endpoint, or
rendered image caching; the image endpoint does not exist yet. Podman,
real MOUSE descriptor fidelity, and old-client compatibility on the lab
server remain unverified. No PostgreSQL search-contract test exists in the
current suite, and this review did not repeat the spike's query-plan or
pagination benchmark. The strict docs build also passed after adding this
response, with an informational notice that the new page is not in `nav`;
the brief prohibits changing `mkdocs.yml`.
