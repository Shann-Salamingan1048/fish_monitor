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








def announce(mode='AUTO', state='OFF'):
    fan.handle_status(f'MegaOutput,COOLER:{state},FANCTRL:V1,FANMODE:{mode},FANPENDING:NONE,REASON:STATUS')


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
    assert port.messages[-1] == 'MegaOutput,FANMODE:ON\n'
    announce('ON', 'ON')
    assert client.get('/api/sensors').json['fan']['confirmed']
    sensors.apply({'temp':False})
    assert fan.get_status()['mode'] == 'ON'
    fan.disconnected()
    assert fan.get_status()['on'] is None
    fan.handle_status('MegaOutput,READY:1,FANCTRL:V1')
    assert port.messages[-1] == 'MegaOutput,FANMODE:ON\n'
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
    assert port.messages[-1] == 'MegaOutput,FANMODE:OFF\n'


def test_temperature_packets_are_not_echoed_to_mega():
    port = connected()
    config.serial_ports.append(port)
    try:
        packet(temp='ERROR')
        assert not any(',TEMP:' in m for m in port.messages)
    finally:
        config.serial_ports.remove(port)
