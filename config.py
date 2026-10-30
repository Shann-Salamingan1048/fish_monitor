# config.py
import os
import math
import secrets
from pathlib import Path
import re
import sqlite3
import threading
import time
from datetime import timedelta

# -------------------------
# Settings
# -------------------------

PORT_1 = os.environ.get("FISHTANK_INPUT_PORT", "/dev/ttyUSB0")
PORT_2 = os.environ.get("FISHTANK_OUTPUT_PORT", "/dev/ttyUSB1")
BAUD_RATE = 115200

CAMERA_WIDTH = 960
CAMERA_HEIGHT = 540
CAMERA_FPS_DELAY = 1.0 / 12.0  # ~12 FPS, lower latency/bandwidth than 720p MJPEG
JPEG_QUALITY = 50  # balanced quality for a responsive Pi MJPEG stream
CAMERA_VIEWER_TIMEOUT = 8  # seconds without heartbeat before camera turns off
MAX_CAMERA_VIEWERS = max(1, int(os.environ.get("FISHTANK_MAX_CAMERA_VIEWERS", "3")))
WEBSITE_VIEWER_TIMEOUT = 20  # seconds without page/API activity before a slot is released
MAX_WEBSITE_VIEWERS = max(1, int(os.environ.get("FISHTANK_MAX_WEBSITE_VIEWERS", "5")))

# Admin / audience access control.
ADMIN_PIN = os.environ.get("FISHTANK_ADMIN_PIN", "admin123")
APP_SECRET_KEY = os.environ.get("FISHTANK_SECRET_KEY") or secrets.token_hex(32)

# SQLite database on the Raspberry Pi.
DB_FILE = os.environ.get("FISHTANK_DB_FILE", str(Path(__file__).with_name("fish_tank_state.db")))
FEEDING_TIMEZONE = os.environ.get("FISHTANK_FEEDING_TIMEZONE", "Asia/Manila")
FEEDING_GRACE_SECONDS = max(
    0, int(os.environ.get("FISHTANK_FEEDING_GRACE_SECONDS", "300"))
)

# Sensor and alert configuration
TEMP_LIMIT_C = 30.0
TEMP_OVERHEAT_LIMIT_C = 35.0
TURBIDITY_LIMIT_NTU = 300.0
TURBIDITY_CRITICAL_NTU = 600.0
QUALITY_CONFIRM_SECONDS = 5.0
QUALITY_MAX_PACKET_GAP_SECONDS = 3.0

PH_LOW_LIMIT = 6.5
PH_HIGH_LIMIT = 8.5
PH_CRITICAL_LOW_LIMIT = 6.0
PH_CRITICAL_HIGH_LIMIT = 9.0
PREDICTION_DELAY_SECONDS = 30.0
PREDICTION_ALERT_DURATION_SECONDS = 30.0
TREND_WINDOW_SECONDS = PREDICTION_DELAY_SECONDS
HISTORY_KEEP_SECONDS = 300.0
HISTORY_SAMPLE_INTERVAL = 3.0
MAX_TK_ALERTS = 5

NOISE_TOLERANCE = {
    "temp": 0.05,
    "turbidity": 1.0,
    "ph": 0.01
}

MIN_RISE_PER_SECOND = {
    "temp": 0.005,
    "turbidity": 0.20,
    "ph": 0.001
}

# Shared data structures (will be accessed by multiple files)
sensor_data = {
    "temp": "--",
    "float_stat": "--",
    "turbidity": "--",
    "turbidity_adc": None,
    "ph": "--"
}

sensor_history = {
    "temp": [],
    "turbidity": [],
    "ph": []
}

last_history_sample_at = {
    "temp": 0.0,
    "turbidity": 0.0,
    "ph": 0.0
}

active_prediction_alerts = {}
active_camera_viewers = {}
active_website_viewers = {}
serial_ports = []
output_serial_ports = []
input_serial_ports = []
shutting_down = False
settings_version = 0
last_sensor_received_at = 0.0
MAX_FEED_CYCLES = (1 << 32) - 1  # Physical uint32_t counter capacity; no serving-size cap.
# Listen on the local network by default so phones can connect on the same Wi-Fi.
WEB_HOST = os.environ.get("FISHTANK_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("FISHTANK_PORT", "5000"))

# Locks for thread safety.  They are created when this module is imported so
# every module that imports one keeps a valid, shared lock reference.  Do not
# replace these lock objects at runtime; replacing them leaves already-imported
# references pointing at the old lock (or at ``None``).
sensor_lock = threading.RLock()
prediction_lock = threading.Lock()
camera_lock = threading.RLock()
website_viewer_lock = threading.RLock()
serial_write_lock = threading.RLock()
feeding_schedule_lock = threading.RLock()

WEEKDAY_NAMES = (
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday"
)
FEED_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


# -------------------------
# SQLite State Storage
# -------------------------

# 0–6 are weekly; 7 is one recurring daily schedule.
EVERY_DAY = 7
FEEDING_DAY_NAMES = (*WEEKDAY_NAMES, "Every day (Monday–Sunday)")


def db_connect():
    return sqlite3.connect(DB_FILE, timeout=10)


def init_database():
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feeding_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 7),
                feed_time TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                last_triggered_date TEXT,
                last_triggered_at REAL,
                last_status TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(day_of_week, feed_time)
            )
            """
        )
        # Extend the old CHECK constraint without losing schedule IDs/history.
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='feeding_schedules'").fetchone()[0]
        if re.search(r"BETWEEN\s+0\s+AND\s+6", schema, re.I):
            sequence = None
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
                sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='feeding_schedules'").fetchone()
            replacement = re.sub(r"BETWEEN\s+0\s+AND\s+6", "BETWEEN 0 AND 7", schema, flags=re.I)
            replacement = replacement.replace("feeding_schedules", "feeding_schedules_new", 1)
            conn.execute(replacement)
            conn.execute("INSERT INTO feeding_schedules_new SELECT * FROM feeding_schedules")
            conn.execute("DROP TABLE feeding_schedules")
            conn.execute("ALTER TABLE feeding_schedules_new RENAME TO feeding_schedules")
            if sequence:
                conn.execute("UPDATE sqlite_sequence SET seq=MAX(seq, ?) WHERE name='feeding_schedules'", (sequence[0],))
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_feeding_schedules_due
            ON feeding_schedules(enabled, day_of_week, feed_time)
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(feeding_schedules)")}
        if "cycles" not in columns:
            conn.execute("ALTER TABLE feeding_schedules ADD COLUMN cycles INTEGER NOT NULL DEFAULT 3")
        conn.execute("""CREATE TABLE IF NOT EXISTS feeding_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, cycles INTEGER NOT NULL,
            source TEXT NOT NULL, schedule_id INTEGER, trigger_date TEXT,
            status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
        conn.commit()


def validate_feed_cycles(value):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError("Cycles must be a positive whole number")
    cycles = int(value)
    if cycles < 1:
        raise ValueError("Cycles must be a positive whole number")
    if cycles > MAX_FEED_CYCLES:
        raise ValueError("Cycle count exceeds the Nano counter capacity (4,294,967,295)")
    return cycles


def validate_feeding_day(day_of_week):
    if isinstance(day_of_week, bool) or not re.fullmatch(r"[0-7]", str(day_of_week)):
        raise ValueError("Select Monday–Sunday or Every day")
    try:
        day = int(day_of_week)
    except (TypeError, ValueError):
        raise ValueError("Select Monday–Sunday or Every day")
    if day < 0 or day > 7:
        raise ValueError("Select Monday–Sunday or Every day")
    return day


def validate_feed_time(feed_time):
    value = str(feed_time or "").strip()
    if not FEED_TIME_PATTERN.fullmatch(value):
        raise ValueError("Time must use the 24-hour HH:MM format")
    return value


def normalize_schedule_enabled(enabled):
    if isinstance(enabled, bool):
        return enabled
    if enabled in (1, "1", "true", "True", "yes", "on"):
        return True
    if enabled in (0, "0", "false", "False", "no", "off"):
        return False
    raise ValueError("Enabled must be true or false")


def _schedule_row_to_dict(row):
    return {
        "id": int(row["id"]),
        "day": int(row["day_of_week"]),
        "dayName": FEEDING_DAY_NAMES[int(row["day_of_week"])],
        "time": row["feed_time"],
        "enabled": bool(row["enabled"]),
        "cycles": int(row["cycles"]),
        "lastTriggeredDate": row["last_triggered_date"],
        "lastTriggeredAt": row["last_triggered_at"],
        "lastStatus": row["last_status"],
    }


def get_feeding_schedules():
    with feeding_schedule_lock, db_connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, day_of_week, feed_time, enabled, cycles,
                   last_triggered_date, last_triggered_at, last_status
            FROM feeding_schedules
            ORDER BY day_of_week, feed_time, id
            """
        ).fetchall()
    return [_schedule_row_to_dict(row) for row in rows]


def create_feeding_schedule(day_of_week, feed_time, enabled=True, cycles=3):
    cycles = validate_feed_cycles(cycles)
    day = validate_feeding_day(day_of_week)
    scheduled_time = validate_feed_time(feed_time)
    is_enabled = normalize_schedule_enabled(enabled)
    now = time.time()

    try:
        with feeding_schedule_lock, db_connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO feeding_schedules
                    (day_of_week, feed_time, enabled, cycles, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (day, scheduled_time, int(is_enabled), cycles, now, now)
            )
            schedule_id = cursor.lastrowid
            conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("A feeding schedule already exists for that day and time") from exc

    return get_feeding_schedule(schedule_id)


def get_feeding_schedule(schedule_id):
    try:
        normalized_id = int(schedule_id)
    except (TypeError, ValueError):
        return None

    with feeding_schedule_lock, db_connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, day_of_week, feed_time, enabled, cycles,
                   last_triggered_date, last_triggered_at, last_status
            FROM feeding_schedules WHERE id = ?
            """,
            (normalized_id,)
        ).fetchone()
    return _schedule_row_to_dict(row) if row else None


def update_feeding_schedule(schedule_id, day_of_week, feed_time, enabled, cycles=None):
    if cycles is not None:
        cycles = validate_feed_cycles(cycles)
    try:
        normalized_id = int(schedule_id)
    except (TypeError, ValueError):
        raise ValueError("Invalid feeding schedule ID")

    day = validate_feeding_day(day_of_week)
    scheduled_time = validate_feed_time(feed_time)
    is_enabled = normalize_schedule_enabled(enabled)

    try:
        with feeding_schedule_lock, db_connect() as conn:
            cursor = conn.execute(
                """
                UPDATE feeding_schedules
                SET last_triggered_date = CASE
                        WHEN day_of_week <> ? OR feed_time <> ? THEN NULL
                        ELSE last_triggered_date
                    END,
                    day_of_week = ?, feed_time = ?, enabled = ?, cycles = COALESCE(?, cycles), updated_at = ?
                WHERE id = ?
                """,
                (
                    day, scheduled_time,
                    day, scheduled_time, int(is_enabled), cycles, time.time(), normalized_id
                )
            )
            conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("A feeding schedule already exists for that day and time") from exc

    if cursor.rowcount == 0:
        return None
    if not is_enabled:
        with db_connect() as conn:
            conn.execute("UPDATE feeding_schedules SET last_status='cancelled' WHERE id=? AND last_status='pending'", (normalized_id,))
    return get_feeding_schedule(normalized_id)


def delete_feeding_schedule(schedule_id):
    try:
        normalized_id = int(schedule_id)
    except (TypeError, ValueError):
        return False

    with feeding_schedule_lock, db_connect() as conn:
        cursor = conn.execute(
            "DELETE FROM feeding_schedules WHERE id = ?",
            (normalized_id,)
        )
        conn.commit()
    return cursor.rowcount > 0


def claim_due_feeding_schedules(local_now):
    """Atomically claim enabled schedules due within the catch-up window."""
    triggered_at = local_now.timestamp()

    with feeding_schedule_lock, db_connect() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        enabled_rows = conn.execute(
            """
            SELECT id, day_of_week, feed_time, enabled, cycles,
                   last_triggered_date, last_triggered_at, last_status
            FROM feeding_schedules
            WHERE enabled = 1
            ORDER BY id
            """
        ).fetchall()

        claimed = []
        for row in enabled_rows:
            hour, minute = (int(part) for part in row["feed_time"].split(":"))
            daily = int(row["day_of_week"]) == EVERY_DAY
            days_back = 0 if daily else (local_now.weekday() - int(row["day_of_week"])) % 7
            occurrence = (local_now - timedelta(days=days_back)).replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            if daily and occurrence > local_now:
                occurrence -= timedelta(days=1)
            seconds_late = (local_now - occurrence).total_seconds()
            trigger_date = occurrence.date().isoformat()

            if not (0 <= seconds_late <= FEEDING_GRACE_SECONDS):
                continue
            if row["last_triggered_date"] == trigger_date:
                continue

            conn.execute(
                """
                UPDATE feeding_schedules
                SET last_triggered_date = ?, last_triggered_at = ?,
                    last_status = 'pending', updated_at = ?
                WHERE id = ?
                """,
                (trigger_date, triggered_at, time.time(), row["id"])
            )
            schedule = _schedule_row_to_dict(row)
            schedule["triggerDate"] = trigger_date
            schedule["scheduledDateTime"] = occurrence.isoformat()
            claimed.append(schedule)
        conn.commit()

    return claimed


def get_pending_feeding_schedules():
    """Return scheduled feeds already claimed but waiting to be sent.

    A feed remains pending while automatic/manual water pumping is active so the
    feeding scheduler can deliver it after the water operation finishes, even
    when that takes longer than the normal grace window.
    """
    with feeding_schedule_lock, db_connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, day_of_week, feed_time, enabled, cycles,
                   last_triggered_date, last_triggered_at, last_status
            FROM feeding_schedules
            WHERE enabled = 1 AND last_status = 'pending' AND last_triggered_date IS NOT NULL
            ORDER BY last_triggered_at, id
            """
        ).fetchall()
    return [_schedule_row_to_dict(row) for row in rows]


def complete_feeding_schedule_attempt(schedule_id, trigger_date, succeeded):
    """Record success, or release a failed claim for retry during the grace window."""
    status = "success" if succeeded else "failed"
    with feeding_schedule_lock, db_connect() as conn:
        if succeeded:
            conn.execute(
                """
                UPDATE feeding_schedules
                SET last_status = ?, updated_at = ?
                WHERE id = ? AND last_triggered_date = ?
                """,
                (status, time.time(), int(schedule_id), trigger_date)
            )
        else:
            conn.execute(
                """
                UPDATE feeding_schedules
                SET last_triggered_date = NULL, last_status = ?, updated_at = ?
                WHERE id = ? AND last_triggered_date = ?
                """,
                (status, time.time(), int(schedule_id), trigger_date)
            )
        conn.commit()


def get_db_value(key, default=None):
    try:
        with db_connect() as conn:
            row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default
    except Exception as exc:
        print(f"Database read error for {key}: {exc}")
        return default


def set_db_value(key, value):
    try:
        with db_connect() as conn:
            conn.execute(
                """
                INSERT INTO app_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, str(value), time.time())
            )
            conn.commit()
    except Exception as exc:
        print(f"Database write error for {key}: {exc}")


def load_float_setting(key, default):
    raw_value = get_db_value(key)
    if raw_value is None:
        set_db_value(key, default)
        return default
    try:
        return float(raw_value)
    except ValueError:
        return default


def load_int_setting(key, default):
    raw_value = get_db_value(key)
    if raw_value is None:
        set_db_value(key, default)
        return default
    try:
        return int(float(raw_value))
    except ValueError:
        return default


# -------------------------
# Settings Management
# -------------------------

def normalize_prediction_timing_settings():
    """Keep prediction timing values safe and synchronized."""
    global PREDICTION_DELAY_SECONDS, PREDICTION_ALERT_DURATION_SECONDS
    global TREND_WINDOW_SECONDS, HISTORY_KEEP_SECONDS

    try:
        PREDICTION_DELAY_SECONDS = max(1.0, float(PREDICTION_DELAY_SECONDS))
    except (TypeError, ValueError):
        PREDICTION_DELAY_SECONDS = 30.0

    try:
        PREDICTION_ALERT_DURATION_SECONDS = max(1.0, float(PREDICTION_ALERT_DURATION_SECONDS))
    except (TypeError, ValueError):
        PREDICTION_ALERT_DURATION_SECONDS = 30.0

    TREND_WINDOW_SECONDS = PREDICTION_DELAY_SECONDS
    HISTORY_KEEP_SECONDS = max(
        300.0,
        TREND_WINDOW_SECONDS + PREDICTION_ALERT_DURATION_SECONDS + 60.0
    )


def clear_prediction_alert_cache():
    global active_prediction_alerts
    with prediction_lock:
        active_prediction_alerts.clear()


def load_persisted_settings():
    global TEMP_LIMIT_C, TEMP_OVERHEAT_LIMIT_C
    global TURBIDITY_LIMIT_NTU, TURBIDITY_CRITICAL_NTU
    global PH_LOW_LIMIT, PH_HIGH_LIMIT, PH_CRITICAL_LOW_LIMIT, PH_CRITICAL_HIGH_LIMIT
    global PREDICTION_DELAY_SECONDS, PREDICTION_ALERT_DURATION_SECONDS, MAX_CAMERA_VIEWERS, MAX_WEBSITE_VIEWERS

    TEMP_LIMIT_C = load_float_setting("limit_temp", TEMP_LIMIT_C)
    TEMP_OVERHEAT_LIMIT_C = load_float_setting("limit_temp_critical", TEMP_OVERHEAT_LIMIT_C)
    TURBIDITY_LIMIT_NTU = load_float_setting("limit_turbidity", TURBIDITY_LIMIT_NTU)
    TURBIDITY_CRITICAL_NTU = load_float_setting("limit_turbidity_critical", TURBIDITY_CRITICAL_NTU)
    PH_LOW_LIMIT = load_float_setting("limit_ph_low", PH_LOW_LIMIT)
    PH_HIGH_LIMIT = load_float_setting("limit_ph_high", PH_HIGH_LIMIT)
    PH_CRITICAL_LOW_LIMIT = load_float_setting("limit_ph_critical_low", PH_CRITICAL_LOW_LIMIT)
    PH_CRITICAL_HIGH_LIMIT = load_float_setting("limit_ph_critical_high", PH_CRITICAL_HIGH_LIMIT)
    MAX_CAMERA_VIEWERS = max(1, load_int_setting("max_camera_viewers", MAX_CAMERA_VIEWERS))
    MAX_WEBSITE_VIEWERS = max(1, load_int_setting("max_website_viewers", MAX_WEBSITE_VIEWERS))
    PREDICTION_DELAY_SECONDS = load_float_setting("prediction_delay_seconds", PREDICTION_DELAY_SECONDS)
    PREDICTION_ALERT_DURATION_SECONDS = load_float_setting(
        "prediction_alert_duration_seconds",
        PREDICTION_ALERT_DURATION_SECONDS
    )
    normalize_prediction_timing_settings()

    set_db_value("servo_tilt_angle", load_int_setting("servo_tilt_angle", 90))
    set_db_value("servo_pan_angle", load_int_setting("servo_pan_angle", 110))
    set_db_value("prediction_delay_seconds", PREDICTION_DELAY_SECONDS)
    set_db_value("prediction_alert_duration_seconds", PREDICTION_ALERT_DURATION_SECONDS)
    set_db_value("max_camera_viewers", MAX_CAMERA_VIEWERS)
    set_db_value("max_website_viewers", MAX_WEBSITE_VIEWERS)


def update_limits_from_payload(payload):
    global TEMP_LIMIT_C, TEMP_OVERHEAT_LIMIT_C
    global TURBIDITY_LIMIT_NTU, TURBIDITY_CRITICAL_NTU
    global PH_LOW_LIMIT, PH_HIGH_LIMIT, PH_CRITICAL_LOW_LIMIT, PH_CRITICAL_HIGH_LIMIT
    global PREDICTION_DELAY_SECONDS, PREDICTION_ALERT_DURATION_SECONDS, MAX_CAMERA_VIEWERS, MAX_WEBSITE_VIEWERS, settings_version

    updates = {
        "limit_temp": ("tempLimit", float, 0.0),
        "limit_temp_critical": ("tempCriticalLimit", float, 0.0),
        "limit_ph_low": ("phLowLimit", float, 0.0),
        "limit_ph_high": ("phHighLimit", float, 0.0),
        "limit_ph_critical_low": ("phCriticalLowLimit", float, 0.0),
        "limit_ph_critical_high": ("phCriticalHighLimit", float, 0.0),
        "max_camera_viewers": ("maxCameraViewers", int, 1),
        "max_website_viewers": ("maxWebsiteViewers", int, 1),
        "prediction_delay_seconds": ("predictionDelaySeconds", float, 1.0),
        "prediction_alert_duration_seconds": ("predictionAlertDurationSeconds", float, 1.0),
    }

    changed = {}
    pending_values = {}
    for db_key, (payload_key, caster, min_value) in updates.items():
        if payload_key not in payload or payload[payload_key] in (None, ""):
            continue
        try:
            raw_number = float(payload[payload_key])
            if not math.isfinite(raw_number) or isinstance(payload[payload_key], bool):
                raise ValueError("Use a finite number")
            if caster is int and not raw_number.is_integer():
                raise ValueError("Use a whole number")
            value = int(raw_number) if caster is int else float(raw_number)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid value for {payload_key}")

        if value < min_value:
            raise ValueError(f"{payload_key} must be at least {min_value:g}")

        pending_values[db_key] = value
        changed[payload_key] = value

    def proposed(key, fallback):
        return pending_values.get(key, fallback)
    if proposed("limit_temp", TEMP_LIMIT_C) >= proposed("limit_temp_critical", TEMP_OVERHEAT_LIMIT_C):
        raise ValueError("Temperature warning must be below its critical limit")
    ph = [proposed(k, v) for k, v in zip(
        ("limit_ph_critical_low", "limit_ph_low", "limit_ph_high", "limit_ph_critical_high"),
        (PH_CRITICAL_LOW_LIMIT, PH_LOW_LIMIT, PH_HIGH_LIMIT, PH_CRITICAL_HIGH_LIMIT))]
    if not (0 <= ph[0] < ph[1] < ph[2] < ph[3] <= 14):
        raise ValueError("Use pH values from 0–14. The critical lower limit must be below the warning range, and the critical upper limit must be above it.")
    with db_connect() as conn:
        conn.executemany("INSERT INTO app_state(key,value,updated_at) VALUES(?,?,?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                         [(k, str(v), time.time()) for k, v in pending_values.items()])
    load_persisted_settings()
    clear_prediction_alert_cache()
    if changed:
        settings_version += 1
    return changed


def get_servo_position():
    return {
        "tilt": load_int_setting("servo_tilt_angle", 90),
        "pan": load_int_setting("servo_pan_angle", 110)
    }


def update_servo_position_for_command(command):
    position = get_servo_position()
    step = 10

    if command == "up":
        position["tilt"] = max(30, min(130, position["tilt"] - step))
    elif command == "down":
        position["tilt"] = max(30, min(130, position["tilt"] + step))
    elif command == "left":
        position["pan"] = max(40, min(180, position["pan"] + step))
    elif command == "right":
        position["pan"] = max(40, min(180, position["pan"] - step))
    elif command == "center":
        position["tilt"] = 90
        position["pan"] = 110

    set_db_value("servo_tilt_angle", position["tilt"])
    set_db_value("servo_pan_angle", position["pan"])
    return position


def get_public_limits():
    return {
        "temperature": {
            "limit": TEMP_LIMIT_C,
            "overheatLimit": TEMP_OVERHEAT_LIMIT_C,
            "unit": "\u00b0C"
        },
        "turbidity": {
            "limit": TURBIDITY_LIMIT_NTU,
            "criticalLimit": TURBIDITY_CRITICAL_NTU,
            "unit": "NTU"
        },
        "ph": {
            "lowLimit": PH_LOW_LIMIT,
            "highLimit": PH_HIGH_LIMIT,
            "criticalLowLimit": PH_CRITICAL_LOW_LIMIT,
            "criticalHighLimit": PH_CRITICAL_HIGH_LIMIT,
            "unit": "pH"
        },
        "maxCameraViewers": MAX_CAMERA_VIEWERS,
        "maxWebsiteViewers": MAX_WEBSITE_VIEWERS,
        "predictionDelaySeconds": PREDICTION_DELAY_SECONDS,
        "predictionAlertDurationSeconds": PREDICTION_ALERT_DURATION_SECONDS,
        "trendWindowSeconds": TREND_WINDOW_SECONDS
    }


# -------------------------
# Serial Communication Helpers
# -------------------------

def build_camera_serial_message(command):
    camera_map = {
        "up": "UP",
        "down": "DOWN",
        "left": "LEFT",
        "right": "RIGHT",
        "center": "CENTER",
        "feed": "NO"
    }
    # Manual Feed Now = exactly one feeder cycle per click.
    feed_value = "ONE" if command == "feed" else "NO"
    return f"NANOOUTPUT,CAMERA:{camera_map[command]},FEED:{feed_value}\n"


def build_scheduled_feed_serial_message():
    """Scheduled feeding keeps the original three-cycle serving."""
    return "NANOOUTPUT,CAMERA:NO,FEED:SCHEDULED\n"


def build_pump_serial_message(command):
    command = str(command or "").strip().upper()
    valid = {
        "EXTRACT_ON", "EXTRACT_OFF",
        "INSERT_ON", "INSERT_OFF",
        "STOP_ALL", "BOTH_ON",
    }
    if command not in valid:
        raise ValueError("Invalid pump command")
    return f"NANOOUTPUT,PUMP:{command}\n"


def build_input_sensor_mode_message(mode):
    mode = str(mode or "").strip().upper()
    if mode not in {"NORMAL", "AUTO_QUALITY", "AUTO_EXTRACT", "FLOAT_ONLY"}:
        raise ValueError("Invalid input sensor mode")
    return f"NANOINPUT,MODE:{mode}\n"


def build_cooler_temperature_message(temperature, threshold=None):
    """Build the temperature update consumed by NANO_OUTPUT.ino."""
    if threshold is None:
        threshold = TEMP_OVERHEAT_LIMIT_C
    return (
        f"NANOOUTPUT,CAMERA:NO,FEED:NO,TEMP:{float(temperature):.2f},"
        f"COOLERTHRESHOLD:{float(threshold):.2f}\n"
    )


def send_to_all_serial_ports(message):
    """Send a message to every connected serial device."""
    return _send_to_serial_targets(list(serial_ports), message)


def register_output_serial_port(ser):
    """Remember a port after it identifies itself as NANO_OUTPUT."""
    with serial_write_lock:
        if ser not in output_serial_ports:
            output_serial_ports.append(ser)
        if ser in input_serial_ports:
            input_serial_ports.remove(ser)


def unregister_output_serial_port(ser):
    with serial_write_lock:
        if ser in output_serial_ports:
            output_serial_ports.remove(ser)


def register_input_serial_port(ser):
    """Remember a port after it sends NANOINPUT sensor/status data."""
    with serial_write_lock:
        if ser not in input_serial_ports:
            input_serial_ports.append(ser)
        if ser in output_serial_ports:
            output_serial_ports.remove(ser)


def unregister_input_serial_port(ser):
    with serial_write_lock:
        if ser in input_serial_ports:
            input_serial_ports.remove(ser)


def send_to_output_serial_ports(message):
    """Send commands to the identified output Nano, with startup fallback."""
    with serial_write_lock:
        targets = list(output_serial_ports) or list(serial_ports)
    return _send_to_serial_targets(targets, message)


def send_to_identified_output_serial_ports(message):
    with serial_write_lock:
        targets = list(output_serial_ports)
    return _send_to_serial_targets(targets, message)


def send_to_input_serial_ports(message):
    """Send sensor-mode commands only to the identified input Nano."""
    with serial_write_lock:
        targets = list(input_serial_ports)
    return _send_to_serial_targets(targets, message)


def begin_shutdown():
    """Atomically inhibit new commands and send final stops to both boards."""
    global shutting_down
    with serial_write_lock:
        shutting_down = True
        # Legacy stops also support a graceful shutdown during a firmware upgrade.
        message = ("NANOOUTPUT,SYSTEM:OFF\nNANOOUTPUT,PUMP:STOP_ALL\n"
                   "NANOOUTPUT,FEED:STOP\nNANOOUTPUT,FANMODE:OFF\n"
                   "NANOINPUT,SYSTEM:OFF\n").encode("ascii")
        targets = list(dict.fromkeys(serial_ports + output_serial_ports + input_serial_ports))
        for ser in targets:
            try:
                if ser and ser.is_open:
                    ser.write(message)
            except Exception as exc:
                print(f"[SHUTDOWN] Stop write failed: {exc}; Nano watchdog will expire.")


def _send_to_serial_targets(targets, message):
    sent = False
    encoded_message = message.encode("utf-8")
    with serial_write_lock:
        if shutting_down:
            return False
        for ser in targets:
            try:
                if ser and ser.is_open:
                    written = ser.write(encoded_message)
                    sent = sent or written == len(encoded_message)
            except Exception as exc:
                print(f"Serial write error: {exc}")
    return sent



# -------------------------
# Session/Admin Helpers
# -------------------------

def is_admin():
    from admin_sessions import is_admin as session_is_admin
    return session_is_admin()


def require_admin_json():
    from flask import jsonify
    if not is_admin():
        return jsonify({"ok": False, "error": "Admin mode required"}), 403
    return None
