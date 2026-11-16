"""Keep the demo stack's RE Manager busy, so the queue-status panel has
something to show. Demo use only: it controls the demo's own disposable RE
Manager, never an instrument's (the adapter itself only reads queues).

Opens the environment, then whenever the queue is empty and idle, adds two
``demo_measurement`` items of random length and starts the queue.
"""

from __future__ import annotations

import argparse
import random
import sys
import time


def main(argv: list[str] | None = None) -> None:
    from bluesky_queueserver_api.item import BPlan
    from bluesky_queueserver_api.zmq import REManagerAPI

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--zmq-control-addr", required=True)
    p.add_argument("--pause", type=float, default=60.0, help="seconds idle between batches")
    args = p.parse_args(argv)
    rng = random.Random(11)
    api = REManagerAPI(zmq_control_addr=args.zmq_control_addr, timeout_recv=10)
    while True:
        try:
            status = api.status(reload=True)
            if not status["worker_environment_exists"]:
                if status["manager_state"] == "idle":
                    api.environment_open()
                    api.wait_for_idle(timeout=120)
            elif status["manager_state"] == "idle" and status["items_in_queue"] == 0:
                time.sleep(args.pause)
                for _ in range(2):
                    api.item_add(BPlan("demo_measurement", duration=rng.uniform(30, 120)))
                api.queue_start()
                print("demo-queue: started two demo measurements", file=sys.stderr)
        except Exception as exc:  # the manager may still be starting
            print(f"demo-queue: {type(exc).__name__}", file=sys.stderr)
        time.sleep(5)
