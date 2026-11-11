"""Recurring water changes. The Pi times both pumps together using existing Mega commands.

Claims/jobs are durable before sending and never replayed after interruption.
All actuator admission uses the shared sensor lock, including feeder requests.
"""
import re
import math
import sqlite3
import time
from datetime import datetime, timedelta
import config
import calibration
import sensor_control
import water_control
import feeding_control
from feeding_scheduler import get_feeding_zone

_lock = config.sensor_lock
_active = None
_ready = False
_started_at = time.time()
NORMAL_RECOVERY_SECONDS = 2.0
PUMP_ACK_SECONDS = 3.0
_PUMPS = {
    'both': ('BOTH_ON', ('ON', 'ON'), 'Both pumps running'),
    'drain': ('EXTRACT_ON', ('ON', 'OFF'), 'Correcting high water: drain only'),
    'refill': ('INSERT_ON', ('OFF', 'ON'), 'Correcting low water: refill only'),
}


def create_tables(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS water_schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT, day INTEGER NOT NULL,
        time TEXT NOT NULL, drain_seconds INTEGER NOT NULL, refill_seconds INTEGER NOT NULL,
        enabled INTEGER NOT NULL, last_date TEXT, last_at REAL, last_status TEXT,
        UNIQUE(day,time))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS water_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER, trigger_date TEXT,
        status TEXT NOT NULL, reason TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)''')


def initialize():
    global _active, _ready, _started_at
    with _lock, config.db_connect() as conn:
        conn.execute("UPDATE water_jobs SET status='interrupted',reason='Application restarted',updated_at=? WHERE status IN ('sent','draining','refilling','exchanging','stopping')", (time.time(),))
        conn.execute("UPDATE water_schedules SET last_status='interrupted' WHERE last_status IN ('claimed','sent','draining','refilling','exchanging','stopping')")
        _active, _ready, _started_at = None, False, time.time()


def _seconds(value, name, maximum):
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value)) or not 1 <= int(value) <= maximum:
        raise ValueError(f'{name} must be a whole number from 1 to {maximum} seconds.')
    return int(value)


def _row(row):
    return dict(id=row['id'], day=row['day'], dayName=config.FEEDING_DAY_NAMES[row['day']],
                time=row['time'], durationSeconds=min(row['drain_seconds'], row['refill_seconds']),
                drainSeconds=row['drain_seconds'], refillSeconds=row['refill_seconds'],
                enabled=bool(row['enabled']), lastTriggeredDate=row['last_date'],
                lastTriggeredAt=row['last_at'], lastStatus=row['last_status'])


def schedules():
    with _lock, config.db_connect() as conn:
        conn.row_factory=sqlite3.Row
        return [_row(r) for r in conn.execute('SELECT * FROM water_schedules ORDER BY day,time,id')]


def save(payload, schedule_id=None):
    if not isinstance(payload, dict) or set(payload)-{'day','time','drainSeconds','refillSeconds','durationSeconds','enabled'}:
        raise ValueError('Invalid water schedule.')
    with _lock:
        old = next((s for s in schedules() if s['id']==schedule_id), None)
        if schedule_id is not None and old is None:
            raise ValueError('Water schedule not found.')
        values={**(old or {}), **payload}
        day=config.validate_feeding_day(values.get('day'))
        at=config.validate_feed_time(values.get('time'))
        if 'durationSeconds' in payload:
            if 'drainSeconds' in payload or 'refillSeconds' in payload:
                raise ValueError('Choose one shared duration for both pumps.')
            values['drainSeconds'] = values['refillSeconds'] = payload['durationSeconds']
        drain=_seconds(values.get('drainSeconds'), 'Drain duration', 600)
        refill=_seconds(values.get('refillSeconds'), 'Refill timeout', 1800)
        enabled=config.normalize_schedule_enabled(values.get('enabled',True))
        try:
            with config.db_connect() as conn:
                if old:
                    # Never clear today's claim when editing: prevents an extra drain.
                    conn.execute('UPDATE water_schedules SET day=?,time=?,drain_seconds=?,refill_seconds=?,enabled=? WHERE id=?',
                                 (day,at,drain,refill,int(enabled),schedule_id))
                else:
                    schedule_id=conn.execute('INSERT INTO water_schedules(day,time,drain_seconds,refill_seconds,enabled) VALUES(?,?,?,?,?)',
                                             (day,at,drain,refill,int(enabled))).lastrowid
        except sqlite3.IntegrityError as exc:
            raise ValueError('A water schedule already exists for that day and time.') from exc
        return next(s for s in schedules() if s['id']==schedule_id)


def delete(schedule_id):
    with _lock, config.db_connect() as conn:
        conn.execute('DELETE FROM water_schedules WHERE id=?',(schedule_id,))


def busy():
    with _lock:
        return _active is not None


def _record(status, reason=''):
    if not _active:
        return
    _active.update(status=status, reason=reason)
    with config.db_connect() as conn:
        conn.execute('UPDATE water_jobs SET status=?,reason=?,updated_at=? WHERE id=?',
                     (status,reason,time.time(),_active['id']))
        conn.execute('UPDATE water_schedules SET last_status=? WHERE id=? AND last_date=?',
                     (status,_active['schedule_id'],_active['trigger_date']))


def disconnect():
    global _ready, _active
    with _lock:
        _ready=False
        _record('interrupted','Mega disconnected; run will not restart')
        _active=None


def handle_status(fields):
    global _ready, _active
    with _lock:
        if fields.get('WATERCHANGE')=='V1':
            _ready=True


def _stop_exchange(outcome, reason):
    """A pending stop retains the reservation until feedback or disconnect."""
    if _active['status'] == 'stopping':
        if outcome != 'complete':
            _active['outcome'] = outcome
            _record('stopping', reason)
        return
    _active['outcome'] = outcome
    _active['pending'].append((('OFF', 'OFF'), time.monotonic()))
    config.send_to_identified_output_serial_ports('MegaOutput,PUMP:STOP_ALL\n')
    _record('stopping', reason)


def _request_pumps(mode, now):
    """Send on transitions only; retain ordered acknowledgements for this serial link."""
    if _active['targetMode'] == mode:
        return
    if len(_active['pending']) >= 3:
        _stop_exchange('interrupted', 'Controller is not keeping up with level changes')
        return
    command, pair, _ = _PUMPS[mode]
    _active['targetMode'] = mode
    _active['pending'].append((pair, now))
    if not config.send_to_identified_output_serial_ports(f'MegaOutput,PUMP:{command}\n'):
        _stop_exchange('interrupted', 'Pump adjustment delivery uncertain')


def pump_feedback(fields):
    global _active
    with _lock:
        if not _active:
            return
        pair = fields.get('PUMP_EXTRACT'), fields.get('PUMP_INSERT')
        pending = _active['pending']
        if fields.get('REASON') != 'COMMAND' or not pending or pair != pending[0][0]:
            if _active['status'] != 'stopping' and pair != _PUMPS[_active['pumpMode']][1]:
                _stop_exchange('failed', 'Pump operation changed unexpectedly')
            return
        pending.pop(0)
        if pair != ('OFF', 'OFF'):
            _active['confirmed'] = True
            _active['pumpMode'] = next(mode for mode, spec in _PUMPS.items() if spec[1] == pair)
            if _active['status'] != 'stopping':
                _record('exchanging', _PUMPS[_active['pumpMode']][2])
        elif _active['status'] == 'stopping' and not pending and _active['confirmed']:
            # An ordered start/adjustment/stop reply chain prevents stale replies
            # from releasing the reservation. Missing replies expire the watchdog.
            outcome = _active['outcome']
            if outcome == 'complete':
                config.send_to_identified_output_serial_ports('MegaOutput,WATERMODE:AUTO\n')
            _record(outcome, _active['reason'])
            _active = None


def service_exchange():
    """Service the timer and level checks before renewing the Mega watchdog."""
    with _lock:
        if not _active:
            return True
        if _active['status'] == 'stopping':
            return False
        reason = _level_fault()
        now = time.monotonic()
        elapsed = now - _active['monotonic']
        if reason or calibration.active() or config.shutting_down:
            _stop_exchange('failed', reason or 'System or calibration changed')
        elif _active['pending'] and now - _active['pending'][0][1] >= PUMP_ACK_SECONDS:
            _stop_exchange('interrupted', 'Pump command not confirmed')
        elif elapsed >= _active['durationSeconds']:
            _stop_exchange('complete', 'Shared duration elapsed')
        else:
            low, normal, upper = (sensor_control.float_reading(k) for k in sensor_control.FLOAT_KEYS)
            if upper or not normal:
                _active['normalSince'] = None
                _request_pumps('drain' if upper else 'refill', now)
            elif _active['targetMode'] != 'both':
                # Require stable Normal before resuming both, avoiding relay chatter.
                if _active['normalSince'] is None:
                    _active['normalSince'] = now
                elif now - _active['normalSince'] >= NORMAL_RECOVERY_SECONDS:
                    _request_pumps('both', now)
        return _active['status'] != 'stopping'


def _level_fault():
    if not sensor_control.all_floats_enabled():
        return 'Enable all three float switches'
    if not 0 <= time.time() - config.last_sensor_received_at <= 3:
        return 'Water-level readings are stale'
    floats = [sensor_control.float_reading(k) for k in sensor_control.FLOAT_KEYS]
    if any(type(value) is not bool for value in floats):
        return 'Water-level readings are unavailable'
    low, normal, upper = floats
    if (upper and not normal) or (normal and not low):
        return 'Float readings are inconsistent'
    return None


def stop():
    with _lock:
        if _active:
            _stop_exchange('cancelled', 'Stopped by operator')
        else:
            config.send_to_identified_output_serial_ports('MegaOutput,WATERCHANGE:STOP\n')
            water_control.manual_action('stop_all')
        return response()


def _block_reason():
    if config.shutting_down: return 'System off'
    if not _ready or len(config.output_serial_ports)!=1: return 'Controller disconnected or not ready'
    if _active: return 'Another water change is active'
    state=water_control.get_status()
    if state['manualOverride']: return 'Automatic water control is paused'
    if state['extractionOn'] or state['insertionOn']: return 'Pumps already running'
    feeder=feeding_control.get_status()
    if feeder['busy']: return 'Feeder is busy'
    if calibration.active(): return 'Sensor calibration is active'
    fault = _level_fault()
    if fault:
        return fault
    if [sensor_control.float_reading(k) for k in sensor_control.FLOAT_KEYS] != [True, True, False]:
        return 'Water must be at Normal before starting a scheduled exchange'
    return None


def process_due(local_now=None):
    global _active
    zone=get_feeding_zone()
    now=local_now or datetime.now(zone)
    now=now.replace(tzinfo=zone) if now.tzinfo is None else now.astimezone(zone)
    with _lock:
        if config.shutting_down: return []
        claimed=[]
        with config.db_connect() as conn:
            conn.row_factory=sqlite3.Row
            conn.execute('BEGIN IMMEDIATE')
            for row in conn.execute('SELECT * FROM water_schedules WHERE enabled=1 ORDER BY id').fetchall():
                daily=row['day']==config.EVERY_DAY
                back=0 if daily else (now.weekday()-row['day'])%7
                hour,minute=map(int,row['time'].split(':'))
                occurrence=(now-timedelta(days=back)).replace(hour=hour,minute=minute,second=0,microsecond=0)
                if daily and occurrence>now: occurrence-=timedelta(days=1)
                date=occurrence.date().isoformat()
                if not 0 <= (now-occurrence).total_seconds() < 60 or row['last_date']==date: continue
                conn.execute("UPDATE water_schedules SET last_date=?,last_at=?,last_status='claimed' WHERE id=?",(date,now.timestamp(),row['id']))
                claimed.append((_row(row),date,occurrence.timestamp()))
        results=[]
        for schedule,date,scheduled_at in claimed:
            reason='Missed while application was stopped' if scheduled_at<_started_at else _block_reason()
            with config.db_connect() as conn:
                status='skipped' if reason else 'sent'
                job=conn.execute('INSERT INTO water_jobs(schedule_id,trigger_date,status,reason,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                                 (schedule['id'],date,status,reason or '',time.time(),time.time())).lastrowid
                conn.execute('UPDATE water_schedules SET last_status=? WHERE id=?',(status,schedule['id']))
            if not reason:
                started = time.monotonic()
                _active=dict(id=job,schedule_id=schedule['id'],trigger_date=date,status='sent',reason='',
                             monotonic=started, durationSeconds=schedule['durationSeconds'],
                             confirmed=False, targetMode='both', pumpMode='both', normalSince=None,
                             pending=[(('ON', 'ON'), started)])
                sent = config.send_to_identified_output_serial_ports('MegaOutput,PUMP:BOTH_ON\n')
                if not sent:
                    _stop_exchange('interrupted', 'Start delivery uncertain')
                    status = 'stopping'
            results.append(dict(scheduleId=schedule['id'],status=status,reason=reason))
        return results


def next_run(items, local_now=None):
    zone=get_feeding_zone()
    now=local_now or datetime.now(zone)
    now=now.replace(tzinfo=zone) if now.tzinfo is None else now.astimezone(zone)
    candidates=[]
    for item in items:
        if not item['enabled']: continue
        daily=item['day']==config.EVERY_DAY
        ahead=0 if daily else (item['day']-now.weekday())%7
        hour,minute=map(int,item['time'].split(':'))
        at=(now+timedelta(days=ahead)).replace(hour=hour,minute=minute,second=0,microsecond=0)
        if at<=now and ((now-at).total_seconds()>=60 or at.timestamp()<_started_at or
                       item['lastTriggeredDate']==at.date().isoformat()):
            at+=timedelta(days=1 if daily else 7)
        candidates.append((at,item['id']))
    if not candidates:return None
    at,ident=min(candidates)
    return dict(scheduleId=ident,dateTime=at.isoformat())


def response():
    with _lock, config.db_connect() as conn:
        conn.row_factory=sqlite3.Row
        row=conn.execute('SELECT * FROM water_jobs ORDER BY id DESC LIMIT 1').fetchone()
        items=schedules()
        active = None
        if _active:
            active = {k:v for k,v in _active.items() if k in {
                'id', 'schedule_id', 'trigger_date', 'status', 'reason', 'durationSeconds'}}
            active['controlText'] = ('Waiting for pump confirmation' if _active['pending']
                                     else _PUMPS[_active['pumpMode']][2])
            # Display only: polling must never reset or advance the pump timer.
            active['remainingSeconds'] = max(0, min(_active['durationSeconds'], math.ceil(
                _active['durationSeconds'] - (time.monotonic() - _active['monotonic']))))
        return dict(ok=True,schedules=items,timezone=str(get_feeding_zone()),nextRun=next_run(items),
                    ready=_ready and len(config.output_serial_ports)==1,
                    active=active,
                    last=dict(row) if row else None)


def worker(stop_event):
    while not stop_event.is_set():
        try: process_due()
        except Exception as exc: print(f'Water schedule error: {exc}')
        if stop_event.wait(1): break
