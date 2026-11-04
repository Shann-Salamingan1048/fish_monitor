from datetime import timedelta
from pathlib import Path
import copy
import pytest
import calibration
import config
import mega_control
import feeding_control as feeder
import feeding_scheduler as scheduler
import water_control as water
from flask_server import flask_app
from test_system import isolated, connected, due
from test_sensor_controls import packet

THREE = dict(ph=dict(points=3, adc401=1000.25, adc686=900.125, adc918=800.5), turbidity=None)


def test_three_point_save_reload_switch_back_and_wire_protocol():
    connected(); calibration.set_active(True); calibration.save(THREE)
    calibration.initialize()
    assert calibration.status()['profile'] == THREE
    _, payload = mega_control.commands()
    assert 'PC:3,A4:1000.2500' in payload
    assert 'A6:900.1250,A9:800.5000' in payload
    assert all(len(line) < 256 for line in payload.splitlines())
    # Existing two-point profiles remain valid, and do not reuse an acid anchor.
    legacy = dict(ph=dict(adc686=900, adc918=800), turbidity=None)
    calibration.save(legacy)
    assert calibration.status()['profile'] == legacy
    assert 'PC:2,A4:0.0000' in mega_control.commands()[1]


@pytest.mark.parametrize('change', [dict(points=4), dict(points=True), dict(adc401=None),
                                   dict(adc401=910), dict(adc401=1023), dict(adc401=float('nan')),
                                   dict(points=2), dict(extra=123)])
def test_invalid_three_point_profile_does_not_replace_saved_values(change):
    calibration.set_active(True); calibration.save(THREE)
    invalid = copy.deepcopy(THREE); invalid['ph'].update(change)
    with pytest.raises(ValueError): calibration.save(invalid)
    assert calibration.status()['profile'] == THREE


def test_three_point_admin_api_and_reported_ph(monkeypatch):
    monkeypatch.setattr(config,'DEVELOPER_OPTIONS',True)
    client=flask_app.test_client()
    client.post('/api/auth/login',json={'pin':config.ADMIN_PIN})
    client.post('/api/developer/calibration',json={'active':True})
    assert client.put('/api/developer/calibration',json=THREE).status_code==200
    assert client.get('/api/developer/calibration').json['calibration']['profile']==THREE
    assert packet(ph='4.01')[0]['ph']=='4.01'  # Host does not apply another formula.
    page=client.get('/developer').text
    assert 'id="ph-points"' in page and 'id="adc401"' in page


@pytest.mark.parametrize('mode',['FLOAT_ONLY','AUTO_QUALITY','NORMAL'])
def test_temperature_display_and_alerts_remain_active_in_pump_modes(mode):
    connected(); water._state.update(sensorMode=mode,extractionOn=True)
    snapshot, alerts = packet(temp='40')
    assert snapshot['temp']=='40' and water.get_status()['temperatureActive']
    assert any(a['sensor']=='Temperature' for a in alerts)


def test_hardware_pump_race_postpones_feed_without_replaying_started_jobs():
    port=connected(); item,now=due(2)
    scheduler.process_due_feeding_schedules(now)
    job=feeder.get_status()['active']['id']
    feeder.handle_status(f'MegaOutput,FEED:DEFERRED,JOB:{job}')
    assert not feeder.get_status()['busy']
    assert config.get_feeding_schedule(item['id'])['lastStatus']=='pending'
    water._state['extractionOn']=True
    scheduler.process_due_feeding_schedules(now+timedelta(hours=2))
    assert len(port.messages)==1
    water._state['extractionOn']=False
    scheduler.process_due_feeding_schedules(now+timedelta(hours=2))
    assert len(port.messages)==2
    job=feeder.get_status()['active']['id']
    feeder.handle_status(f'MegaOutput,FEED:STARTED,JOB:{job}')
    feeder.handle_status(f'MegaOutput,FEED:DEFERRED,JOB:{job}')
    assert feeder.get_status()['active']['status']=='running'


def test_pending_daily_occurrence_not_overwritten_next_day():
    connected(); item=config.create_feeding_schedule(7,'08:00',True,2)
    _, now=due()
    water._state['insertionOn']=True
    scheduler.process_due_feeding_schedules(now)
    original=config.get_feeding_schedule(item['id'])['lastTriggeredDate']
    scheduler.process_due_feeding_schedules(now+timedelta(days=1))
    assert config.get_feeding_schedule(item['id'])['lastTriggeredDate']==original


def test_removed_notice_text_is_absent_from_gui_and_web():
    root=Path(__file__).resolve().parents[1]
    for name in ['tkinter_gui.py','local_controls.py','templates/index.html','templates/controls.html','static/script.js']:
        text=(root/name).read_text().lower()
        assert 'only enabled sensors are evaluated' not in text
        assert 'only critical ph and very cloudy' not in text
