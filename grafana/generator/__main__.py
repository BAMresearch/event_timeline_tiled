"""Write the generated dashboards: ``uv run --locked python grafana/generator``.

The JSON files under ``grafana/dashboards/`` are what Grafana provisions and
what is committed; edit the generator, not the JSON, and run this. A test
fails if the two differ.
"""

import json
from pathlib import Path

from timeline_dashboard import dashboard

DASHBOARDS = Path(__file__).resolve().parents[1] / "dashboards"


def render(content: dict[str, object]) -> str:
    return json.dumps(content, indent=1, ensure_ascii=True) + "\n"


if __name__ == "__main__":
    (DASHBOARDS / "timeline.json").write_text(render(dashboard()))
