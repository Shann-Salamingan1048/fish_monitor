"""Turbidity forecasts through the serial parser and shared UI alert paths."""
import pytest
import calibration
import config
import sensor_control
from flask_server import flask_app
from gui_snapshot import build_snapshot
from mcu_comm import MCUCommunicator
from predictions import build_sensor_alerts
from test_system import isolated


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(config.time, 'time', lambda: now[0])
    monkeypatch.setattr(config, 'TREND_WINDOW_SECONDS', 30.0)
    monkeypatch.setattr(config, 'PREDICTION_DELAY_SECONDS', 30.0)
    return now


def send(adc, label=None):
    if label is None:
        label = next(row['label'] for row in calibration.effective_adc_ranges()
                     if row['min'] <= adc <= row['max'])
    revision = sensor_control._revision
    _, alerts = MCUCommunicator()._parse_sensor_message(
        f'MegaInput,Temp:27,pH:7,Turbidity:{label},TurbidityADC:{adc},'
        f'FloatLow:1,FloatNormal:1,FloatUpper:0,SENSORCFG:{revision}')
    return forecasts(alerts)


def forecasts(alerts):
    return [alert for alert in alerts if alert['sensor'] == 'Turbidity'
            and alert['type'] == 'prediction']


def trend(clock, values):
    alerts = []
    for value in values:
        alerts = send(value)
        clock[0] += 3
    return alerts


def test_default_adc_trend_reaches_web_and_desktop(clock):
    for i in range(10):
        assert send(900 - i * 6) == []
        clock[0] += 3
    result = send(840)
    assert len(result) == 1
    alert = result[0]
    assert alert['target_category'] == 'Cloudy'
    assert alert['limit'] == 409
    assert alert['unit'] == ' ADC' and 'NTU' not in alert['message']
    assert alert['trend'] == 'cloudier' and alert['eta_seconds'] > 0
    assert config.sensor_history['turbidity'] == []
    assert len(config.sensor_history['turbidity_adc']) == 11
    assert forecasts(flask_app.test_client().get('/api/sensors').json['alerts'])
    assert forecasts(build_snapshot()[-1])
    assert 'may become Cloudy' in alert['short_message']


def test_cloudy_can_predict_very_cloudy(clock):
    result = trend(clock, range(380, 259, -12))
    assert len(result) == 1
    assert result[0]['target_category'] == 'Very Cloudy'
    assert result[0]['limit'] == 170
    assert send(100) == []


@pytest.mark.parametrize('values', [
    [900] * 11,
    list(range(800, 921, 12)),
    [900, 895, 902, 897, 904, 895, 900, 898, 905, 896, 900],
])
def test_stable_improving_or_noisy_water_has_no_prediction(clock, values):
    assert trend(clock, values) == []


def test_custom_category_boundaries_and_reversed_adc_direction(clock):
    calibration.set_active(True)
    calibration.save({'ph': None, 'turbidity': [
        dict(min=0, max=600, label='Looks clear 123', category='Clear'),
        dict(min=601, max=850, label='Needs attention', category='Cloudy'),
        dict(min=851, max=1023, label='Dirty', category='Very Cloudy'),
    ]})
    calibration.set_active(False)
    result = trend(clock, range(400, 521, 12))
    assert result[0]['target_category'] == 'Cloudy'
    assert 'Needs attention' in result[0]['short_message']
    assert result[0]['limit'] == 601
    assert result[0]['trend_rate_per_second'] > 0
    assert config.sensor_history['turbidity'] == []


def test_ntu_configured_ranges_use_the_same_compiled_boundaries(clock):
    from test_ntu_calibration import install
    install()
    result = trend(clock, range(900, 779, -12))
    assert result[0]['limit'] == 700
    assert result[0]['target_category'] == 'Cloudy'
    assert result[0]['unit'] == ' ADC'


@pytest.mark.parametrize('invalid', ['WAITING', 'PAUSED', 'ERROR', 'bad', '-1', '1024'])
def test_invalid_reading_clears_prediction_and_requires_new_history(clock, invalid):
    assert trend(clock, range(900, 779, -12))
    assert send(invalid, label='WAITING') == []
    assert config.sensor_history['turbidity_adc'] == []
    assert send(780) == []


def test_stale_packet_gap_disable_calibration_and_reconnect_clear_history(clock):
    assert trend(clock, range(900, 779, -12))
    clock[0] += 20
    assert forecasts(build_sensor_alerts(config.sensor_data, config.sensor_history)) == []
    assert send(770) == []
    assert len(config.sensor_history['turbidity_adc']) == 1
    sensor_control.apply({'turbidity': False})
    assert config.sensor_history['turbidity_adc'] == []
    sensor_control.apply({'turbidity': True})
    assert trend(clock, range(900, 779, -12))
    calibration.set_active(True)
    assert config.sensor_history['turbidity_adc'] == []
    assert send(770) == []
    calibration.set_active(False)
    assert trend(clock, range(900, 779, -12))
    sensor_control.input_reconnected()
    assert config.sensor_history['turbidity_adc'] == []


def test_custom_history_duration_is_observed(clock, monkeypatch):
    monkeypatch.setattr(config, 'TREND_WINDOW_SECONDS', 60.0)
    assert trend(clock, range(950, 835, -6)) == []
    assert send(830)
