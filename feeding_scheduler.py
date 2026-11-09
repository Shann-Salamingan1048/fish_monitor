"""Standalone daily/weekly feeding scheduler with pump-conflict postponement."""

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import config
import water_control
import feeding_control
from config import (
    build_scheduled_feed_serial_message,
    send_to_identified_output_serial_ports,
)


_feeding_zone = None


def get_feeding_zone():
    global _feeding_zone
    if _feeding_zone is not None:
        return _feeding_zone

    try:
        _feeding_zone = ZoneInfo(config.FEEDING_TIMEZONE)
    except ZoneInfoNotFoundError:
        print(f"Unknown feeding timezone {config.FEEDING_TIMEZONE!r}; using UTC")
        _feeding_zone = ZoneInfo("UTC")
    return _feeding_zone


def get_next_feeding_run(schedules, local_now=None):
    zone = get_feeding_zone()
    now = local_now or datetime.now(zone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=zone)
    else:
        now = now.astimezone(zone)

    next_run = None
    next_schedule_id = None

    for schedule in schedules:
        if not schedule.get("enabled"):
            continue

        hour, minute = (int(part) for part in schedule["time"].split(":"))
        daily = int(schedule["day"]) == config.EVERY_DAY
        days_ahead = 0 if daily else (int(schedule["day"]) - now.weekday()) % 7
        candidate = (now + timedelta(days=days_ahead)).replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )

        if candidate <= now:
            seconds_late = (now - candidate).total_seconds()
            already_triggered = (
                schedule.get("lastTriggeredDate") == candidate.date().isoformat()
            )
            if seconds_late > config.FEEDING_GRACE_SECONDS or already_triggered:
                candidate += timedelta(days=1 if daily else 7)

        if next_run is None or candidate < next_run:
            next_run = candidate
            next_schedule_id = schedule["id"]

    if next_run is None:
        return None

    return {
        "scheduleId": next_schedule_id,
        "dateTime": next_run.isoformat(),
    }


def feeding_schedule_response():
    schedules = config.get_feeding_schedules()
    zone = get_feeding_zone()
    pending = config.get_pending_feeding_schedules()

    return {
        "ok": True,
        "timezone": getattr(zone, "key", str(zone)),
        "schedules": schedules,
        "nextRun": get_next_feeding_run(schedules),
        "postponed": len(pending),
        "postponedSchedules": [item["id"] for item in pending],
        "pumpActive": water_control.is_pump_active(),
        "feeder": feeding_control.get_status(),
    }


def process_due_feeding_schedules(local_now=None):
    """Claim due feeds and postpone them for as long as water pumping is active."""
    zone = get_feeding_zone()
    now = local_now or datetime.now(zone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=zone)
    else:
        now = now.astimezone(zone)

    # A schedule is claimed while it is inside its normal grace window. Once
    # claimed, it stays pending in SQLite until the water operation finishes.
    config.claim_due_feeding_schedules(now)
    pending = config.get_pending_feeding_schedules()

    if not pending:
        return []
    status = feeding_control.get_status()
    # Paused occurrences are intentionally skipped, never queued for a later burst.
    if status['paused']:
        with config.db_connect() as conn:
            conn.execute("UPDATE feeding_schedules SET last_status='cancelled' WHERE last_status='pending'")
        return []
    if water_control.is_pump_active() or status['busy'] or not status['ready']:
        return []
    results = []
    # One active serving at a time. Firmware reports completion before the next.
    for schedule in pending[:1]:
        try:
            feeding_control.request_feed(schedule['cycles'], 'schedule', schedule)
            results.append({'scheduleId': schedule['id'], 'sent': True})
        except ValueError as exc:
            print(f"Scheduled feed deferred: {exc}")

    return results


def feeding_schedule_worker(stop_event=None, poll_seconds=1.0):
    while stop_event is None or not stop_event.is_set():
        try:
            if not config.shutting_down:
                process_due_feeding_schedules()
        except Exception as exc:
            print(f"Feeding scheduler error: {exc}")

        if stop_event is not None:
            if stop_event.wait(poll_seconds):
                break
        else:
            time.sleep(poll_seconds)
