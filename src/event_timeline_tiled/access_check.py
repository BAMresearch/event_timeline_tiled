"""Check the adapter's Tiled key before use: ``ett-check-access``.

Reads the key exactly as the adapter does (``ETT_TILED_API_KEY_FILE``, the
same settings), so running it inside the adapter container also checks the
key file's mount and permissions. Strictly read-only: it never attempts a
write, because a wrongly privileged key would then really write. Instead it
checks the key's scopes as Tiled reports them, which is what Tiled enforces.

Checks, each printed as PASS/FAIL with a status (never the key):

1. the key file is readable and Tiled accepts the key;
2. the key's scopes are exactly ``read:metadata`` and ``read:data``;
3. every configured catalogue root can be searched;
4. for each ``--run PATH``: its metadata and a small piece of real data
   (the first array or table below it) can be read;
5. for each ``--denied PATH``: it cannot be read (with an access policy).

Exit status 0 only if every check passes.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from .settings import Settings, _roots
from .tiled_http import TiledReader

READ_SCOPES = frozenset({"read:metadata", "read:data"})


def run_checks(
    settings: Settings,
    runs: list[str],
    denied: list[str],
    reader: TiledReader,
    out: Callable[[str], None] = print,
) -> bool:
    results: list[bool] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results.append(ok)
        out(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")

    key = reader.whoami_key(settings.tiled_api_key[:8])
    check("key accepted", key.status == 200, f"HTTP {key.status}")
    if key.status == 200:
        scopes = frozenset(key.scopes or ())
        extra, missing = sorted(scopes - READ_SCOPES), sorted(READ_SCOPES - scopes)
        check(
            "key scopes",
            key.scopes is not None and not extra and not missing,
            "not reported by Tiled"
            if key.scopes is None
            else f"{', '.join(sorted(scopes))}"
            + (f"; remove {', '.join(extra)}" if extra else "")
            + (f"; missing {', '.join(missing)}" if missing else ""),
        )
    for root in settings.catalog_roots or ("",):
        check(f"search /{root}", (s := reader.check_root(root)).status == "ok", s.status)
    for path in runs:
        status = reader.metadata_status(path)
        check(f"metadata {path}", status == 200, f"HTTP {status}")
        sample = reader.read_sample(path)
        check(f"data below {path}", sample.ok, sample.detail)
    for path in denied:
        status = reader.metadata_status(path)
        check(f"denied {path}", status in {401, 403, 404}, f"HTTP {status}")
    out(f"{sum(results)}/{len(results)} checks passed")
    return all(results)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--run", action="append", default=[], metavar="PATH",
                   help="a run the key must be able to read (repeatable)")  # fmt: skip
    p.add_argument("--denied", action="append", default=[], metavar="PATH",
                   help="a node the key must not be able to read (repeatable)")  # fmt: skip
    args = p.parse_args(argv)
    paths = [*args.run, *args.denied]
    try:
        # Same rules as catalogue roots, and never empty (that would check the root).
        if len(_roots(tuple(paths))) != len(paths) or not all(p.strip("/") for p in paths):
            raise ValueError("--run and --denied need non-empty paths")
        settings = Settings.from_env()
    except (OSError, ValueError) as exc:
        # OSError: the key file is missing or unreadable (e.g. directory permissions).
        sys.exit(f"FAIL  settings: {type(exc).__name__}: {exc}")
    reader = TiledReader(settings)
    try:
        ok = run_checks(settings, args.run, args.denied, reader)
    finally:
        reader.close()
    sys.exit(0 if ok else 1)
