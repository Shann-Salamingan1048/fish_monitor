from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from types import SimpleNamespace
import threading
import pytest
import config
import water_schedule as ws
import water_control
import feeding_control
import calibration
import sensor_control
from mcu_comm import MCUCommunicator
from flask_server import flask_app
from test_system import isolated,connected

NOW=datetime(2026,11,4,8,0,tzinfo=ZoneInfo('Asia/Manila'))


def row(day=7,**kwargs):
    return ws.save(dict(day=day,time='08:00',drainSeconds=30,refillSeconds=120,**kwargs))


def ready(monkeypatch):
    port=connected();ws.handle_status({'WATERCHANGE':'V1'})
    monkeypatch.setattr(ws,'_started_at',NOW.timestamp()-120)
    MCUCommunicator()._parse_sensor_message(
        f'MegaInput,Temp:27,pH:7,pHADC:850,Turbidity:Clear,TurbidityADC:900,FloatLow:1,FloatNormal:1,FloatUpper:0,SENSORCFG:{sensor_control._revision}')
    assert ws._block_reason() is None
    return port


def test_weekly_daily_edit_and_reload_preserve_feeding():
    feed=config.create_feeding_schedule(0,'07:00',True,2)
    daily=row();weekly=row(2)
    assert len(ws.schedules())==2
    ws.save({'enabled':False},weekly['id'])
    ws.initialize();assert ws.schedules()[0]['enabled'] is False
    assert config.get_feeding_schedule(feed['id'])['cycles']==2
    ws.delete(weekly['id']);assert len(ws.schedules())==1
    assert ws.schedules()[0]['id']==daily['id']


@pytest.mark.parametrize('change',[{'day':8},{'time':'24:00'},{'drainSeconds':0},{'drainSeconds':601},
                                  {'refillSeconds':1801},{'refillSeconds':True},{'drainSeconds':1.5},
                                  {'enabled':None},{'random':'x'}])
def test_invalid_schedule_rejected(change):
    with pytest.raises(ValueError):ws.save(dict(day=7,time='08:00',drainSeconds=30,refillSeconds=120,**{})|change)
    assert not ws.schedules()


def test_due_claim_atomic_one_command_and_no_feeding_overlap(monkeypatch):
    port=ready(monkeypatch);row()
    threads=[threading.Thread(target=ws.process_due,args=(NOW,)) for _ in range(8)]
    for t in threads:t.start()
    for t in threads:t.join()
    messages=[m for m in port.messages if 'PUMP:BOTH_ON' in m]
    assert messages == ['MegaOutput,PUMP:BOTH_ON\n']
    with pytest.raises(ValueError,match='water pumping'):feeding_control.request_feed(1)
    assert water_control.is_pump_active()  # Reservation covers serial acknowledgement delay.
    reader = MCUCommunicator()
    reader._parse_output_message('MegaOutput,PUMP_EXTRACT:ON,PUMP_INSERT:ON,REASON:COMMAND')
    assert ws.schedules()[0]['lastStatus'] == 'exchanging'
    assert ws._active['durationSeconds'] == 30
    assert ws.service_exchange()
    ws._active['monotonic'] -= 30
    assert not ws.service_exchange()
    assert port.messages[-1] == 'MegaOutput,PUMP:STOP_ALL\n'
    assert ws.busy()
    reader._parse_output_message('MegaOutput,PUMP_EXTRACT:OFF,PUMP_INSERT:OFF,REASON:COMMAND')
    assert not ws.busy() and ws.response()['last']['status']=='complete'
    assert port.messages[-1] == 'MegaOutput,WATERMODE:AUTO\n'
    assert not ws.process_due(NOW+timedelta(seconds=10))


def test_one_minute_shared_timer_and_read_only_countdown(monkeypatch):
    port = ready(monkeypatch)
    ws.save(dict(day=7, time='08:00', durationSeconds=60))
    clock = [100.0]
    monkeypatch.setattr(ws.time, 'monotonic', lambda: clock[0])
    ws.process_due(NOW)
    reader = MCUCommunicator()
    reader._parse_output_message('MegaOutput,PUMP_EXTRACT:ON,PUMP_INSERT:ON,REASON:COMMAND')
    assert ws.response()['active']['remainingSeconds'] == 60
    clock[0] = 159.01
    for _ in range(5):
        active = ws.response()['active']
        assert active['remainingSeconds'] == 1
        assert 'monotonic' not in active
    assert ws.service_exchange()
    assert not any('STOP_ALL' in m for m in port.messages)
    clock[0] = 160.0
    assert not ws.service_exchange()
    assert ws.response()['active']['remainingSeconds'] == 0
    clock[0] = 170.0
    assert not ws.service_exchange()
    assert ws.response()['active']['remainingSeconds'] == 0
    assert sum('PUMP:BOTH_ON' in m for m in port.messages) == 1
    assert sum('PUMP:STOP_ALL' in m for m in port.messages) == 1
    reader._parse_output_message('MegaOutput,PUMP_EXTRACT:OFF,PUMP_INSERT:OFF,REASON:COMMAND')
    assert ws.response()['active'] is None
    assert ws.response()['last']['status'] == 'complete'


@pytest.mark.parametrize('block',['old_firmware','disconnected','manual','pumps','feeding','calibration','float_disabled','stale','not_normal','off'])
def test_start_guards_skip_without_replay(monkeypatch,block):
    port=ready(monkeypatch);row()
    if block=='old_firmware':ws._ready=False
    if block=='disconnected':config.output_serial_ports.clear()
    if block=='manual':water_control._state['manualOverride']=True
    if block=='pumps':water_control._state['extractionOn']=True
    if block=='feeding':feeding_control.request_feed(5)
    if block=='calibration':calibration.set_active(True)
    if block=='float_disabled':sensor_control._enabled['float_upper']=False
    if block=='stale':config.last_sensor_received_at=0
    if block=='not_normal':sensor_control._raw['float_normal']=False
    if block=='off':config.shutting_down=True
    ws.process_due(NOW)
    assert not any('PUMP:BOTH_ON' in m for m in port.messages)
    if block!='off':assert ws.response()['last']['status']=='skipped'


def test_overlapping_daily_and_weekly_only_start_one(monkeypatch):
    port=ready(monkeypatch);row();row(2)
    ws.process_due(NOW)
    assert len([m for m in port.messages if 'PUMP:BOTH_ON' in m])==1
    assert {r['lastStatus'] for r in ws.schedules()}=={'sent','skipped'}


def test_restart_disconnect_and_delivery_failure_do_not_replay(monkeypatch):
    port=ready(monkeypatch);row();ws.process_due(NOW)
    ws.disconnect();assert not ws.busy() and ws.response()['last']['status']=='interrupted'
    ws.initialize();ws.handle_status({'WATERCHANGE':'V1'})
    ws.process_due(NOW+timedelta(seconds=15))
    assert len([m for m in port.messages if 'PUMP:BOTH_ON' in m])==1


def test_failed_write_marks_uncertain_and_does_not_retry(monkeypatch):
    ready(monkeypatch);row()
    monkeypatch.setattr(config,'send_to_identified_output_serial_ports',lambda _:False)
    ws.process_due(NOW)
    assert ws.busy() and not ws.service_exchange()
    ws.pump_feedback({'REASON':'COMMAND','PUMP_EXTRACT':'OFF','PUMP_INSERT':'OFF'})
    assert ws.busy() and not ws.service_exchange()  # No start reply, so keep watchdog inhibited.
    ws.disconnect()
    assert not ws.busy() and ws.response()['last']['status']=='interrupted'
    assert not ws.process_due(NOW+timedelta(seconds=10))


def test_missed_and_late_runs_are_not_caught_up(monkeypatch):
    port=ready(monkeypatch);row()
    monkeypatch.setattr(ws,'_started_at',NOW.timestamp()+5)
    ws.process_due(NOW+timedelta(seconds=10))
    assert ws.response()['last']['status']=='skipped'
    assert not ws.process_due(NOW+timedelta(days=1,seconds=61))
    assert not any('PUMP:BOTH_ON' in m for m in port.messages)


def test_stop_and_missing_completion_leave_manual_pause(monkeypatch):
    port=ready(monkeypatch);row();ws.process_due(NOW)
    ws.pump_feedback({'REASON':'COMMAND','PUMP_EXTRACT':'ON','PUMP_INSERT':'ON'})
    water_control.manual_action('stop_all')
    assert port.messages[-1] == 'MegaOutput,PUMP:STOP_ALL\n'
    assert not ws.service_exchange()
    ws.pump_feedback({'REASON':'COMMAND','PUMP_EXTRACT':'OFF','PUMP_INSERT':'OFF'})
    assert not ws.busy() and ws.response()['last']['status']=='cancelled'
    assert not any('WATERMODE:AUTO' in m for m in port.messages)


@pytest.mark.parametrize('fault', ['inconsistent', 'unknown', 'disabled', 'stale', 'calibration', 'partial', 'unconfirmed'])
def test_pi_stops_both_and_inhibits_heartbeat_on_fault(monkeypatch, fault):
    port = ready(monkeypatch)
    row()
    ws.process_due(NOW)
    if fault != 'unconfirmed':
        ws.pump_feedback({'REASON':'COMMAND','PUMP_EXTRACT':'ON','PUMP_INSERT':'ON'})
    if fault == 'inconsistent': sensor_control._raw['float_low'] = False
    if fault == 'unknown': sensor_control._raw['float_normal'] = None
    if fault == 'disabled': sensor_control._enabled['float_upper'] = False
    if fault == 'stale': config.last_sensor_received_at = 0
    if fault == 'calibration': monkeypatch.setattr(calibration, 'active', lambda: True)
    if fault == 'partial':
        ws.pump_feedback({'REASON':'LOCAL_FAILSAFE','PUMP_EXTRACT':'ON','PUMP_INSERT':'OFF'})
    if fault == 'unconfirmed':
        pair, sent_at = ws._active['pending'][0]
        ws._active['pending'][0] = (pair, sent_at - 4)
    assert not ws.service_exchange()
    assert port.messages[-1] == 'MegaOutput,PUMP:STOP_ALL\n'
    ws.pump_feedback({'REASON':'COMMAND','PUMP_EXTRACT':'OFF','PUMP_INSERT':'OFF'})
    if fault == 'unconfirmed':
        assert ws.busy() and not ws.service_exchange()
        ws.disconnect()
    assert ws.response()['last']['status'] in ('failed', 'interrupted')
    assert not any('WATERMODE:AUTO' in m for m in port.messages)


def set_floats(values):
    for key, value in zip(sensor_control.FLOAT_KEYS, values):
        sensor_control._raw[key] = value
    config.last_sensor_received_at = ws.time.time()


def pump_ack(extract, insert):
    MCUCommunicator()._parse_output_message(
        f'MegaOutput,PUMP_EXTRACT:{extract},PUMP_INSERT:{insert},REASON:COMMAND')


@pytest.mark.parametrize('levels,command,pair', [
    ((True,False,False), 'INSERT_ON', ('OFF','ON')),
    ((False,False,False), 'INSERT_ON', ('OFF','ON')),
    ((True,True,True), 'EXTRACT_ON', ('ON','OFF')),
])
def test_level_correction_recovers_without_cancelling_or_extending_timer(monkeypatch, levels, command, pair):
    port = ready(monkeypatch)
    ws.save(dict(day=7,time='08:00',durationSeconds=60))
    clock = [100.0]
    monkeypatch.setattr(ws.time,'monotonic',lambda:clock[0])
    ws.process_due(NOW); pump_ack('ON','ON')
    clock[0] = 110.0
    set_floats(levels)
    assert ws.service_exchange()
    assert port.messages[-1] == f'MegaOutput,PUMP:{command}\n'
    pump_ack(*pair)
    assert 'only' in ws.response()['active']['controlText']
    messages = len(port.messages)
    for _ in range(20): assert ws.service_exchange()
    assert len(port.messages) == messages  # No command flood while correcting.
    set_floats((True,True,False))
    clock[0] = 111.0; assert ws.service_exchange()
    clock[0] = 112.9; assert ws.service_exchange()
    assert len(port.messages) == messages
    # A normal-float wobble restarts recovery confirmation, not the run.
    set_floats(levels); assert ws.service_exchange()
    set_floats((True,True,False))
    clock[0] = 113.0; assert ws.service_exchange()
    clock[0] = 115.0; assert ws.service_exchange()
    assert port.messages[-1] == 'MegaOutput,PUMP:BOTH_ON\n'
    pump_ack('ON','ON')
    assert ws.response()['active']['remainingSeconds'] == 45
    assert ws.response()['active']['controlText'] == 'Both pumps running'
    assert not any('STOP_ALL' in m for m in port.messages)
    clock[0] = 160.0; assert not ws.service_exchange()
    pump_ack('OFF','OFF')
    assert ws.response()['last']['status'] == 'complete'
    assert not ws.busy()


def test_opposite_level_limit_overrides_recovery_delay(monkeypatch):
    port = ready(monkeypatch); row(); ws.process_due(NOW); pump_ack('ON','ON')
    set_floats((True,True,True)); assert ws.service_exchange(); pump_ack('ON','OFF')
    set_floats((True,True,False)); assert ws.service_exchange()
    set_floats((False,False,False)); assert ws.service_exchange()
    assert port.messages[-1] == 'MegaOutput,PUMP:INSERT_ON\n'
    pump_ack('OFF','ON')
    assert ws.busy() and ws._active['status'] == 'exchanging'


def test_safety_adjustment_can_follow_start_before_its_ack(monkeypatch):
    port = ready(monkeypatch); row(); ws.process_due(NOW)
    set_floats((True,True,True)); assert ws.service_exchange()
    assert port.messages[-1] == 'MegaOutput,PUMP:EXTRACT_ON\n'
    pump_ack('ON','ON'); pump_ack('ON','OFF')
    assert not ws._active['pending']
    assert ws.service_exchange()
    ws.stop(); pump_ack('OFF','OFF')
    assert ws.response()['last']['status'] == 'cancelled'


def test_missing_adjustment_ack_stops_and_cannot_be_cleared_by_stale_reply(monkeypatch):
    port = ready(monkeypatch); row(); ws.process_due(NOW); pump_ack('ON','ON')
    set_floats((True,False,False)); assert ws.service_exchange()
    pair, sent_at = ws._active['pending'][0]
    ws._active['pending'][0] = (pair, sent_at - 4)
    assert not ws.service_exchange()
    assert port.messages[-1] == 'MegaOutput,PUMP:STOP_ALL\n'
    pump_ack('OFF','OFF')  # Missing the adjustment reply: retain the reservation.
    assert ws.busy() and not ws.service_exchange()
    ws.disconnect(); assert ws.response()['last']['status'] == 'interrupted'


def test_shared_deadline_ends_run_even_during_level_correction(monkeypatch):
    port = ready(monkeypatch); row(); ws.process_due(NOW); pump_ack('ON','ON')
    set_floats((True,False,False)); assert ws.service_exchange(); pump_ack('OFF','ON')
    ws._active['monotonic'] -= 30
    assert not ws.service_exchange()
    pump_ack('OFF','OFF')
    assert ws.response()['last']['status'] == 'complete'
    assert port.messages[-1] == 'MegaOutput,WATERMODE:AUTO\n'


def test_shared_duration_and_legacy_fields_survive_restarts():
    current = ws.save(dict(day=7, time='09:00', durationSeconds=45))
    assert current['durationSeconds'] == current['drainSeconds'] == current['refillSeconds'] == 45
    legacy = row()
    assert legacy['durationSeconds'] == 30
    ws.initialize()
    assert next(r for r in ws.schedules() if r['id'] == current['id'])['durationSeconds'] == 45
    with pytest.raises(ValueError):
        ws.save(dict(day=7,time='10:00',durationSeconds=20,drainSeconds=30))
    with pytest.raises(ValueError):
        ws.save(dict(day=7,time='10:00',durationSeconds=601))


def test_manual_commands_cannot_replace_an_active_exchange(monkeypatch):
    ready(monkeypatch)
    row()
    ws.process_due(NOW)
    with pytest.raises(ValueError):
        water_control.manual_action('both_start')
    with pytest.raises(ValueError):
        water_control.manual_action('auto_resume')
    ws.stop()
    with pytest.raises(ValueError):
        feeding_control.request_feed(1)


def test_late_start_reply_cannot_cancel_a_stop(monkeypatch):
    port = ready(monkeypatch)
    row()
    ws.process_due(NOW)
    ws.stop()
    ws.pump_feedback({'REASON':'COMMAND','PUMP_EXTRACT':'ON','PUMP_INSERT':'ON'})
    assert not ws.service_exchange()
    assert ws._active['status'] == 'stopping'
    ws.pump_feedback({'REASON':'COMMAND','PUMP_EXTRACT':'OFF','PUMP_INSERT':'OFF'})
    assert ws.response()['last']['status'] == 'cancelled'


def test_schedule_edit_does_not_erase_today_claim(monkeypatch):
    ready(monkeypatch);s=row();ws.process_due(NOW)
    ws.save({'time':'08:01'},s['id'])
    assert ws.schedules()[0]['lastTriggeredDate']==NOW.date().isoformat()


def test_admin_routes_and_csrf():
    client=flask_app.test_client();body=dict(day=7,time='08:00',drainSeconds=30,refillSeconds=120)
    assert client.get('/api/water-schedules').status_code==200
    assert client.post('/api/water-schedules',json=body).status_code==403
    assert client.post('/api/water-change/stop').status_code==403
    client.post('/api/auth/login',json={'pin':config.ADMIN_PIN})
    assert client.post('/api/water-schedules',json=body,headers={'Origin':'https://evil.example'}).status_code==403
    result=client.post('/api/water-schedules',json=body);assert result.status_code==200
    schedule=result.json['schedules'][0]
    assert client.put('/api/water-schedules/'+str(schedule['id']),json={'enabled':False}).status_code==200
    assert client.delete('/api/water-schedules/'+str(schedule['id'])).json['schedules']==[]
    assert 'Water-change schedule' in client.get('/water-changes').text


def test_gui_save_calls_shared_schedule_service():
    from water_schedule_gui import WaterScheduleWindow
    var=lambda value:SimpleNamespace(get=lambda:value)
    panel=SimpleNamespace(day=var(config.FEEDING_DAY_NAMES[7]),hour=var('09'),minute=var('10'),duration=var('20'),editing=None,
                          run=lambda callback,*args:callback())
    WaterScheduleWindow.save(panel)
    assert ws.schedules()[0]['time']=='09:10'
    assert ws.schedules()[0]['drainSeconds']==20


def test_next_run_uses_water_grace_not_feeding_grace(monkeypatch):
    ready(monkeypatch);row()
    at=ws.next_run(ws.schedules(),NOW+timedelta(seconds=61))
    assert at['dateTime']==(NOW+timedelta(days=1)).isoformat()
