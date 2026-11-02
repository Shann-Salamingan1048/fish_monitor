import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import threading
from unittest.mock import patch
import pytest
import config
import feeding_control as feeder
import feeding_scheduler as scheduler
import water_control
import sensor_control
import fan_control
import calibration
import admin_sessions
from flask_server import flask_app
from mcu_comm import MCUCommunicator

class FakeSerial:
    is_open = True
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail
    def write(self, data):
        self.messages.append(data.decode())
        if self.fail:
            raise OSError('connection lost')
        return len(data)

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    with admin_sessions._lock:
        admin_sessions._active.clear()
    monkeypatch.setattr(config, 'shutting_down', False)
    monkeypatch.setattr(config, 'MAX_ADMIN_SESSIONS', 2)
    config.serial_ports.clear()
    monkeypatch.setattr(config, 'DB_FILE', str(tmp_path / 'test.db'))
    # Existing tests exercise state transitions without waiting; confirmation
    # timing is covered separately with a controlled monotonic clock.
    monkeypatch.setattr(config, 'QUALITY_CONFIRM_SECONDS', 0.0)
    fan_control.initialize()
    config.init_database()
    config.load_persisted_settings()
    calibration.initialize()
    sensor_control.initialize()
    feeder.initialize()
    config.output_serial_ports.clear()
    config.input_serial_ports.clear()
    config.active_website_viewers.clear()
    config.last_sensor_received_at = 0
    with water_control._lock:
        water_control._state.update(mode='idle', extractionOn=False, insertionOn=False, manualOverride=False, sensorMode='NORMAL', startedAt=None, levelCorrection=None, qualitySensor=None, phSettlingRequired=False, phSettlingSince=None)
    yield
    config.output_serial_ports.clear()


def connected(fail=False):
    port = FakeSerial(fail)
    config.output_serial_ports.append(port)
    feeder.handle_status('MegaOutput,READY:1,FEEDER:V3')
    return port


def due(cycles=3):
    now = datetime(2026,10,12,8,0,0,tzinfo=ZoneInfo('Asia/Manila'))
    item = config.create_feeding_schedule(0,'08:00', True, cycles)
    return item, now

@pytest.mark.parametrize('bad', [0,4294967296,-1,True,1.5,'1.5','nan',None,'1e1'])
def test_cycle_validation(bad):
    with pytest.raises(ValueError): config.create_feeding_schedule(0, '08:00', cycles=bad)
    assert config.get_feeding_schedules() == []


def test_migration_preserves_existing_schedule(tmp_path, monkeypatch):
    path = tmp_path / 'old.db'
    with sqlite3.connect(path) as conn:
        conn.execute('''CREATE TABLE feeding_schedules(id INTEGER PRIMARY KEY,day_of_week INTEGER,feed_time TEXT,enabled INTEGER,last_triggered_date TEXT,last_triggered_at REAL,last_status TEXT,created_at REAL,updated_at REAL,UNIQUE(day_of_week,feed_time))''')
        conn.execute("INSERT INTO feeding_schedules VALUES(9,0,'08:00',1,NULL,NULL,NULL,0,0)")
    monkeypatch.setattr(config, 'DB_FILE', str(path))
    config.init_database(); config.init_database()
    assert config.get_feeding_schedule(9)['cycles'] == 3


def test_schedule_update_preserves_cycles_on_toggle():
    item,_ = due(7)
    config.update_feeding_schedule(item['id'], 0, '08:00', False)
    assert config.get_feeding_schedule(item['id'])['cycles'] == 7


def test_send_is_not_completion_and_busy_rejects_duplicate():
    port = connected()
    state = feeder.request_feed(4)
    job = state['active']['id']
    assert port.messages == [f'MegaOutput,FEED:CUSTOM,CYCLES:4,JOB:{job}\n']
    assert state['active']['status'] == 'sent'
    with pytest.raises(ValueError): feeder.request_feed(4)
    feeder.handle_status(f'MegaOutput,FEED:STARTED,JOB:{job}')
    assert feeder.get_status()['active']['status'] == 'running'
    feeder.handle_status(f'MegaOutput,FEED:COMPLETE,JOB:{job}')
    assert not feeder.get_status()['busy']
    assert feeder.get_status()['last']['status'] == 'complete'


def test_simultaneous_manual_requests_only_one_sent():
    port = connected()
    def attempt():
        try: feeder.request_feed(1)
        except ValueError: pass
    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(port.messages) == 1


def test_disconnected_legacy_and_pumping_rejected():
    with pytest.raises(ValueError): feeder.request_feed()
    config.output_serial_ports.append(FakeSerial())
    with pytest.raises(ValueError): feeder.request_feed()
    feeder.handle_status('MegaOutput,FEEDER:V3')
    water_control._state['extractionOn'] = True
    with pytest.raises(ValueError): feeder.request_feed()


def test_scheduled_exactly_once_and_acknowledged():
    port = connected(); item, now = due(6)
    scheduler.process_due_feeding_schedules(now)
    scheduler.process_due_feeding_schedules(now)
    assert len(port.messages) == 1 and 'CYCLES:6' in port.messages[0]
    assert config.get_feeding_schedule(item['id'])['lastStatus'] == 'sent'
    job = feeder.get_status()['active']['id']
    feeder.handle_status(f'MegaOutput,FEED:COMPLETE,JOB:{job}')
    scheduler.process_due_feeding_schedules(now)
    assert len(port.messages) == 1
    assert config.get_feeding_schedule(item['id'])['lastStatus'] == 'complete'


def test_pending_feed_waits_for_pumps_then_uses_custom_cycles():
    port = connected(); item, now = due(2)
    water_control._state['insertionOn'] = True
    scheduler.process_due_feeding_schedules(now)
    assert not port.messages
    water_control._state['insertionOn'] = False
    scheduler.process_due_feeding_schedules(now + timedelta(minutes=10))
    assert 'CYCLES:2' in port.messages[0]


def test_pause_skips_due_and_manual_still_available():
    port = connected(); item, now = due()
    feeder.set_paused(True)
    scheduler.process_due_feeding_schedules(now)
    feeder.set_paused(False)
    scheduler.process_due_feeding_schedules(now)
    assert not port.messages
    feeder.set_paused(True)
    feeder.request_feed(2)
    assert len(port.messages) == 1


def test_paused_schedule_cancels_pending():
    port = connected(); item, now = due()
    water_control._state['insertionOn'] = True
    scheduler.process_due_feeding_schedules(now)
    config.update_feeding_schedule(item['id'], 0,'08:00',False)
    water_control._state['insertionOn'] = False
    scheduler.process_due_feeding_schedules(now)
    assert not port.messages


def test_old_pending_does_not_build_backlog():
    port = connected(); item, now = due()
    water_control._state['insertionOn'] = True
    scheduler.process_due_feeding_schedules(now)
    water_control._state['insertionOn'] = False
    scheduler.process_due_feeding_schedules(now + timedelta(minutes=31))
    assert not port.messages
    assert config.get_feeding_schedule(item['id'])['lastStatus'] == 'expired'


def test_write_failure_and_restart_never_replay():
    port=connected(True); item,now=due()
    scheduler.process_due_feeding_schedules(now)
    assert feeder.get_status()['last']['status'] == 'unknown'
    feeder.initialize()
    feeder.handle_status('MegaOutput,FEEDER:V3')
    scheduler.process_due_feeding_schedules(now)
    assert len(port.messages) == 1


def test_stop_waits_for_ack():
    port=connected(); feeder.request_feed(2)
    feeder.stop_feeder()
    assert feeder.get_status()['busy']
    feeder.handle_status('MegaOutput,FEED:STOPPED,JOB:0')
    assert not feeder.get_status()['busy']
    assert feeder.get_status()['last']['status'] == 'cancelled'


def test_limits_validation_is_atomic():
    old = config.get_public_limits()
    with pytest.raises(ValueError):
        config.update_limits_from_payload({'tempLimit':28, 'phLowLimit':13})
    assert config.get_public_limits() == old
    with pytest.raises(ValueError): config.update_limits_from_payload({'tempLimit':'NaN'})
    with pytest.raises(ValueError): config.update_limits_from_payload({'maxCameraViewers':1.5})


def test_flask_auth_assets_schedule_and_feed_routes():
    flask_app.config['TESTING'] = True
    client = flask_app.test_client()
    for url in ('/', '/graphs', '/camera','/static/script.js','/static/style.css'):
        response = client.get(url)
        assert response.status_code == 200
    assert client.post('/api/feeder', json={'action':'feed','cycles':2}).status_code == 403
    assert client.post('/api/auth/login',json={'pin':config.ADMIN_PIN}).status_code == 200
    response=client.post('/api/feeding-schedules',json={'day':2,'time':'09:30','cycles':5})
    assert response.status_code == 201
    item=response.json['schedule']
    assert item['cycles'] == 5
    assert client.put('/api/feeding-schedules/'+str(item['id']),json={'enabled':False}).json['schedule']['cycles']==5
    assert client.post('/api/feeder',json={'action':'feed','cycles':2}).status_code == 409
    port=connected()
    assert client.post('/api/feeder',json={'action':'feed','cycles':2}).status_code == 200
    assert client.post('/api/control',json={'command':'feed'}).status_code == 409
    assert len(port.messages)==1
    assert client.get('/api/sensors').json['connection']['stale'] is True


def test_mcu_feedback_path():
    connected(); feeder.request_feed(1)
    job=feeder.get_status()['active']['id']
    MCUCommunicator()._parse_output_message(f'MegaOutput,FEED:COMPLETE,JOB:{job}')
    assert feeder.get_status()['last']['status']=='complete'


@pytest.mark.parametrize('cycles', [21, 30, 100, 65536, 4294967295])
def test_no_arbitrary_cycle_cap(cycles):
    port = connected()
    item, now = due(cycles)
    assert config.get_feeding_schedule(item['id'])['cycles'] == cycles
    scheduler.process_due_feeding_schedules(now)
    assert f'CYCLES:{cycles},' in port.messages[0]
    assert feeder.get_status()['active']['cycles'] == cycles
    feeder.stop_feeder()
    feeder.handle_status('MegaOutput,FEED:STOPPED,JOB:0')
    assert not feeder.get_status()['busy']


def test_previous_capped_firmware_requires_upgrade():
    config.output_serial_ports.append(FakeSerial())
    feeder.handle_status('MegaOutput,FEEDER:V2')
    with pytest.raises(ValueError):
        feeder.request_feed(30)
