"""A short history of the hub's and the cameras' health, for the page's sparklines.

hubd reads the hub's health and every camera's status every 2 s anyway; this keeps one
point every SAMPLE_S for the last hour, per metric, so the page can draw a small line
next to each value (user, 2026-11-16: it would have shown the memory leak of that day
at a glance). In memory only: a restart starts a new history.
"""
import collections
import threading
import time

SAMPLE_S = 10.0
POINTS = 360                       # one hour at 10 s

# Which values are kept: hub health (hub_health()) and camera status (camnode /status).
HUB_METRICS = ("temp_c", "cpu_mhz", "load", "mem_available_mb", "swap_used_mb", "fan_rpm")
CAMERA_METRICS = ("temp_c", "wifi_dbm", "wifi_mbps", "free_gb", "load")


class HealthHistory:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.last_sample = None
        self.hub = {name: collections.deque(maxlen=POINTS) for name in HUB_METRICS}
        self.cameras = {}

    def maybe_record(self, hub_health, camera_statuses):
        """Called on every poll; keeps a point only every SAMPLE_S."""
        now = self.clock()
        if self.last_sample is not None and now - self.last_sample < SAMPLE_S:
            return False
        self.last_sample = now
        with self.lock:
            for name, points in self.hub.items():
                points.append((hub_health or {}).get(name))
            for camera, status in camera_statuses.items():
                series = self.cameras.setdefault(camera, {name: collections.deque(maxlen=POINTS) for name in CAMERA_METRICS})
                for name, points in series.items():
                    points.append((status or {}).get(name))          # None while offline: a gap in the line
        return True

    def as_dict(self):
        with self.lock:
            return {"interval_s": SAMPLE_S,
                    "hub": {name: list(points) for name, points in self.hub.items()},
                    "cameras": {camera: {name: list(points) for name, points in series.items()}
                                for camera, series in self.cameras.items()}}
