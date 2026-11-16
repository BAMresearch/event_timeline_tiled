"""Render the Tiled config from the environment, then start Tiled.

Tiled does not expand environment variables in its config file. Missing
variables are an error, so a half-filled config never starts. Values are
pasted into YAML and database URLs unescaped, so they are restricted to
letters, digits, "-" and "_" (e.g. ``secrets.token_hex(32)``); anything else
is refused rather than silently changing the configuration.
"""

import os
import re
import sys
from pathlib import Path

SAFE = re.compile(r"^[A-Za-z0-9_-]{16,}$")
PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)\}")

template = Path("/etc/tiled/config.template.yml").read_text()
names = set(PLACEHOLDER.findall(template))
missing = sorted(n for n in names if not os.environ.get(n))
if missing:
    sys.exit(f"tiled entrypoint: missing environment variables: {', '.join(missing)}")
unsafe = sorted(n for n in names if not SAFE.match(os.environ[n]))
if unsafe:
    sys.exit(
        "tiled entrypoint: these must be at least 16 characters of A-Z, a-z, 0-9, '-', '_' "
        f"(generate with secrets.token_hex(32)): {', '.join(unsafe)}"
    )
rendered = PLACEHOLDER.sub(lambda m: os.environ[m.group(1)], template)
out = "/tmp/tiled-config.yml"
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(rendered)
os.execvp("tiled", ["tiled", "serve", "config", out, "--host", "0.0.0.0", "--port", "8000"])
