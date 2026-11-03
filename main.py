"""Run the monitor once: desktop + web, or --headless for web only."""
import argparse
import threading
import signal
import webbrowser
import ipaddress
import subprocess

import config
# Comment this line to hide Developer option and block its routes; restart main.py.
config.DEVELOPER_OPTIONS = True
import calibration
import fan_control
import feeding_control
import water_control
import water_schedule
import sensor_control
from feeding_scheduler import feeding_schedule_worker
from mcu_comm import get_communicator
from flask_server import flask_app, camera_watchdog, stop_camera_if_needed
from werkzeug.serving import make_server


def local_network_urls():
    """Find this Pi's current IPv4 links without an internet connection."""
    if config.WEB_HOST in {'127.0.0.1', 'localhost', '::1'}:
        return []
    try:
        if config.WEB_HOST == '0.0.0.0':
            result = subprocess.run(['hostname', '-I'], capture_output=True,
                                    text=True, timeout=2, check=True)
            addresses = result.stdout.split()
        else:
            addresses = [config.WEB_HOST]
        urls = []
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                continue
            if address.version == 4 and not address.is_loopback and not address.is_unspecified:
                url = f'http://{address}:{config.WEB_PORT}'
                if url not in urls:
                    urls.append(url)
        return urls
    except (OSError, subprocess.SubprocessError):
        return []


def main():
    parser = argparse.ArgumentParser(description='Fish monitoring local control system')
    parser.add_argument('--headless', action='store_true', help='Run without a desktop display')
    parser.add_argument('--open-browser', action='store_true', help='Open the local dashboard')
    args = parser.parse_args()
    config.init_database()
    config.load_persisted_settings()
    fan_control.initialize()
    feeding_control.initialize()
    calibration.initialize()
    sensor_control.initialize()
    water_schedule.initialize()
    stop_event = threading.Event()
    # Binding before hardware startup also prevents two copies from using the Mega.
    server = make_server(config.WEB_HOST, config.WEB_PORT, flask_app, threaded=True)
    root = None
    communicator = get_communicator()
    try:
        if not args.headless:
            import tkinter as tk
            from tkinter_gui import FocusedMonitorApp
            root = tk.Tk()
            app = FocusedMonitorApp(root)
            communicator.app_callback = app.update_labels
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=camera_watchdog, daemon=True).start()
        communicator.start_reading(config.MEGA_PORT)
        worker = threading.Thread(target=feeding_schedule_worker, args=(stop_event,), daemon=True)
        worker.start()
        water_worker = threading.Thread(target=water_schedule.worker, args=(stop_event,), daemon=True)
        water_worker.start()
        url = f'http://localhost:{config.WEB_PORT}'
        print(f'Dashboard: {url} (bind address: {config.WEB_HOST})')
        for phone_url in local_network_urls():
            print(f'Phone / same Wi-Fi: {phone_url}')
        if args.open_browser:
            webbrowser.open(url)
        def close(*_):
            stop_event.set()
            if root is not None:
                root.quit()
        signal.signal(signal.SIGTERM, close)
        signal.signal(signal.SIGINT, close)
        if root is not None:
            root.protocol('WM_DELETE_WINDOW', close)
            root.mainloop()
        else:
            stop_event.wait()
    finally:
        stop_event.set()
        # Close the write gate before scheduler joins or sensor callbacks.
        config.begin_shutdown()
        # Wait for the scheduler to leave its command path before shutdown stops.
        if 'worker' in locals():
            worker.join(timeout=3)
        if 'water_worker' in locals():
            water_worker.join(timeout=3)
        try:
            feeding_control.stop_feeder()
        except ValueError:
            pass
        fan_control.stop_for_shutdown()
        water_control.stop_all_for_shutdown()
        communicator.stop_reading()
        with config.camera_lock:
            stop_camera_if_needed()
        # shutdown() requires a running serve_forever thread.
        if 'worker' in locals():
            server.shutdown()
        server.server_close()
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


if __name__ == '__main__':
    main()
