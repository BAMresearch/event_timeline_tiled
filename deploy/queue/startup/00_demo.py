"""RE Manager startup for the demo stack and tests: simulated devices only.

The queue-status panel reads this RE Manager's state; the plans write no runs.
"""

import bluesky.plan_stubs as bps
from bluesky import RunEngine
from bluesky.plans import count
from ophyd.sim import det1

RE = RunEngine({})


def demo_measurement(duration: float = 30.0):  # noqa: ANN201
    """Wait ``duration`` seconds, then count a simulated detector once."""
    yield from bps.sleep(duration)
    yield from count([det1])
