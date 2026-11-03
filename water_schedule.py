"""Recurring water changes. Pi schedules; the Mega runs a bounded drain/refill.

Claims/jobs are durable before sending and never replayed after interruption.
All actuator admission uses the shared sensor lock, including feeder requests.
"""
import re
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
        conn.execute("UPDATE water_jobs SET status='interrupted',reason='Application restarted',updated_at=? WHERE status IN ('sent','draining','refilling','stopping')", (time.time(),))
        conn.execute("UPDATE water_schedules SET last_status='interrupted' WHERE last_status IN ('claimed','sent','draining','refilling','stopping')")
        _active, _ready, _started_at = None, False, time.time()


def _seconds(value, name, maximum):
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value)) or not 1 <= int(value) <= maximum:
        raise ValueError(f'{name} must be a whole number from 1 to {maximum} seconds.')
    return int(value)


def _row(row):
    return dict(id=row['id'], day=row['day'], dayName=config.FEEDING_DAY_NAMES[row['day']],
                time=row['time'], drainSeconds=row['drain_seconds'], refillSeconds=row['refill_seconds'],
                enabled=bool(row['enabled']), lastTriggeredDate=row['last_date'],
                lastTriggeredAt=row['last_at'], lastStatus=row['last_status'])


def schedules():
    with _lock, config.db_connect() as conn:
        conn.row_factory=sqlite3.Row
        return [_row(r) for r in conn.execute('SELECT * FROM water_schedules ORDER BY day,time,id')]


def save(payload, schedule_id=None):
    if not isinstance(payload, dict) or set(payload)-{'day','time','drainSeconds','refillSeconds','enabled'}:
        raise ValueError('Invalid water schedule.')
    with _lock:
        old = next((s for s in schedules() if s['id']==schedule_id), None)
        if schedule_id is not None and old is None:
            raise ValueError('Water schedule not found.')
        values={**(old or {}), **payload}
        day=config.validate_feeding_day(values.get('day'))
        at=config.validate_feed_time(values.get('time'))
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
        if not _active or fields.get('WCJOB')!=str(_active['id']):
            return
        status=fields.get('WC','').lower()
        if status not in {'draining','refilling','complete','cancelled','failed','rejected'}:
            return
        _record(status,fields.get('WCREASON','').lower().replace('_',' '))
        if status in {'complete','cancelled','failed','rejected'}:
            _active=None


def stop():
    global _active
    with _lock:
        sent=config.send_to_identified_output_serial_ports('MegaOutput,WATERCHANGE:STOP\n')
        water_control.manual_action('stop_all')
        if _active:
            _record('stopping' if sent else 'interrupted','Stopped by operator')
            if not sent: _active=None
        return response()


def _block_reason():
    if config.shutting_down: return 'System off'
    if not _ready or len(config.output_serial_ports)!=1: return 'Connect Mega with water-change firmware'
    if _active: return 'Another water change is active'
    state=water_control.get_status()
    if state['manualOverride']: return 'Automatic water control is paused'
    if state['extractionOn'] or state['insertionOn']: return 'Pumps already running'
    feeder=feeding_control.get_status()
    if feeder['busy']: return 'Feeder is busy'
    if calibration.active(): return 'Sensor calibration is active'
    if not sensor_control.all_floats_enabled(): return 'Enable all three float switches'
    if not config.last_sensor_received_at or time.time()-config.last_sensor_received_at>3:
        return 'Water-level readings are stale'
    floats=[sensor_control.float_reading(k) for k in sensor_control.FLOAT_KEYS]
    if floats != [True,True,False]: return 'Water must be at Normal with valid float readings'
    return None


def process_due(local_now=None):
    global _active
    zone=get_feeding_zone()
    now=local_now or datetime.now(zone)
    now=now.replace(tzinfo=zone) if now.tzinfo is None else now.astimezone(zone)
    with _lock:
        if _active and time.monotonic()-_active['monotonic']>_active['timeout']:
            # Ambiguous completion is not retried; the Mega also owns its timeout.
            config.send_to_identified_output_serial_ports('MegaOutput,WATERCHANGE:STOP\n')
            water_control.manual_action('stop_all')
            _record('interrupted','No completion confirmation')
            _active=None
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
                _active=dict(id=job,schedule_id=schedule['id'],trigger_date=date,status='sent',reason='',
                             monotonic=time.monotonic(),timeout=schedule['drainSeconds']+schedule['refillSeconds']+15)
                sent=config.send_to_identified_output_serial_ports(
                    f"MegaOutput,WATERCHANGE:START,JOB:{job},DRAIN:{schedule['drainSeconds']},REFILL:{schedule['refillSeconds']}\n")
                if not sent:
                    _record('interrupted','Command delivery uncertain; not retried'); _active=None; status='interrupted'
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
        return dict(ok=True,schedules=items,timezone=str(get_feeding_zone()),nextRun=next_run(items),
                    ready=_ready and len(config.output_serial_ports)==1,
                    active={k:v for k,v in _active.items() if k not in {'monotonic','timeout'}} if _active else None,
                    last=dict(row) if row else None)


def worker(stop_event):
    while not stop_event.is_set():
        try: process_due()
        except Exception as exc: print(f'Water schedule error: {exc}')
        if stop_event.wait(1): break
