"""Shutdown race boundary and unit conversion at the actual control/UI paths."""
import threading
import pytest
import config
import water_control
import temperature_units as units
from mcu_comm import MCUCommunicator
from flask_server import flask_app
from test_system import isolated, connected, FakeSerial
from test_sensor_controls import packet


def test_shutdown_closes_all_write_paths_and_ignores_late_sensor_packets():
    output = connected()
    input_port = FakeSerial()
    config.input_serial_ports.append(input_port)
    config.serial_ports.extend([output, input_port])
    water_control.manual_action('both_start')
    config.begin_shutdown()
    for port in (output, input_port):
        assert 'MegaOutput,SYSTEM:OFF\n' in port.messages[-1]
        assert 'MegaInput,SYSTEM:OFF\n' in port.messages[-1]
    counts = (len(output.messages), len(input_port.messages))
    packet(ph='3', temp='40')
    assert not config.send_to_output_serial_ports('MegaOutput,PUMP:BOTH_ON\n')
    assert not MCUCommunicator()._send_serial_message(output, 'MegaOutput,SYSTEM:ON\n')
    assert not config.send_to_input_serial_ports('MegaInput,SYSTEM:ON\n')
    assert (len(output.messages), len(input_port.messages)) == counts


def test_shutdown_writes_after_inflight_command_and_rejects_next():
    started, release = threading.Event(), threading.Event()
    class DelayedSerial(FakeSerial):
        def write(self, data):
            if b'BOTH_ON' in data:
                started.set()
                assert release.wait(2)
            return super().write(data)
    port = DelayedSerial()
    config.output_serial_ports.append(port)
    sender = threading.Thread(target=config.send_to_output_serial_ports, args=('MegaOutput,PUMP:BOTH_ON\n',))
    sender.start(); assert started.wait(2)
    shutdown = threading.Thread(target=config.begin_shutdown);shutdown.start()
    release.set();sender.join(2);shutdown.join(2)
    assert not sender.is_alive() and not shutdown.is_alive()
    assert 'BOTH_ON' in port.messages[0] and 'SYSTEM:OFF' in port.messages[-1]
    assert not config.send_to_output_serial_ports('MegaOutput,FANMODE:ON\n')


@pytest.mark.parametrize('c,f', [(0,32),(30,86),(-40,-40),(100,212),(26.8,80.24)])
def test_temperature_roundtrip(c,f):
    assert units.to_display(c, 'F') == pytest.approx(f)
    assert units.to_celsius(f, 'F') == pytest.approx(c)


def test_unknown_temperature_is_not_converted_to_number():
    for value in ('ERROR','WAITING','PAUSED','DISABLED'):
        assert units.format_reading(value, 'F') == value
    assert units.format_reading(None, 'F') == '-- °F'
    assert units.format_reading(float('nan'), 'F') == '-- °F'
    assert units.format_reading(0,'F') == '32.00 °F'
    assert units.convert_text('Current 30°C. Limit 35°C.', 'F') == 'Current 86°F. Limit 95°F.'


def test_desktop_fahrenheit_limit_form_stores_celsius():
    from local_controls import LocalControls
    from types import SimpleNamespace
    class Value:
        def __init__(self, value): self.value = value
        def get(self): return self.value
    window = SimpleNamespace(temperature_unit='F', limit_vars={
        'tempLimit': Value('86'), 'tempCriticalLimit': Value('95')})
    LocalControls.save_limits(window)
    assert config.TEMP_LIMIT_C == pytest.approx(30)
    assert config.TEMP_OVERHEAT_LIMIT_C == pytest.approx(35)


def test_shutdown_rejects_web_actuation():
    client=flask_app.test_client()
    client.post('/api/auth/login',json={'pin':config.ADMIN_PIN})
    config.begin_shutdown()
    response=client.post('/api/water-control',json={'action':'both_start'})
    assert response.status_code == 503




def test_output_watchdog_retries_without_latching_application_shutdown():
    import serial
    port=connected()
    with pytest.raises(serial.SerialException, match='HOST_TIMEOUT'):
        MCUCommunicator()._parse_output_message('MegaOutput,SYSTEM:OFF,SYSTEMCTRL:V1,REASON:HOST_TIMEOUT')
    assert not config.shutting_down
    assert 'MegaOutput,PUMP:STOP_ALL\n' in port.messages
    assert not water_control.get_status()['pumpActive']
    assert water_control.get_status()['manualOverride']
