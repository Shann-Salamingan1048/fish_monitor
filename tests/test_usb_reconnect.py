"""Single Mega discovery, port changes, descriptor cleanup and safe recovery."""
from types import SimpleNamespace
import pytest
import serial
import config
import mcu_comm
import water_control
import serial_discovery
from serial_discovery import MegaPortDiscovery
from test_system import isolated, FakeSerial


def adapter(path, location, vid=0x1a86, pid=0x7523):
    return SimpleNamespace(device=path, location=location, vid=vid, pid=pid)


def test_clone_follows_physical_port_not_reused_tty_number(monkeypatch):
    ports=[adapter('/dev/ttyUSB0','1-1.2')]
    monkeypatch.setattr(serial_discovery.list_ports,'comports',lambda:ports)
    d=MegaPortDiscovery()
    assert d.acquire('auto-mega')=='/dev/ttyUSB0'
    assert d.acquire('auto-mega') is None
    d.release('auto-mega');ports[:]=[]
    assert d.acquire('auto-mega') is None
    ports[:]=[adapter('/dev/ttyUSB0','1-9'),adapter('/dev/ttyUSB3','1-1.2')]
    assert d.acquire('auto-mega')=='/dev/ttyUSB3'


def test_does_not_probe_unknown_adapter_or_ignore_explicit_path(monkeypatch):
    ports=[adapter('/dev/ttyUSB2','1-3',123,456)]
    monkeypatch.setattr(serial_discovery.list_ports,'comports',lambda:ports)
    d=MegaPortDiscovery()
    assert d.acquire('auto-mega') is None
    assert d.acquire('/dev/serial/by-path/missing') is None
    assert d.acquire('/dev/ttyUSB2')=='/dev/ttyUSB2'


def test_reconnect_worker_re_resolves_path_and_releases_claim(monkeypatch):
    controller=mcu_comm.MCUCommunicator();controller.running=True
    ports=[adapter('/dev/ttyUSB0','1-2')]
    monkeypatch.setattr(serial_discovery.list_ports,'comports',lambda:ports)
    calls=[]
    def read(path):
        calls.append(path)
        if len(calls)==1: ports[:]=[adapter('/dev/ttyUSB3','1-2')]
        else: controller.running=False
    monkeypatch.setattr(controller,'read_serial_data',read)
    monkeypatch.setattr(mcu_comm.time,'sleep',lambda _:None)
    controller._read_with_reconnect('auto-mega')
    assert calls==['/dev/ttyUSB0','/dev/ttyUSB3']
    assert not controller._discovery._claimed


def test_failed_initialization_does_not_expose_either_role():
    controller=mcu_comm.MCUCommunicator();controller._seen_roles.add('mega')
    with pytest.raises(serial.SerialException):controller._identify_mega(FakeSerial(fail=True))
    assert not config.output_serial_ports and not config.input_serial_ports
    assert water_control.get_status()['manualOverride']


def test_old_firmware_identity_cannot_receive_actuator_commands(monkeypatch):
    controller=mcu_comm.MCUCommunicator();controller.running=True
    class Port(FakeSerial):
        read=False
        @property
        def in_waiting(self):
            if self.read: raise serial.SerialException('test disconnected')
            return 1
        def readline(self):self.read=True;return b'OLD_CONTROLLER,READY:1\n'
        def close(self):self.is_open=False
    port=Port()
    monkeypatch.setattr(mcu_comm.serial,'Serial',lambda *a,**k:port)
    monkeypatch.setattr(mcu_comm.time,'sleep',lambda _:None)
    controller.read_serial_data('/dev/ttyUSB0')
    assert not port.messages and not port.is_open
