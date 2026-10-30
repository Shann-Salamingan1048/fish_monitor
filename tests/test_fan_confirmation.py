"""Shared fan API and spike rejection with deterministic time."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
import config
import fan_control as fan
import sensor_control as sensors
import water_control as water
from flask_server import flask_app
from test_system import isolated, connected
from test_sensor_controls import packet, admin


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(water.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(config, 'QUALITY_CONFIRM_SECONDS', 5.0)
    water.reset_confirmation()
    return now


@pytest.mark.parametrize('bad', [{'ph':'3'}, {'turbidity':'Very Cloudy'}])
def test_spike_resets_and_sustained_condition_activates(clock, bad):
    connected()
    for t in (100, 101, 102, 104):
        clock[0] = t; packet(**bad)
        assert not water.is_pump_active()
    assert water.get_status()['pendingQuality']
    clock[0] = 104.5; packet()
    assert not water.get_status()['pendingQuality']
    for t in range(105, 110):
        clock[0] = t; packet(**bad)
        assert not water.is_pump_active()
    clock[0] = 110; packet(**bad)
    assert water.get_status()['extractionOn']


def test_gap_invalid_and_opposite_ph_restart_confirmation(clock):
    connected(); packet(ph='3')
    clock[0] += 10; packet(ph='3')
    assert not water.is_pump_active()
    clock[0] += 2; packet(ph='ERROR')
    clock[0] += 1; packet(ph='3')
    clock[0] += 2; packet(ph='11')
    clock[0] += 2; packet(ph='11')
    assert not water.is_pump_active()
    sensors.apply({'ph':False})
    assert not water.get_status()['pendingQuality']
    sensors.apply({'ph':True})
    clock[0] += 2; packet(ph='11')
    assert not water.is_pump_active()
    sensors.input_reconnected()
    assert not water.get_status()['pendingQuality']


def test_water_level_protection_is_immediate(clock):
    connected(); packet(upper='1')
    assert water.get_status()['extractionOn']
    packet(low='0', normal='0', upper='0')
    assert water.get_status()['insertionOn']
    packet()
    assert not water.is_pump_active()


def test_normal_numbers_need_not_be_identical(clock):
    connected()
    for i, ph in enumerate(('9.1', '9.2', '9.0', '9.3', '9.15', '9.2')):
        clock[0] = 100 + i; packet(ph=ph)
    assert water.get_status()['extractionOn']


def test_manual_reset_and_threshold_edit_restart(clock, monkeypatch):
    connected(); packet(ph='10')
    clock[0] += 2; packet(ph='10')
    water.manual_action('stop_all'); water.manual_action('auto_resume')
    clock[0] += 2; packet(ph='10')
    assert not water.is_pump_active()
    monkeypatch.setattr(config, 'PH_HIGH_LIMIT', 9.0)
    clock[0] += 2; packet(ph='10')
    assert water.get_status()['pendingQuality'][0]['seconds'] == 0


def announce(mode='AUTO', state='OFF'):
    fan.handle_status(f'NANOOUTPUT,COOLER:{state},FANCTRL:V1,FANMODE:{mode},FANPENDING:NONE,REASON:STATUS')


def test_fan_auth_acknowledgement_disconnect_and_sync(clock):
    port = connected()
    client = flask_app.test_client()
    assert client.post('/api/fan', json={'mode':'ON'}).status_code == 403
    client = admin()
    assert client.post('/api/fan', json={'mode':'ON'}).status_code == 400
    announce()
    response = client.post('/api/fan', json={'mode':'ON'})
    assert response.status_code == 200
    state = response.json['fan']
    assert state['mode'] == 'ON' and not state['confirmed'] and state['on'] is False
    assert port.messages[-1] == 'NANOOUTPUT,FANMODE:ON\n'
    announce('ON', 'ON')
    assert client.get('/api/sensors').json['fan']['confirmed']
    sensors.apply({'temp':False})
    assert fan.get_status()['mode'] == 'ON'
    fan.disconnected()
    assert fan.get_status()['on'] is None
    fan.handle_status('NANOOUTPUT,READY:1,FANCTRL:V1')
    assert port.messages[-1] == 'NANOOUTPUT,FANMODE:ON\n'
    announce('ON', 'ON')
    clock[0] += 7
    assert not fan.get_status()['available']
    fan.initialize()
    assert fan.get_status()['mode'] == 'AUTO'


@pytest.mark.parametrize('payload', [[], None, {}, {'mode': True}, {'mode':'wrong'}, {'mode':'ON','extra':1}])
def test_fan_invalid_payload(payload):
    connected(); announce()
    assert admin().post('/api/fan', json=payload).status_code == 400


def test_fan_failed_write_and_shutdown():
    port = connected(); announce()
    port.fail = True
    with pytest.raises(ValueError): fan.set_mode('ON')
    assert fan.get_status()['mode'] == 'AUTO'
    port.fail = False
    fan.stop_for_shutdown()
    assert port.messages[-1] == 'NANOOUTPUT,FANMODE:OFF\n'


def test_invalid_temperature_interrupts_fan_confirmation():
    port = connected()
    config.serial_ports.append(port)
    try:
        packet(temp='ERROR')
        assert 'NANOOUTPUT,TEMP:INVALID\n' in port.messages
    finally:
        config.serial_ports.remove(port)
