"""Health check of the demo Tiled: its API answers on 127.0.0.1:8000.

A script, so the check's command has no argument with spaces in it: Podman's
Docker-compatible API (4.9) splits health-check arguments at spaces, which
turned ``python -c "<code>"`` into ``python -c import``.
"""

import urllib.request

urllib.request.urlopen("http://127.0.0.1:8000/api/v1/", timeout=5)
