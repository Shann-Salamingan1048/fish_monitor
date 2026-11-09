"""Build desktop notification snapshots without holding up Tk's event loop."""
import logging
import queue
import threading
import calibration
import config
import sensor_control
from predictions import build_sensor_alerts


def build_snapshot():
    with config.sensor_lock:
        version = config.settings_version
        readings = config.sensor_data.copy()
        clarity = calibration.clarity_readout(readings.get("turbidity"))
        alerts = build_sensor_alerts(readings, config.sensor_history)
        controls = sensor_control.get_status()
        import water_control
        controls["waterControl"] = water_control.get_status()
    readings["turbidity"] = clarity
    return version, readings, controls, alerts


class SnapshotWorker:
    def __init__(self, builder=build_snapshot):
        self.builder = builder
        self.results = queue.Queue(maxsize=1)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name='AquaPi GUI snapshots', daemon=True)
        self.thread.start()
        self.request()

    def request(self):
        self._wake.set()

    def close(self):
        self._stop.set()
        self._wake.set()

    def _run(self):
        while not self._stop.is_set():
            self._wake.wait(1.0)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                result = self.builder()
                try:
                    self.results.get_nowait()
                except queue.Empty:
                    pass
                self.results.put_nowait(result)
            except Exception:
                logging.exception('Unable to prepare desktop readings')
            # Coalesce bursts; at most four snapshots per second, no backlog.
            self._stop.wait(0.25)
