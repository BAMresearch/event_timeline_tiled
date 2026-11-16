"""Runs inside the demo-seed image with MOUSE's client stack (see old_client_check.py):
write a measurement, an open run and a processing run into the demo Tiled."""

import json
import time

import tiled
from tiled.client import from_uri

from event_timeline_tiled.testing import FixtureWriter

assert tiled.__version__ == "0.2.8", tiled.__version__
client = from_uri("http://tiled:8000", api_key=open("/secrets/seeder.key").read().strip())
fw = FixtureWriter(client, "/data/processed")
now = time.time()
md = {
    "facility": "BAM",
    "instrument": "MOUSE",
    "sampleid": "S-oldclient",
    "plan_name": "old_client_check",
}
closed = fw.mouse_measurement(md, now - 600, now - 300)
opened = fw.mouse_measurement({**md, "sampleid": "S-oldclient-open"}, now - 200, None)
processing = fw.processing_run([closed], now - 250, now - 240)
print(json.dumps({"closed": closed, "open": opened, "processing": processing}))
