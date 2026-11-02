"""Daily recurrence, preserved database data and complete pump-pair commands."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
import config
import water_control as water
import sensor_control as sensors
import feeding_scheduler as scheduler
import feeding_control as feeder
from flask_server import flask_app
from test_system import isolated, connected
from test_sensor_controls import packet, admin
from test_fan_confirmation import clock


def pair():
    status = water.get_status()
    return status['extractionOn'], status['insertionOn']












def test_manual_both_auth_stop_restore_and_feeder_conflict():
    port = connected()
    assert flask_app.test_client().post('/api/water-control',json={'action':'both_start'}).status_code == 403
    client = admin()
    assert client.post('/api/water-control',json={'action':'both_start'}).status_code == 200
    assert pair() == (True, True)
    packet(upper='1'); assert pair() == (True, True)  # explicit manual override
    with pytest.raises(ValueError): feeder.request_feed(1)
    water.manual_action('extract_start'); assert pair() == (True, False)
    water.manual_action('both_start'); water.manual_action('insert_start')
    assert pair() == (False, True)
    water.manual_action('stop_all'); packet(ph='3')
    assert pair() == (False, False)
    water.manual_action('auto_resume'); packet(ph='3')
    assert pair() == (False, False)
    assert water.get_status()['phSettling']['active']
    water.stop_all_for_shutdown(); assert pair() == (False, False)
    config.output_serial_ports.clear()
    with pytest.raises(ValueError): water.manual_action('both_start')


@pytest.mark.parametrize('stop_action,expected,command', [
    ('extract_stop', (False, True), 'INSERT_ON'),
    ('insert_stop', (True, False), 'EXTRACT_ON'),
])
def test_stop_one_pump_preserves_other_and_pauses_automation(stop_action, expected, command):
    port = connected()
    water.manual_action('both_start')
    water.manual_action(stop_action)
    assert pair() == expected
    assert port.messages[-1] == f'MegaOutput,PUMP:{command}\n'
    assert water.get_status()['manualOverride']
    packet(ph='3')
    assert pair() == expected
    water.manual_action('insert_stop' if stop_action == 'extract_stop' else 'extract_stop')
    assert pair() == (False, False)
    assert port.messages[-1] == 'MegaOutput,PUMP:STOP_ALL\n'


def test_daily_once_on_all_seven_days_and_next_run():
    connected()
    item = config.create_feeding_schedule(7, '08:00', cycles=4)
    monday = datetime(2026,10,19,8,0,tzinfo=ZoneInfo('Asia/Manila'))
    for i in range(8):
        now = monday + timedelta(days=i)
        scheduler.process_due_feeding_schedules(now)
        active = feeder.get_status()['active']
        assert active is not None and active['cycles'] == 4
        feeder.handle_status(f"MegaOutput,FEED:COMPLETE,JOB:{active['id']}")
        scheduler.process_due_feeding_schedules(now)
        assert not feeder.get_status()['busy']
        upcoming = scheduler.get_next_feeding_run(config.get_feeding_schedules(), now)
        assert datetime.fromisoformat(upcoming['dateTime']) == now + timedelta(days=1)
    assert config.get_feeding_schedule(item['id'])['dayName'] == 'Every day (Monday–Sunday)'


def test_daily_midnight_grace_and_paused_schedule():
    item = config.create_feeding_schedule(7, '23:59')
    now = datetime(2026,10,20,0,1,tzinfo=ZoneInfo('Asia/Manila'))
    due = config.claim_due_feeding_schedules(now)
    assert len(due) == 1 and due[0]['triggerDate'] == '2026-10-19'
    assert config.claim_due_feeding_schedules(now) == []
    config.update_feeding_schedule(item['id'],7,'23:59',False)
    assert config.claim_due_feeding_schedules(now + timedelta(days=1)) == []
    assert scheduler.get_next_feeding_run(config.get_feeding_schedules(), now) is None


def test_daily_api_edit_toggle_delete_and_duplicates():
    client = admin()
    response = client.post('/api/feeding-schedules',json={'day':7,'time':'09:00','cycles':30})
    assert response.status_code == 201
    item = response.json['schedule']; ident = item['id']
    assert client.post('/api/feeding-schedules',json={'day':7,'time':'09:00'}).status_code == 400
    assert client.put(f'/api/feeding-schedules/{ident}',json={'enabled':False}).json['schedule']['day'] == 7
    changed = client.put(f'/api/feeding-schedules/{ident}',json={'day':6,'enabled':True}).json['schedule']
    assert changed['dayName'] == 'Sunday' and changed['cycles'] == 30
    assert client.put(f'/api/feeding-schedules/{ident}',json={'day':7}).json['schedule']['day'] == 7
    assert client.delete(f'/api/feeding-schedules/{ident}').status_code == 200
    assert config.get_feeding_schedules() == []


def test_migrate_original_check_preserve_history_and_sequence(tmp_path,monkeypatch):
    old = tmp_path / 'old.db'; monkeypatch.setattr(config,'DB_FILE',str(old))
    with sqlite3.connect(old) as conn:
        conn.execute('''CREATE TABLE feeding_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
            feed_time TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)),
            last_triggered_date TEXT, last_triggered_at REAL, last_status TEXT,
            created_at REAL NOT NULL, updated_at REAL NOT NULL, cycles INTEGER NOT NULL DEFAULT 3,
            UNIQUE(day_of_week,feed_time))''')
        conn.execute("INSERT INTO feeding_schedules VALUES(9,0,'08:00',1,'2026-10-19',123,'complete',10,20,30)")
        conn.execute("INSERT INTO feeding_schedules VALUES(99,6,'09:00',0,NULL,NULL,NULL,1,1,4)")
        conn.execute('DELETE FROM feeding_schedules WHERE id=99')
    config.init_database(); config.init_database()
    item = config.get_feeding_schedule(9)
    assert item['cycles'] == 30 and item['lastTriggeredDate'] == '2026-10-19' and item['lastStatus'] == 'complete'
    daily = config.create_feeding_schedule(7,'08:00')
    assert daily['id'] > 99


def test_removed_custom_turbidity_fields_not_saved():
    changed = config.update_limits_from_payload({'turbidityLimit':9999, 'turbidityCriticalLimit':1})
    assert changed == {}


def test_desktop_daily_date_and_form_mapping():
    from tkinter_gui import FocusedMonitorApp
    from local_controls import LIMIT_FIELDS
    app = FocusedMonitorApp.__new__(FocusedMonitorApp)
    item = config.create_feeding_schedule(7, '08:00')
    now = datetime(2026,10,25,9,0,tzinfo=ZoneInfo('Asia/Manila'))  # Sunday
    assert 'Monday, Oct 26, 2026' == app._format_schedule_next_feed(item, now)
    assert config.FEEDING_DAY_NAMES.index(item['dayName']) == 7
    assert not any(key.startswith('turbidity') for key, _, _ in LIMIT_FIELDS)


def test_day_rejects_fractional_and_out_of_range_values():
    for invalid in (7.1, True, 8, -1, None, 'everyday'):
        with pytest.raises(ValueError): config.create_feeding_schedule(invalid, '08:00')
