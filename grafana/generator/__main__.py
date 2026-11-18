"""Write the generated dashboards: ``uv run --locked python grafana/generator``.

The JSON files under ``grafana/dashboards/`` are what Grafana provisions and
what is committed; edit the generator, not the JSON, and run this. A test
fails if the two differ.
"""

import json
from pathlib import Path

import overview_dashboard
import timeline_dashboard

DASHBOARDS = Path(__file__).resolve().parents[1] / "dashboards"
GENERATED = {"timeline.json": timeline_dashboard.dashboard,
             "overview.json": overview_dashboard.dashboard}  # fmt: skip
"""The generated dashboards; health.json is written by hand."""


def render(content: dict[str, object]) -> str:
    return json.dumps(content, indent=1, ensure_ascii=True) + "\n"


if __name__ == "__main__":
    for name, build in GENERATED.items():
        (DASHBOARDS / name).write_text(render(build()))
