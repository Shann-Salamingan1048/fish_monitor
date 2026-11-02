"""Integration tests: actual packet parser, controller, SQLite and Flask routes."""
import copy
import json
import math
import pytest
import config
import calibration
import sensor_control
import water_control as water
from mcu_comm import MCUCommunicator
from predictions import build_sensor_alerts, get_prediction_numeric, get_sensor_numeric
from flask_server import flask_app
from test_system import isolated, connected

PROFILE = {'ph': {'adc686': 900, 'adc918': 800}, 'turbidity': None}


def packet(ph='6.86', pa='900', ta='900', clarity='Clear', level='Normal', revision=None):
    rev = sensor_control._revision if revision is None else revision
    return MCUCommunicator()._parse_sensor_message(
        f'MegaInput,Temp:27,pH:{ph},pHADC:{pa},Turbidity:{clarity},TurbidityADC:{ta},FloatSensor:{level},SENSORCFG:{rev}')


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(water.time, 'monotonic', lambda: now[0])
    return now


def test_calibration_persists_and_syncs_mega_ram_without_actuator_commands(clock):
    port = connected()
    calibration.set_active(True)
    calibration.save(PROFILE)
    assert any('MegaConfig,COMMIT:' in m for m in port.messages)
    assert not any(',PUMP:' in m or ',FEED:' in m for m in port.messages)
    assert packet(pa='900')[0]['ph'] == '6.86'
    calibration.reset_readings()
    assert packet(pa='800',ph='9.18')[0]['ph'] == '9.18'
    calibration.initialize()
    assert calibration.status()['profile'] == PROFILE
    assert calibration.active()
    assert packet(pa='850',ph='8.02')[0]['ph'] == '8.02'
    assert not water.is_pump_active()


def test_host_preserves_mega_filtered_ph_on_repeated_packets(clock):
    calibration.set_active(True); calibration.save(PROFILE)
    packet(pa='900')
    for t in (100.5, 101, 101.5, 102):
        clock[0] = t; snapshot, _ = packet(pa='800',ph='7.32')
    assert snapshot['ph'] == '7.32'  # Reported by Mega; the Pi must not apply another filter.
    assert calibration.status()['live']['phADC'] == 800


@pytest.mark.parametrize('bad', ['NaN', 'inf', '-1', '1024', 'WAITING', 'PAUSED', ''])
def test_missing_adc_is_not_exposed_as_a_calibration_measurement(bad):
    calibration.set_active(True); calibration.save(PROFILE)
    assert packet()[0]['ph'] == '6.86'
    assert packet(pa=bad, ph='WAITING')[0]['ph'] == 'WAITING'
    assert calibration.status()['live']['phADC'] is None
    assert not water.is_pump_active()


def test_reconnect_and_old_revision_do_not_reuse_adc(clock):
    packet(pa='901')
    packet(pa='700', revision=sensor_control._revision - 1)
    assert calibration.status()['live']['phADC'] == 901
    clock[0] += 4
    assert calibration.status()['live']['phADC'] is None
    sensor_control.input_reconnected()
    assert calibration.status()['live']['ageSeconds'] is None


def test_ranges_arbitrary_label_controls_alerts_not_text():
    profile = {'ph': None, 'turbidity': [dict(min=0,max=500,label='Clear 123 <b>',category='Very Cloudy'),
                                         dict(min=501,max=1023,label='Cloudy custom',category='Clear')]}
    calibration.set_active(True); calibration.save(profile); calibration.set_active(False)
    connected()
    snapshot, alerts = packet(ta='500', clarity='Clear')
    assert snapshot['turbidity'] == 'Clear 123 <b>'
    assert not water.is_pump_active()  # Only a hardware status frame updates pumps.
    alert = next(a for a in alerts if a['sensor'] == 'Turbidity')
    assert alert['severity'] == 'critical' and 'NTU' not in alert['message']
    assert get_prediction_numeric('turbidity', snapshot['turbidity']) is None
    snapshot, _ = packet(ta='501', clarity='Very Cloudy')
    assert snapshot['turbidity'] == 'Cloudy custom'
    assert not water.is_pump_active()
    assert get_sensor_numeric('turbidity', snapshot['turbidity']) == 0


@pytest.mark.parametrize('change', ['overlap','gap','end','reverse','fraction','duplicate','reserved','category','bool','nonfinite'])
def test_invalid_profiles_rejected_atomically(change):
    calibration.set_active(True); calibration.save(PROFILE)
    candidate = dict(ph=copy.deepcopy(PROFILE['ph']), turbidity=copy.deepcopy(calibration.DEFAULT_RANGES))
    rows = candidate['turbidity']
    if change == 'overlap': rows[1]['min'] = 170
    if change == 'gap': rows[1]['min'] = 172
    if change == 'end': rows[-1]['max'] = 1022
    if change == 'reverse': candidate['ph'] = {'adc686': 800, 'adc918': 900}
    if change == 'fraction': rows[0]['max'] = 170.5
    if change == 'duplicate': rows[1]['label'] = rows[0]['label']
    if change == 'reserved': rows[0]['label'] = 'WAITING'
    if change == 'category': rows[0]['category'] = 'safe-ish'
    if change == 'bool': candidate['ph']['adc686'] = True
    if change == 'nonfinite': candidate['ph']['adc686'] = math.nan
    before = config.get_db_value('sensor_calibration')
    with pytest.raises(ValueError): calibration.save(candidate)
    assert config.get_db_value('sensor_calibration') == before
    assert calibration.status()['profile'] == PROFILE


def test_disabled_feature_blocks_page_api_but_keeps_saved_calibration(monkeypatch):
    calibration.set_active(True); calibration.save(PROFILE); calibration.set_active(False)
    monkeypatch.setattr(config, 'DEVELOPER_OPTIONS', False)
    client = flask_app.test_client()
    assert client.post('/api/auth/login', json={'pin': config.ADMIN_PIN}).status_code == 200
    for method in ('get', 'put', 'post'):
        assert getattr(client, method)('/api/developer/calibration', json=PROFILE).status_code == 404
    assert client.get('/developer').status_code == 404
    assert 'Developer option' not in client.get('/').text
    assert packet()[0]['ph'] == '6.86'


def test_admin_access_mode_save_and_cross_site_denial(monkeypatch):
    monkeypatch.setattr(config, 'DEVELOPER_OPTIONS', True)
    client = flask_app.test_client()
    for method in ('get','post','put'):
        assert getattr(client,method)('/api/developer/calibration',json=PROFILE).status_code == 403
    assert client.get('/developer').status_code == 302
    assert 'Developer option' not in client.get('/').text
    client.post('/api/auth/login',json={'pin':config.ADMIN_PIN})
    assert 'Developer option' in client.get('/').text
    assert client.get('/developer').status_code == 200
    assert client.put('/api/developer/calibration',json=PROFILE).status_code == 400
    assert client.post('/api/developer/calibration',json={'active':True},headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.post('/api/developer/calibration',json={'active':True},headers={'Origin':'https://localhost'}).status_code == 200
    assert client.put('/api/developer/calibration',json=PROFILE).status_code == 200
    assert client.post('/api/developer/calibration',json={'active':False}).status_code == 200
    assert not calibration.active()










def test_default_fallback_unchanged_until_saved():
    assert packet(ph='7.42',pa='900',clarity='Cloudy')[0]['ph'] == '7.42'
    assert calibration.status()['profile'] == {'ph':None,'turbidity':None}


def test_failed_database_save_does_not_install_calibration(monkeypatch):
    import sqlite3
    calibration.set_active(True)
    def fail(): raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(config, 'db_connect', fail)
    with pytest.raises(sqlite3.OperationalError): calibration.save(PROFILE)
    assert calibration.status()['profile'] == {'ph':None, 'turbidity':None}
    with pytest.raises(sqlite3.OperationalError): calibration.set_active(False)
    assert calibration.active()


def test_default_range_boundaries_match_firmware():
    # Mega computes clarity = ADC / 1023 * 3000, then categorical cutoffs.
    rows = calibration.DEFAULT_RANGES
    for adc in range(1024):
        score = adc / 1023 * 3000
        expected = ('Very Cloudy' if score < 500 else 'Cloudy' if score < 1200 else
                    'Slightly Cloudy' if score < 2200 else 'Clear' if score < 2800 else 'Very Clear')
        assert next(row['category'] for row in rows if row['min'] <= adc <= row['max']) == expected
