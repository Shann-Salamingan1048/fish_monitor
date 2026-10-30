"""One feeder command path shared by Tk, Flask and the scheduler.

A serial write is NOT proof of delivery. Jobs are recorded before sending,
completed by Nano feedback, and never replayed after an uncertain result.
"""
import threading
import time
import sqlite3
import config
import water_control

_lock = threading.RLock()
_ready = False
_active = None
_last = None


def initialize():
    global _active, _ready, _last
    with _lock, config.db_connect() as conn:
        conn.execute("UPDATE feeding_jobs SET status='unknown', updated_at=? WHERE status IN ('sent','running','stopping')", (time.time(),))
        conn.execute("UPDATE feeding_schedules SET last_status='unknown' WHERE last_status IN ('sent','running')")
        _active = None
        _ready = False
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT * FROM feeding_jobs ORDER BY id DESC LIMIT 1').fetchone()
        _last = dict(row) if row else None


def _record_status(status):
    global _last
    if _active is None:
        return
    _active['status'] = status
    _active['updated_at'] = time.time()
    with config.db_connect() as conn:
        conn.execute('UPDATE feeding_jobs SET status=?, updated_at=? WHERE id=?',
                     (status, time.time(), _active['id']))
        if _active.get('schedule_id'):
            conn.execute('UPDATE feeding_schedules SET last_status=? WHERE id=? AND last_triggered_date=?',
                         (status, _active['schedule_id'], _active['trigger_date']))
    _last = dict(_active)


def disconnect():
    global _ready
    with _lock:
        _ready = False
        _record_status('unknown')


def handle_status(line):
    global _ready, _active
    fields = dict(part.split(':', 1) for part in line.split(',') if ':' in part)
    with _lock:
        if fields.get('FEEDER') == 'V3':
            _record_status('unknown')
            _active = None  # Nano reset boots with feeder closed; no command replay.
            _ready = True
        event = fields.get('FEED')
        if not event:
            return
        if event == 'STOPPED' and fields.get('JOB') == '0':
            _record_status('cancelled')
            _active = None
            return
        if not _active or fields.get('JOB') != str(_active['id']):
            return
        status = {'STARTED': 'running', 'COMPLETE': 'complete',
                  'STOPPED': 'cancelled', 'REJECTED': 'rejected'}.get(event)
        if status:
            _record_status(status)
            if status in ('complete', 'cancelled', 'rejected'):
                _active = None


def get_status():
    with _lock:
        if _active and _active['status'] in ('sent', 'running', 'stopping'):
            if time.time() - _active['created_at'] > _active['cycles'] * 2 + 15:
                _record_status('unknown')
        return {'ready': _ready and len(config.output_serial_ports) == 1,
                'busy': _active is not None,
                'active': dict(_active) if _active else None,
                'last': dict(_last) if _last else None,
                'paused': config.get_db_value('feeding_paused', '0') == '1',
                'maxCycles': config.MAX_FEED_CYCLES}


def set_paused(paused):
    paused = config.normalize_schedule_enabled(paused)
    with _lock, config.db_connect() as conn:
        conn.execute("INSERT INTO app_state(key,value,updated_at) VALUES('feeding_paused',?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                     ('1' if paused else '0', time.time()))
        if paused:
            conn.execute("UPDATE feeding_schedules SET last_status='cancelled' WHERE last_status='pending'")
    return get_status()


def request_feed(cycles=1, source='manual', schedule=None):
    if config.shutting_down:
        raise ValueError('System is off. Restart the application to resume.')
    global _active, _last
    cycles = config.validate_feed_cycles(cycles)
    with _lock:
        status = get_status()
        if not status['ready']:
            raise ValueError('Feeder unavailable. Connect one output Nano with the updated firmware.')
        if status['busy']:
            raise ValueError('Feeder is busy or awaiting confirmation. Stop the feeder before another request.')
        if water_control.is_pump_active():
            raise ValueError('Wait until water pumping has stopped before feeding.')
        if source == 'schedule' and status['paused']:
            raise ValueError('Automatic feeding is paused.')
        now = time.time()
        with config.db_connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if schedule:
                row = conn.execute("SELECT enabled,cycles,last_status,last_triggered_date FROM feeding_schedules WHERE id=?", (schedule['id'],)).fetchone()
                if not row or not row[0] or row[2] != 'pending' or row[3] != schedule['lastTriggeredDate']:
                    raise ValueError('Schedule changed or paused before dispatch')
                cycles = config.validate_feed_cycles(row[1])
            cursor = conn.execute('INSERT INTO feeding_jobs(cycles,source,schedule_id,trigger_date,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                (cycles, source, schedule['id'] if schedule else None,
                 schedule['lastTriggeredDate'] if schedule else None, 'sent', now, now))
            _active = {'id': cursor.lastrowid, 'cycles': cycles, 'source': source,
                       'schedule_id': schedule['id'] if schedule else None,
                       'trigger_date': schedule['lastTriggeredDate'] if schedule else None,
                       'status': 'sent', 'created_at': now, 'updated_at': now}
            if schedule:
                conn.execute("UPDATE feeding_schedules SET last_status='sent' WHERE id=? AND last_triggered_date=?",
                             (schedule['id'], schedule['lastTriggeredDate']))
        _last = dict(_active)
        message = f"NANOOUTPUT,FEED:CUSTOM,CYCLES:{cycles},JOB:{_active['id']}\n"
        if not config.send_to_identified_output_serial_ports(message):
            _record_status('unknown')
            raise ValueError('Delivery uncertain. Inspect the feeder and press Stop; this request will not be repeated.')
        return get_status()


def stop_feeder():
    with _lock:
        if not config.output_serial_ports:
            raise ValueError('Output Nano disconnected; feeder stop cannot be confirmed.')
        _record_status('stopping')
        if not config.send_to_identified_output_serial_ports('NANOOUTPUT,FEED:STOP,JOB:0\n'):
            _record_status('unknown')
            raise ValueError('Could not send stop. Check the output Nano connection.')
        return get_status()
