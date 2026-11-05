# flask_server.py
import threading
import time
import cv2
import uuid
import sqlite3
from urllib.parse import urlsplit
from flask import Flask, render_template, jsonify, request, Response, session, abort, redirect
import config
import admin_sessions
import admin_credentials
import calibration

# Try to import picamera2, but handle it gracefully if not available
try:
    from picamera2 import Picamera2

    PICAMERA_AVAILABLE = True
except ImportError:
    PICAMERA_AVAILABLE = False
    Picamera2 = None
    print("Warning: picamera2 module not found. Camera streaming will be disabled.")

from config import (
    APP_SECRET_KEY, ADMIN_PIN,
    CAMERA_WIDTH, CAMERA_HEIGHT, CAMERA_FPS_DELAY, JPEG_QUALITY,
    CAMERA_VIEWER_TIMEOUT, WEBSITE_VIEWER_TIMEOUT,
    get_servo_position, update_servo_position_for_command,
    update_limits_from_payload, get_public_limits,
    sensor_data, sensor_history, active_camera_viewers, active_website_viewers,
    sensor_lock, camera_lock, website_viewer_lock,
    is_admin, require_admin_json,
    send_to_output_serial_ports, build_camera_serial_message,
    load_persisted_settings, clear_prediction_alert_cache
)
from feeding_scheduler import feeding_schedule_response
from predictions import build_sensor_alerts, format_float_status
import water_control
import sensor_control
import feeding_control
import fan_control

flask_app = Flask(__name__)
flask_app.secret_key = APP_SECRET_KEY

picam2 = None
latest_camera_frame = None
latest_camera_frame_sequence = 0
camera_frame_condition = threading.Condition(camera_lock)
camera_capture_thread = None
# -------------------------
# Total Website Viewer Limit
# -------------------------

def _cleanup_expired_website_viewers_locked():
    now = time.time()
    expired = [
        viewer_id for viewer_id, last_seen in active_website_viewers.items()
        if now - last_seen > WEBSITE_VIEWER_TIMEOUT
    ]
    for viewer_id in expired:
        active_website_viewers.pop(viewer_id, None)


def _website_viewer_id():
    viewer_id = session.get("website_viewer_id")
    if not viewer_id:
        viewer_id = "web-" + uuid.uuid4().hex
        session["website_viewer_id"] = viewer_id
    return viewer_id


def _get_website_viewer_count():
    with website_viewer_lock:
        _cleanup_expired_website_viewers_locked()
        return len(active_website_viewers)


def _touch_website_viewer():
    viewer_id = _website_viewer_id()
    with website_viewer_lock:
        _cleanup_expired_website_viewers_locked()
        if viewer_id not in active_website_viewers and len(active_website_viewers) >= config.MAX_WEBSITE_VIEWERS:
            return False, len(active_website_viewers)
        active_website_viewers[viewer_id] = time.time()
        return True, len(active_website_viewers)


@flask_app.before_request
def enforce_total_website_viewer_limit():
    if config.shutting_down and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        return jsonify({'ok': False, 'error': 'System is off. Restart the application to resume.'}), 503
    # Static assets are fetched only after the page itself is admitted. Keeping
    # them outside the counter reduces lock traffic on the Raspberry Pi.
    if request.path.startswith("/static/"):
        return None

    allowed, viewers = _touch_website_viewer()
    if allowed:
        admin_sessions.is_admin()
        return None

    payload = {
        "ok": False,
        "error": "Website viewer limit reached",
        "viewers": viewers,
        "max_viewers": config.MAX_WEBSITE_VIEWERS,
    }
    if request.path.startswith("/api/"):
        return jsonify(payload), 429
    return (
        "<h1>Viewer limit reached</h1>"
        f"<p>This dashboard currently allows {config.MAX_WEBSITE_VIEWERS} active viewer(s). "
        "Please try again after another viewer leaves.</p>",
        503,
        {"Retry-After": str(WEBSITE_VIEWER_TIMEOUT)},
    )


@flask_app.route("/api/viewers/status")
def website_viewer_status():
    viewers = _get_website_viewer_count()
    return jsonify({
        "ok": True,
        "active_viewers": viewers,
        "max_viewers": config.MAX_WEBSITE_VIEWERS,
    })


@flask_app.route("/api/viewers/stop", methods=["POST"])
def website_viewer_stop():
    viewer_id = session.get("website_viewer_id")
    with website_viewer_lock:
        if viewer_id:
            active_website_viewers.pop(viewer_id, None)
        viewers = len(active_website_viewers)
    return jsonify({"ok": True, "active_viewers": viewers})


# -------------------------
# Flask Web Routes
# -------------------------

@flask_app.route("/")
def index():
    return render_template("index.html")


@flask_app.route("/camera")
def camera_page():
    return render_template("camera.html")


@flask_app.route("/graphs")
def graphs_page():
    return render_template("graphs.html")


@flask_app.route('/feeding')
def feeding_page():
    return render_template('feeding.html')


@flask_app.route('/controls')
def controls_page():
    return redirect('/water-changes')

@flask_app.route('/water-changes')
def water_changes_page():
    return render_template('water_changes.html')

@flask_app.route('/cooling-fan')
def cooling_fan_page():
    return render_template('cooling_fan.html')


@flask_app.route('/sensors')
def sensors_page():
    return render_template('sensors.html')


@flask_app.route('/settings')
def settings_page():
    return render_template('settings.html')


@flask_app.route('/access')
def access_page():
    return render_template('access.html')


@flask_app.context_processor
def developer_navigation():
    return {'developer_visible': bool(config.DEVELOPER_OPTIONS and is_admin())}


@flask_app.route('/developer')
def developer_page():
    if not config.DEVELOPER_OPTIONS:
        abort(404)
    if not is_admin():
        return redirect('/access')
    return render_template('developer.html')


@flask_app.route('/api/developer/calibration', methods=['GET', 'PUT', 'POST'])
def developer_calibration_api():
    if not config.DEVELOPER_OPTIONS:
        abort(404)
    error = require_admin_json()
    if error:
        return error
    if request.method == 'GET':
        return jsonify(ok=True, calibration=calibration.status())
    # Same-origin JSON only; prevent cross-site authenticated calibration writes.
    if request.headers.get('Sec-Fetch-Site') == 'cross-site':
        return jsonify(ok=False, error='Use the AquaPi calibration page.'), 403
    if request.headers.get('Origin') and urlsplit(request.headers['Origin']).netloc != request.host:
        return jsonify(ok=False, error='Use the AquaPi calibration page.'), 403
    data = request.get_json(silent=True)
    try:
        if request.method == 'POST':
            if not isinstance(data, dict) or set(data) != {'active'}:
                raise ValueError('Provide the calibration mode.')
            result = calibration.set_active(data['active'])
        else:
            result = calibration.save(data)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except (sqlite3.Error, OSError):
        return jsonify(ok=False, error='Calibration was not saved. Check free space and database permissions.'), 503
    return jsonify(ok=True, calibration=result)


@flask_app.route("/api/auth/status")
def auth_status():
    return jsonify({
        "ok": True,
        "role": "admin" if is_admin() else "audience",
        "adminSessions": admin_sessions.status(),
        "developerOptions": bool(config.DEVELOPER_OPTIONS and is_admin()),
        "servo": get_servo_position(),
        "limits": get_public_limits(),
        "waterControl": water_control.get_status()
    })


@flask_app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True)
    pin = data.get("pin") if isinstance(data, dict) else None
    with admin_credentials.lock:
        if not admin_credentials.verify(pin):
            admin_sessions.logout()
            return jsonify({"ok": False, "error": "Invalid admin PIN"}), 401
        if not admin_sessions.login():
            return jsonify(ok=False, role='audience', adminSessions=admin_sessions.status(),
                           error=f'Admin sign-in limit reached ({config.MAX_ADMIN_SESSIONS}). Sign out on another device first.'), 409
    return jsonify(ok=True, role='admin', adminSessions=admin_sessions.status())


@flask_app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    admin_sessions.logout()
    return jsonify({"ok": True, "role": "audience"})


@flask_app.route("/api/limits", methods=["GET", "PUT"])
def limits_api():
    if request.method == "GET":
        return jsonify({"ok": True, "limits": get_public_limits()})

    admin_error = require_admin_json()
    if admin_error:
        return admin_error

    try:
        changed = update_limits_from_payload(request.get_json(force=True))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "changed": changed, "limits": get_public_limits()})


@flask_app.route('/api/fan', methods=['GET', 'POST'])
def fan_api():
    if request.method == 'GET':
        return jsonify(ok=True, fan=fan_control.get_status())
    error = require_admin_json()
    if error:
        return error
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {'mode'}:
        return jsonify(ok=False, error='Provide a fan mode: AUTO, ON or OFF.'), 400
    try:
        return jsonify(ok=True, fan=fan_control.set_mode(payload['mode']))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@flask_app.route("/api/water-control", methods=["GET", "POST"])
def water_control_api():
    if request.method == "GET":
        return jsonify({"ok": True, "waterControl": water_control.get_status()})

    admin_error = require_admin_json()
    if admin_error:
        return admin_error

    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action", "")).strip().lower()

    try:
        status = water_control.manual_action(action)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "waterControl": status})


@flask_app.route("/api/feeding-schedules", methods=["GET", "POST"])
def feeding_schedules_api():
    if request.method == "GET":
        return jsonify(feeding_schedule_response())

    admin_error = require_admin_json()
    if admin_error:
        return admin_error

    payload = request.get_json(silent=True) or {}
    try:
        schedule = config.create_feeding_schedule(
            payload.get("day"),
            payload.get("time"),
            payload.get("enabled", True),
            payload.get("cycles", 3),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    response = feeding_schedule_response()
    response["schedule"] = schedule
    return jsonify(response), 201


@flask_app.route("/api/feeding-schedules/<int:schedule_id>", methods=["PUT", "DELETE"])
def feeding_schedule_item_api(schedule_id):
    admin_error = require_admin_json()
    if admin_error:
        return admin_error

    existing = config.get_feeding_schedule(schedule_id)
    if existing is None:
        return jsonify({"ok": False, "error": "Feeding schedule not found"}), 404

    if request.method == "DELETE":
        config.delete_feeding_schedule(schedule_id)
        return jsonify(feeding_schedule_response())

    payload = request.get_json(silent=True) or {}
    try:
        schedule = config.update_feeding_schedule(
            schedule_id,
            payload.get("day", existing["day"]),
            payload.get("time", existing["time"]),
            payload.get("enabled", existing["enabled"]),
            payload.get("cycles", existing["cycles"]),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    response = feeding_schedule_response()
    response["schedule"] = schedule
    return jsonify(response)


@flask_app.route('/api/sensor-controls', methods=['GET', 'PUT'])
def sensor_controls():
    if request.method == 'PUT':
        error = require_admin_json()
        if error:
            return error
        data = request.get_json(silent=True)
        try:
            if not isinstance(data, dict) or set(data) != {'enabled'}:
                raise ValueError('Expected an enabled object containing sensor names and booleans.')
            status = sensor_control.apply(data['enabled'])
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        return jsonify(ok=True, sensorControls=status)
    return jsonify(ok=True, sensorControls=sensor_control.get_status())


@flask_app.route("/api/sensors")
def sensors():
    with sensor_lock:
        sensor_snapshot = sensor_data.copy()
        history_snapshot = {
            key: list(values)
            for key, values in sensor_history.items()
        }

        alerts = build_sensor_alerts(sensor_snapshot, history_snapshot)
        controls = sensor_control.get_status()
        water_status = water_control.get_status()

    response_data = sensor_snapshot.copy()
    response_data["float_stat"] = format_float_status(response_data.get("float_stat"))
    response_data["turbidityCategory"] = calibration.category_for_label(sensor_snapshot.get("turbidity"))
    response_data["turbidityCustom"] = calibration.is_custom_label(sensor_snapshot.get("turbidity"))
    response_data["alerts"] = alerts
    response_data["sensorControls"] = controls
    response_data["waterControl"] = water_status
    response_data["limits"] = get_public_limits()
    response_data["role"] = "admin" if is_admin() else "audience"
    response_data["adminSessions"] = admin_sessions.status()
    response_data["servo"] = get_servo_position()
    response_data["websiteViewers"] = {
        "active": _get_website_viewer_count(),
        "max": config.MAX_WEBSITE_VIEWERS,
    }
    response_data["feeder"] = feeding_control.get_status()
    response_data["fan"] = fan_control.get_status()
    age = time.time() - config.last_sensor_received_at if config.last_sensor_received_at else None
    response_data["connection"] = {"sensorAgeSeconds": age, "stale": (age is None or age > 10) and not response_data["sensorControls"]["allDisabled"],
                                   "input": bool(config.input_serial_ports), "output": bool(config.output_serial_ports)}

    return jsonify(response_data)


@flask_app.route("/api/control", methods=["POST"])
def control():
    admin_error = require_admin_json()
    if admin_error:
        return admin_error

    data = request.get_json(force=True)
    command = str(data.get("command", "")).strip().lower()

    valid_commands = {"feed", "up", "down", "left", "right", "center"}
    if command not in valid_commands:
        return jsonify({
            "ok": False,
            "error": "Invalid command"
        }), 400

    if command == 'feed':
        try:
            return jsonify(ok=True, feeder=feeding_control.request_feed(data.get('cycles', 1)), servo=get_servo_position())
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 409
    serial_message = build_camera_serial_message(command)
    sent = config.send_to_identified_output_serial_ports(serial_message)
    if not sent:
        return jsonify(ok=False, error="MegaOutput disconnected; command not sent"), 503
    position = update_servo_position_for_command(command)

    return jsonify({
        "ok": sent,
        "command": command,
        "serial": serial_message.strip(),
        "servo": position
    })


# -------------------------
# Camera Viewer Tracking API
# -------------------------

def cleanup_expired_camera_viewers_locked():
    now = time.time()
    expired_viewer_ids = [
        viewer_id
        for viewer_id, last_seen in active_camera_viewers.items()
        if now - last_seen > CAMERA_VIEWER_TIMEOUT
    ]
    for viewer_id in expired_viewer_ids:
        active_camera_viewers.pop(viewer_id, None)
    return expired_viewer_ids


def get_active_camera_viewer_count_locked():
    cleanup_expired_camera_viewers_locked()
    return len(active_camera_viewers)


def register_camera_viewer(viewer_id):
    if not viewer_id:
        return False, 0
    with camera_lock:
        viewer_count = get_active_camera_viewer_count_locked()
        if viewer_id not in active_camera_viewers and viewer_count >= config.MAX_CAMERA_VIEWERS:
            return False, viewer_count
        active_camera_viewers[viewer_id] = time.time()
        return True, get_active_camera_viewer_count_locked()


def unregister_camera_viewer(viewer_id):
    with camera_lock:
        if viewer_id:
            active_camera_viewers.pop(viewer_id, None)
        return get_active_camera_viewer_count_locked()


@flask_app.route("/api/camera/viewer/start", methods=["POST"])
def camera_viewer_start():
    data = request.get_json(silent=True) or {}
    viewer_id = str(data.get("viewerId", "")).strip()
    if not viewer_id:
        return jsonify({"ok": False, "error": "Missing viewerId"}), 400
    allowed, viewers = register_camera_viewer(viewer_id)
    if not allowed:
        return jsonify({"ok": False, "error": "Camera viewer limit reached", "viewers": viewers,
                        "max_viewers": config.MAX_CAMERA_VIEWERS}), 429
    return jsonify({"ok": True, "viewerId": viewer_id, "viewers": viewers,
                    "max_viewers": config.MAX_CAMERA_VIEWERS})


@flask_app.route("/api/camera/viewer/heartbeat", methods=["POST"])
def camera_viewer_heartbeat():
    data = request.get_json(silent=True) or {}
    viewer_id = str(data.get("viewerId", "")).strip()
    if not viewer_id:
        return jsonify({"ok": False, "error": "Missing viewerId"}), 400
    allowed, viewers = register_camera_viewer(viewer_id)
    if not allowed:
        return jsonify({"ok": False, "error": "Camera viewer limit reached", "viewers": viewers,
                        "max_viewers": config.MAX_CAMERA_VIEWERS}), 429
    return jsonify({"ok": True, "viewerId": viewer_id, "viewers": viewers,
                    "max_viewers": config.MAX_CAMERA_VIEWERS})


@flask_app.route("/api/camera/viewer/stop", methods=["POST"])
def camera_viewer_stop():
    data = request.get_json(silent=True) or {}
    viewer_id = str(data.get("viewerId", "")).strip()
    viewers = unregister_camera_viewer(viewer_id)
    return jsonify({"ok": True, "viewerId": viewer_id, "viewers": viewers})


@flask_app.route("/api/camera/status")
def camera_status():
    with camera_lock:
        viewers = get_active_camera_viewer_count_locked()
        running = picam2 is not None and PICAMERA_AVAILABLE
    return jsonify({"ok": True, "camera_running": running, "active_viewers": viewers,
                    "max_viewers": config.MAX_CAMERA_VIEWERS})


# -------------------------
# Raspberry Pi Camera Stream
# -------------------------

def get_camera():
    global picam2
    import config as app_config
    if app_config.shutting_down:
        return None
    if not PICAMERA_AVAILABLE:
        print("Camera not available: picamera2 module not installed")
        return None

    with camera_lock:
        if app_config.shutting_down:
            return None
        if picam2 is None:
            print("Starting Raspberry Pi camera...")
            try:
                picam2 = Picamera2()
                config = picam2.create_video_configuration(
                    main={"size": (CAMERA_WIDTH, CAMERA_HEIGHT), "format": "RGB888"},
                    buffer_count=2,
                    queue=False
                )
                picam2.configure(config)
                # Let libcamera auto-exposure/auto-white-balance settle naturally.
                # RGB888 + explicit OpenCV conversion below prevents red/blue swapping.
                try:
                    picam2.set_controls({"AwbEnable": True, "AeEnable": True})
                except Exception as exc:
                    print(f"Camera auto-control warning: {exc}")
                picam2.start()
                time.sleep(2)
                start_camera_capture_worker()
            except Exception as e:
                print(f"Failed to start camera: {e}")
                return None
        return picam2


def start_camera_capture_worker():
    """Capture and encode once, then share the newest JPEG with all viewers."""
    global camera_capture_thread
    if camera_capture_thread is not None and camera_capture_thread.is_alive():
        return
    camera_capture_thread = threading.Thread(target=capture_camera_frames, daemon=True)
    camera_capture_thread.start()


def capture_camera_frames():
    global latest_camera_frame, latest_camera_frame_sequence
    while True:
        with camera_lock:
            camera = picam2
        if camera is None:
            return

        try:
            frame = camera.capture_array()
            # Picamera2 frame is RGB; OpenCV JPEG encoding expects BGR.
            # Without this conversion, skin can appear blue/red-swapped.
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            ok, buffer = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
            )
            if ok:
                with camera_frame_condition:
                    latest_camera_frame = buffer.tobytes()
                    latest_camera_frame_sequence += 1
                    camera_frame_condition.notify_all()
            time.sleep(CAMERA_FPS_DELAY)
        except Exception as exc:
            print(f"Camera capture error: {exc}")
            time.sleep(0.2)


def generate_camera_frames(viewer_id):
    if not PICAMERA_AVAILABLE:
        print("Camera stream not available: picamera2 module not installed")
        return

    last_sequence = -1
    try:
        while True:
            try:
                camera = get_camera()
                if camera is None:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: text/plain\r\n\r\n"
                        b"Camera not available\r\n"
                    )
                    time.sleep(2)
                    continue

                with camera_frame_condition:
                    if latest_camera_frame is None or latest_camera_frame_sequence == last_sequence:
                        camera_frame_condition.wait(timeout=1.0)
                    frame = latest_camera_frame
                    last_sequence = latest_camera_frame_sequence
                if frame is None:
                    continue

                yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" +
                        frame +
                        b"\r\n"
                )

            except GeneratorExit:
                print("Camera stream client disconnected.")
                break
            except Exception as e:
                print(f"Camera stream error: {e}")
                time.sleep(1)
    finally:
        unregister_camera_viewer(viewer_id)


@flask_app.route("/camera_feed")
def camera_feed():
    if not PICAMERA_AVAILABLE:
        return "Camera not available: picamera2 module not installed", 503

    viewer_id = str(request.args.get("viewerId", "")).strip()
    if not viewer_id:
        viewer_id = "stream-" + uuid.uuid4().hex
    allowed, viewers = register_camera_viewer(viewer_id)
    if not allowed:
        return jsonify({"ok": False, "error": "Camera viewer limit reached", "viewers": viewers,
                        "max_viewers": config.MAX_CAMERA_VIEWERS}), 429
    response = Response(
        generate_camera_frames(viewer_id),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )
    # Prevent browser/reverse-proxy buffering of the MJPEG stream.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def stop_camera_if_needed():
    global picam2, latest_camera_frame, latest_camera_frame_sequence
    if not PICAMERA_AVAILABLE or picam2 is None:
        return
    print("Stopping Raspberry Pi camera: no active camera page viewers.")
    try:
        picam2.stop()
        picam2.close()
    except Exception as e:
        print(f"Camera stop error: {e}")
    with camera_frame_condition:
        picam2 = None
        latest_camera_frame = None
        latest_camera_frame_sequence += 1
        camera_frame_condition.notify_all()


def camera_watchdog():
    while True:
        try:
            if not PICAMERA_AVAILABLE:
                time.sleep(5)
                continue

            should_stop = False
            with camera_lock:
                viewers = get_active_camera_viewer_count_locked()
                if picam2 is not None and viewers <= 0:
                    should_stop = True
            if should_stop:
                with camera_lock:
                    stop_camera_if_needed()
            time.sleep(2)
        except Exception as e:
            print(f"Camera watchdog error: {e}")
            time.sleep(2)


def run_flask():
    flask_app.run(
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        debug=False,
        threaded=True,
        use_reloader=False
    )


@flask_app.route('/api/feeder', methods=['GET', 'POST'])
def feeder_api():
    if request.method == 'GET':
        return jsonify(ok=True, feeder=feeding_control.get_status())
    admin_error = require_admin_json()
    if admin_error:
        return admin_error
    payload = request.get_json(silent=True) or {}
    try:
        action = payload.get('action')
        if action == 'feed':
            result = feeding_control.request_feed(payload.get('cycles', 1))
        elif action == 'stop':
            result = feeding_control.stop_feeder()
        elif action == 'pause':
            result = feeding_control.set_paused(True)
        elif action == 'resume':
            result = feeding_control.set_paused(False)
        else:
            raise ValueError('Choose feed, stop, pause or resume')
        return jsonify(ok=True, feeder=result)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 409


def require_water_schedule_admin():
    error = require_admin_json()
    if error: return error
    if (request.headers.get('Sec-Fetch-Site') == 'cross-site' or
            request.headers.get('Origin') and urlsplit(request.headers['Origin']).netloc != request.host):
        return jsonify(ok=False, error='Use the AquaPi water schedule page.'), 403
    return None


@flask_app.route('/api/water-schedules', methods=['GET', 'POST'])
def water_schedules_api():
    import water_schedule
    if request.method == 'GET':
        return jsonify(water_schedule.response())
    error = require_water_schedule_admin()
    if error: return error
    try:
        water_schedule.save(request.get_json(silent=True))
        return jsonify(water_schedule.response())
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@flask_app.route('/api/water-schedules/<int:schedule_id>', methods=['PUT', 'DELETE'])
def water_schedule_item(schedule_id):
    import water_schedule
    error = require_water_schedule_admin()
    if error: return error
    if not any(s['id'] == schedule_id for s in water_schedule.schedules()):
        return jsonify(ok=False, error='Water schedule not found'), 404
    try:
        if request.method == 'DELETE': water_schedule.delete(schedule_id)
        else: water_schedule.save(request.get_json(silent=True), schedule_id)
        return jsonify(water_schedule.response())
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@flask_app.route('/api/water-change/stop', methods=['POST'])
def stop_scheduled_water_change():
    import water_schedule
    error = require_water_schedule_admin()
    if error: return error
    try: return jsonify(water_schedule.stop())
    except ValueError as exc: return jsonify(ok=False, error=str(exc)), 409
