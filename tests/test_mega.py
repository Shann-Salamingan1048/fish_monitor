from types import SimpleNamespace
import pytest
import config
import mcu_comm
import mega_control
import serial_discovery
import water_control
import feeding_control
import calibration
from test_system import isolated, FakeSerial


def mega(monkeypatch):
    mega_control.reset()
    return mcu_comm.MCUCommunicator()


def test_one_handle_registered_for_both_roles_and_local_policy_sent(monkeypatch):
    controller=mega(monkeypatch); port=FakeSerial()
    controller._identify_mega(port)
    assert config.output_serial_ports == [port] == config.input_serial_ports
    assert port.messages[0] == 'MegaOutput,SYSTEM:ON\n'
    assert any('MegaConfig,BEGIN:' in s and 'MegaConfig,COMMIT:' in s for s in port.messages)
    assert 'MegaOutput,WATERMODE:AUTO\n' in port.messages
    port.messages.clear()
    assert not controller._identify_mega(port)
    assert port.messages == []


def test_recovery_remains_manual_and_does_not_replay_actuators(monkeypatch):
    controller=mega(monkeypatch); controller._seen_roles.add('mega')
    port=FakeSerial();controller._identify_mega(port)
    assert port.messages[0] == 'MegaOutput,SYSTEM:OFF\n'
    assert 'MegaOutput,WATERMODE:MANUAL\n' in port.messages
    assert not any('BOTH_ON' in s or 'FEED:CUSTOM' in s or 'CAMERA:SET' in s for s in port.messages)


def test_shutdown_blocks_mega_reactivation(monkeypatch):
    controller=mega(monkeypatch); config.begin_shutdown()
    with pytest.raises(mcu_comm.serial.SerialException):controller._identify_mega(FakeSerial())
    assert not config.output_serial_ports and not config.input_serial_ports


def test_one_reader_is_started(monkeypatch):
    controller=mega(monkeypatch); threads=[]
    class Thread:
        def __init__(self,**kwargs):threads.append(kwargs)
        def start(self):pass
    monkeypatch.setattr(mcu_comm.threading,'Thread',Thread)
    controller.start_reading()
    assert len(threads)==1 and threads[0]['args']==('auto-mega',)


def test_genuine_and_clone_mega_follow_usb_location(monkeypatch):
    devices=[SimpleNamespace(device='/dev/ttyACM0',location='1-1.2',vid=0x2341,pid=0x42)]
    monkeypatch.setattr(serial_discovery.list_ports,'comports',lambda:devices)
    discovery=serial_discovery.MegaPortDiscovery()
    assert discovery.acquire('auto-mega')=='/dev/ttyACM0'
    discovery.release('auto-mega');devices[0].device='/dev/ttyACM2'
    assert discovery.acquire('auto-mega')=='/dev/ttyACM2'


def test_policy_ack_prevents_repeat_and_change_triggers_sync(monkeypatch):
    controller=mega(monkeypatch);port=FakeSerial();controller._identify_mega(port)
    revision,payload=mega_control.commands()
    assert all(len(line)<256 for line in payload.splitlines())
    mega_control.handle_status(f'AquaPiMega,CFG:{revision}')
    port.messages.clear();assert mega_control.sync();assert not port.messages
    monkeypatch.setattr(config,'PH_CRITICAL_LOW_LIMIT',5.5)
    assert mega_control.sync(force=True);assert 'PL:5.5' in port.messages[-1]


def test_local_state_is_displayed_without_echoing_pump_commands(monkeypatch):
    controller=mega(monkeypatch);port=FakeSerial();controller._identify_mega(port)
    port.messages.clear()
    mega_control.handle_status('AquaPiMega,WATER:auto_extract_turbidity,EXTRACT:1,INSERT:1,MANUAL:0,MODE:AUTO_QUALITY,SETTLING:1,REMAIN:-1')
    water_control.process_sensor_update('Normal','Very Cloudy','6.0')
    assert water_control.is_pump_active() and not port.messages
    water_control.manual_action('stop_all')
    assert 'MegaOutput,WATERMODE:MANUAL\n' in port.messages
    assert 'MegaOutput,PUMP:STOP_ALL\n' in port.messages
    water_control.manual_action('auto_resume')
    assert port.messages[-1]=='MegaOutput,WATERMODE:AUTO\n'


def test_periodic_identity_does_not_clear_an_active_feed(monkeypatch):
    controller=mega(monkeypatch);port=FakeSerial();controller._identify_mega(port)
    controller._parse_output_message('AquaPiMega,READY:1,FEEDER:V3,FANCTRL:V1')
    feeding_control.request_feed(3)
    mega_control.handle_status('AquaPiMega,STATUS:1,FEEDER:V3,FANCTRL:V1')
    assert not controller._identify_mega(port)
    assert feeding_control.get_status()['busy']


def test_no_fallback_commands_to_unverified_usb_in_mega_mode(monkeypatch):
    mega(monkeypatch);port=FakeSerial();config.serial_ports.append(port)
    assert not config.send_to_output_serial_ports('MegaOutput,PUMP:BOTH_ON\n')
    assert not port.messages


def test_reader_accepts_boot_and_both_streams_with_one_connection(monkeypatch):
    controller=mega(monkeypatch);controller.running=True
    seen=[]
    class Port(FakeSerial):
        count=0
        def close(self):self.is_open=False
        @property
        def in_waiting(self):
            if self.count == 3:
                seen.append((list(config.input_serial_ports),list(config.output_serial_ports)))
                raise mcu_comm.serial.SerialException('unplugged')
            return 1
        def readline(self):
            lines=[b'AquaPiMega,READY:1,PROTOCOL:1,FEEDER:V3,FANCTRL:V1,SYSTEM:OFF\n',
                   b'MegaInputStatus,BOOT:1,SYSTEM:OFF\n',
                   b'MegaOutput,SYSTEM:ON\n']
            line=lines[self.count];self.count+=1;return line
    port=Port();monkeypatch.setattr(mcu_comm.serial,'Serial',lambda *a,**k:port)
    monkeypatch.setattr(mcu_comm.time,'sleep',lambda _:None)
    controller.read_serial_data('/dev/ttyACM0')
    assert seen==[([port],[port])]
    assert 'MegaOutput,HEARTBEAT:1\n' in port.messages
    assert not any('MegaInput,HEARTBEAT:' in s for s in port.messages)
    assert not config.input_serial_ports and not config.output_serial_ports
    assert water_control.get_status()['manualOverride']


def test_custom_calibration_is_sent_with_categories_not_display_labels(monkeypatch):
    mega(monkeypatch)
    calibration.set_active(True)
    calibration.save({'ph':{'adc686':900,'adc918':800},'turbidity':[
        {'min':0,'max':500,'label':'Crystal','category':'Very Clear'},
        {'min':501,'max':1023,'label':'Dirty','category':'Very Cloudy'}]})
    revision,payload=mega_control.commands()
    assert 'A6:900.0000,A9:800.0000' in payload
    assert 'MAINT:1,ROWS:2' in payload
    assert 'MIN:501,MAX:1023,CAT:0' in payload
    assert 'Dirty' not in payload
