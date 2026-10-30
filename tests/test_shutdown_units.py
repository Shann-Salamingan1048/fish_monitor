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
        assert 'NANOOUTPUT,SYSTEM:OFF\n' in port.messages[-1]
        assert 'NANOINPUT,SYSTEM:OFF\n' in port.messages[-1]
    counts = (len(output.messages), len(input_port.messages))
    packet(ph='3', temp='40')
    assert not config.send_to_output_serial_ports('NANOOUTPUT,PUMP:BOTH_ON\n')
    assert not MCUCommunicator()._send_serial_message(output, 'NANOOUTPUT,SYSTEM:ON\n')
    assert not config.send_to_input_serial_ports('NANOINPUT,SYSTEM:ON\n')
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
    sender = threading.Thread(target=config.send_to_output_serial_ports, args=('NANOOUTPUT,PUMP:BOTH_ON\n',))
    sender.start(); assert started.wait(2)
    shutdown = threading.Thread(target=config.begin_shutdown);shutdown.start()
    release.set();sender.join(2);shutdown.join(2)
    assert not sender.is_alive() and not shutdown.is_alive()
    assert 'BOTH_ON' in port.messages[0] and 'SYSTEM:OFF' in port.messages[-1]
    assert not config.send_to_output_serial_ports('NANOOUTPUT,FANMODE:ON\n')


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


@pytest.mark.parametrize('board', ['input', 'output'])
def test_reader_arms_correct_board_before_restoring_controls_and_sends_heartbeat(monkeypatch, board):
    import mcu_comm
    controller=MCUCommunicator()
    controller.running=True
    class Reader(FakeSerial):
        def __init__(self):
            super().__init__()
            self.lines=[b'NANOINPUTSTATUS,READY:1,INPUTFW:ASYNC_V1,SYSTEM:OFF\n'] if board=='input' else [b'NANOOUTPUT,READY:1,FEEDER:V3,FANCTRL:V1,SYSTEMCTRL:V1,SYSTEM:OFF\n']
        @property
        def in_waiting(self):
            if not self.lines: controller.running=False
            return len(self.lines)
        def readline(self): return self.lines.pop(0)
        def close(self): self.is_open=False
    port=Reader()
    monkeypatch.setattr(mcu_comm.serial,'Serial',lambda *a,**kw:port)
    monkeypatch.setattr(mcu_comm.time,'sleep',lambda _:None)
    controller.read_serial_data('/dev/test')
    prefix='NANOINPUT' if board=='input' else 'NANOOUTPUT'
    assert port.messages[0]==prefix+',SYSTEM:ON\n'
    assert prefix+',HEARTBEAT:1\n' in port.messages
    assert port.is_open is False


def test_output_watchdog_status_latches_host_off():
    port=connected()
    MCUCommunicator()._parse_output_message('NANOOUTPUT,SYSTEM:OFF,SYSTEMCTRL:V1,REASON:HOST_TIMEOUT')
    assert config.shutting_down
    assert 'SYSTEM:OFF' in port.messages[-1]
    assert not water_control.get_status()['pumpActive']
