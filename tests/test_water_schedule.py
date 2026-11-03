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
    messages=[m for m in port.messages if 'WATERCHANGE:START' in m]
    assert len(messages)==1 and 'DRAIN:30,REFILL:120' in messages[0]
    with pytest.raises(ValueError,match='water pumping'):feeding_control.request_feed(1)
    assert water_control.is_pump_active()  # Reservation covers serial acknowledgement delay.
    job=ws.response()['active']['id']
    ws.handle_status({'WC':'DRAINING','WCJOB':str(job)})
    assert ws.schedules()[0]['lastStatus']=='draining'
    ws.handle_status({'WC':'COMPLETE','WCJOB':str(job),'WCREASON':'NORMAL_LEVEL'})
    assert not ws.busy() and ws.response()['last']['status']=='complete'
    assert not ws.process_due(NOW+timedelta(seconds=10))


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
    assert not any('WATERCHANGE:START' in m for m in port.messages)
    if block!='off':assert ws.response()['last']['status']=='skipped'


def test_overlapping_daily_and_weekly_only_start_one(monkeypatch):
    port=ready(monkeypatch);row();row(2)
    ws.process_due(NOW)
    assert len([m for m in port.messages if 'WATERCHANGE:START' in m])==1
    assert {r['lastStatus'] for r in ws.schedules()}=={'sent','skipped'}


def test_restart_disconnect_and_delivery_failure_do_not_replay(monkeypatch):
    port=ready(monkeypatch);row();ws.process_due(NOW)
    ws.disconnect();assert not ws.busy() and ws.response()['last']['status']=='interrupted'
    ws.initialize();ws.handle_status({'WATERCHANGE':'V1'})
    ws.process_due(NOW+timedelta(seconds=15))
    assert len([m for m in port.messages if 'WATERCHANGE:START' in m])==1


def test_failed_write_marks_uncertain_and_does_not_retry(monkeypatch):
    ready(monkeypatch);row()
    monkeypatch.setattr(config,'send_to_identified_output_serial_ports',lambda _:False)
    ws.process_due(NOW);assert not ws.busy() and ws.response()['last']['status']=='interrupted'
    assert not ws.process_due(NOW+timedelta(seconds=10))


def test_missed_and_late_runs_are_not_caught_up(monkeypatch):
    port=ready(monkeypatch);row()
    monkeypatch.setattr(ws,'_started_at',NOW.timestamp()+5)
    ws.process_due(NOW+timedelta(seconds=10))
    assert ws.response()['last']['status']=='skipped'
    assert not ws.process_due(NOW+timedelta(days=1,seconds=61))
    assert not any('WATERCHANGE:START' in m for m in port.messages)


def test_stop_and_missing_completion_leave_manual_pause(monkeypatch):
    port=ready(monkeypatch);row();ws.process_due(NOW)
    ws.stop();assert water_control.get_status()['manualOverride']
    assert 'MegaOutput,WATERCHANGE:STOP\n' in port.messages
    job=ws.response()['active']['id']
    ws.handle_status({'WC':'CANCELLED','WCJOB':str(job)})
    assert not ws.busy() and ws.response()['last']['status']=='cancelled'


def test_deadline_stops_unknown_run(monkeypatch):
    port=ready(monkeypatch);row();ws.process_due(NOW)
    ws._active['monotonic']-=1000;ws.process_due(NOW+timedelta(seconds=1))
    assert not ws.busy() and ws.response()['last']['status']=='interrupted'
    assert water_control.get_status()['manualOverride']
    assert 'MegaOutput,WATERCHANGE:STOP\n' in port.messages


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
    assert 'Water-change schedule' in client.get('/controls').text


def test_gui_save_calls_shared_schedule_service():
    from water_schedule_gui import WaterScheduleWindow
    var=lambda value:SimpleNamespace(get=lambda:value)
    panel=SimpleNamespace(day=var(config.FEEDING_DAY_NAMES[7]),at=var('09:10'),drain=var('20'),refill=var('60'),editing=None,
                          run=lambda callback,*args:callback())
    WaterScheduleWindow.save(panel)
    assert ws.schedules()[0]['time']=='09:10'
    assert ws.schedules()[0]['drainSeconds']==20


def test_next_run_uses_water_grace_not_feeding_grace(monkeypatch):
    ready(monkeypatch);row()
    at=ws.next_run(ws.schedules(),NOW+timedelta(seconds=61))
    assert at['dateTime']==(NOW+timedelta(days=1)).isoformat()
