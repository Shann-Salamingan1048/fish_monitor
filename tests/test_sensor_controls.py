"""Integration checks: actual parser, state machine, persistence and HTTP API."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import threading
import pytest
import config
import sensor_control as sensors
import water_control as water
import feeding_control as feeder
from predictions import build_sensor_alerts
from mcu_comm import MCUCommunicator
from flask_server import flask_app
from test_system import isolated, connected, FakeSerial


def packet(temp='27', ph='7', turbidity='Clear', low='1', normal='1', upper='0', revision=None):
    rev = sensors.get_status()['revision'] if revision is None else revision
    return MCUCommunicator()._parse_sensor_message(
        f'NANOINPUT,Temp:{temp},pH:{ph},Turbidity:{turbidity},'
        f'FloatLow:{low},FloatNormal:{normal},FloatUpper:{upper},SENSORCFG:{rev}')


def admin():
    client = flask_app.test_client()
    client.post('/api/auth/login', json={'pin': config.ADMIN_PIN})
    return client


@pytest.mark.parametrize('key,bad,kwargs', [('ph','3',{'ph':'3'}), ('turbidity','Very Cloudy',{'turbidity':'Very Cloudy'})])
def test_disable_cancels_quality_action_and_remaining_sensor_works(key,bad,kwargs,monkeypatch):
    connected()
    packet(**kwargs)
    assert water.get_status()['extractionOn']
    sensors.apply({key:False})
    assert not water.get_status()['pumpActive']
    snapshot, alerts = packet(**kwargs)
    assert snapshot[key] == 'DISABLED'
    assert not water.get_status()['pumpActive']
    assert not any(a['sensor'] == ('pH Level' if key == 'ph' else 'Turbidity') for a in alerts)
    packet(**({'turbidity':'Very Cloudy'} if key == 'ph' else {'ph':'3'}))
    if key == 'turbidity':
        assert not water.is_pump_active()
        future = water.time.monotonic() + 121
        monkeypatch.setattr(water.time, 'monotonic', lambda: future)
        packet(ph='3')
    assert water.get_status()['extractionOn']


def test_disabled_ph_cancels_refill_owned_by_ph():
    connected(); packet(ph='3'); packet(ph='3',normal='0',low='0')
    assert water.get_status()['mode'] == 'auto_extract_ph'
    assert water.get_status()['insertionOn'] and not water.get_status()['extractionOn']
    sensors.apply({'ph':False})
    assert not water.get_status()['pumpActive']
    # The still-enabled low switch can independently request a refill.
    packet(ph='3',normal='0',low='0')
    assert water.get_status()['mode'] == 'auto_insert'


def test_disabled_low_is_not_dry_and_bypasses_dry_run_rule():
    connected(); sensors.apply({'float_low':False})
    packet(low='0',normal='0')
    assert not water.get_status()['pumpActive']
    packet(low='0',normal='0',turbidity='Very Cloudy')
    assert water.get_status()['extractionOn']
    packet(low='0',normal='0',turbidity='Very Cloudy')
    assert water.get_status()['extractionOn']


def test_disabled_upper_does_not_report_overflow():
    connected(); sensors.apply({'float_upper':False})
    snapshot, alerts = packet(upper='1')
    assert 'Normal' in snapshot['float_stat']
    assert not water.get_status()['pumpActive']
    assert not any('overflow' in a.get('title','').lower() for a in alerts)


def test_disabled_normal_uses_upper_stop_and_does_not_invent_low():
    connected(); sensors.apply({'float_normal':False})
    packet(normal='1',low='1')
    assert not water.get_status()['pumpActive']
    packet(normal='1',low='0')
    assert water.get_status()['insertionOn']
    packet(normal='1',low='1')
    assert water.get_status()['insertionOn']  # disabled normal cannot stop it
    packet(normal='1',low='1',upper='1')
    assert not water.get_status()['pumpActive']


def test_all_floats_disabled_quality_can_work_without_false_refill():
    connected(); sensors.apply({k:False for k in sensors.FLOAT_KEYS})
    snap, alerts = packet(low='0',normal='0',upper='1',ph='3')
    assert snap['float_stat'] == 'DISABLED'
    assert water.get_status()['extractionOn']
    assert not any(a['sensor']=='Water Level' for a in alerts)
    packet(ph='7',low='0',normal='0',upper='1')
    assert not water.get_status()['pumpActive']


def test_temperature_disable_turns_cooler_off_and_stops_forwarding():
    port=connected(); packet(temp='40')
    assert any('TEMP:40' in m for m in port.messages)
    sensors.apply({'temp':False})
    assert port.messages[-1] == 'NANOOUTPUT,TEMPSENSOR:OFF\n'
    port.messages.clear()
    snap, alerts=packet(temp='40')
    assert snap['temp']=='DISABLED' and not any('TEMP:' in m for m in port.messages)
    assert not any(a['sensor']=='Temperature' for a in alerts)


def test_reenable_rejects_old_packets_and_clears_history():
    connected(); packet(ph='3')
    old=sensors.get_status()['revision']
    sensors.apply({'ph':False}); sensors.apply({'ph':True})
    assert config.sensor_data['ph']=='WAITING'
    assert config.sensor_history['ph']==[]
    packet(ph='3',revision=old)
    assert config.sensor_data['ph']=='WAITING'
    assert not water.get_status()['pumpActive']
    packet(ph='7')
    assert config.sensor_data['ph']=='7'
    assert not water.get_status()['pumpActive']


def test_disabled_prediction_cache_cannot_leak():
    sensors.apply({'ph':False})
    import time
    config.active_prediction_alerts['ph_level:rising']={'expires_at':time.time()+100, 'alert':{'sensor':'pH Level','type':'prediction'}}
    alerts=build_sensor_alerts({'ph':'14','temp':'27','float_stat':'Normal','turbidity':'Clear'}, {'ph':[], 'temp':[], 'turbidity':[]})
    assert not any(a['sensor']=='pH Level' for a in alerts)


def test_settings_persist_and_restore_after_restart():
    sensors.apply({'ph':False,'float_upper':False})
    sensors.initialize()
    assert not sensors.enabled('ph') and not sensors.enabled('float_upper')
    input_port=FakeSerial(); output_port=connected()
    config.input_serial_ports.append(input_port)
    sensors.input_reconnected(); sensors.sync_output()
    assert 'SENSORS:29,' in input_port.messages[-1]
    assert 'TEMPSENSOR:ON' in output_port.messages[-1]
    assert config.sensor_data['ph']=='DISABLED'


def test_http_auth_validation_and_cross_interface_sync():
    viewer=flask_app.test_client()
    assert viewer.put('/api/sensor-controls',json={'enabled':{'ph':False}}).status_code==403
    client=admin()
    for bad in ({}, {'enabled':{}}, {'enabled':{'bad':False}}, {'enabled':{'ph':'false'}}, {'enabled':{'ph':False,'temp':1}}, []):
        assert client.put('/api/sensor-controls',json=bad).status_code==400
    assert sensors.enabled('ph')
    response=client.put('/api/sensor-controls',json={'enabled':{'ph':False}})
    assert response.status_code==200
    assert not sensors.enabled('ph')  # Desktop service sees the web change.
    sensors.apply({'temp':False})    # Web sees the desktop service change.
    state=viewer.get('/api/sensors').json
    assert state['temp']=='DISABLED' and state['ph']=='DISABLED'
    assert set(state['sensorControls']['disabled'])=={'ph','temp'}


def test_all_disabled_does_not_raise_stale_fault_and_feeding_still_works():
    connected(); sensors.apply({k:False for k in sensors.SENSORS})
    data=flask_app.test_client().get('/api/sensors').json
    assert data['sensorControls']['allDisabled'] and not data['connection']['stale']
    assert data['alerts']==[]
    assert feeder.request_feed(30)['active']['cycles']==30


def test_disabling_does_not_stop_manual_pump():
    connected(); water.manual_action('extract_start')
    sensors.apply({'ph':False, 'float_low':False})
    packet(ph='2',low='0',normal='0')
    assert water.get_status()['mode']=='manual_extract'
    assert water.get_status()['extractionOn']


def test_noop_does_not_clear_history_or_resend():
    port=connected(); packet()
    before=list(config.sensor_history['ph']); port.messages.clear()
    revision=sensors.get_status()['revision']
    sensors.apply({'ph':True})
    assert sensors.get_status()['revision']==revision
    assert config.sensor_history['ph']==before and not port.messages


def test_failed_persistence_keeps_current_settings(monkeypatch):
    def fail(): raise OSError('disk full')
    monkeypatch.setattr(config,'db_connect',fail)
    with pytest.raises(OSError): sensors.apply({'ph':False})
    assert sensors.enabled('ph')


def test_concurrent_bad_packets_cannot_restart_disabled_ph():
    connected(); errors=[]
    def updates():
        try:
            for _ in range(40): packet(ph='3')
        except Exception as exc: errors.append(exc)
    t=threading.Thread(target=updates);t.start()
    sensors.apply({'ph':False});t.join(timeout=5)
    assert not t.is_alive() and not errors
    assert not water.get_status()['pumpActive']
    assert config.sensor_data['ph']=='DISABLED'
