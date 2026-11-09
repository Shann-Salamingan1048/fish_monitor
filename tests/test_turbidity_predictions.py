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
    assert alert['target_label'] == alert['target_category'] == 'Cloudy'
    assert alert['limit'] == 599
    assert alert['unit'] == ' ADC' and 'NTU' not in alert['message']
    assert alert['trend'] == 'falling' and alert['eta_seconds'] > 0
    assert config.sensor_history['turbidity'] == []
    assert len(config.sensor_history['turbidity_adc']) == 11
    assert forecasts(flask_app.test_client().get('/api/sensors').json['alerts'])
    assert forecasts(build_snapshot()[-1])
    assert 'may reach Cloudy' in alert['short_message']


def test_cloudy_can_predict_very_cloudy(clock):
    result = trend(clock, range(555, 434, -12))
    assert len(result) == 1
    assert result[0]['target_category'] == 'Very Cloudy'
    assert result[0]['limit'] == 399
    assert send(100) == []


@pytest.mark.parametrize('values', [
    [900] * 11,
    [900, 895, 902, 897, 904, 895, 900, 898, 905, 896, 900],
])
def test_stable_or_noisy_water_has_no_prediction(clock, values):
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


def set_ranges(rows):
    calibration.set_active(True)
    calibration.save({'ph': None, 'turbidity': rows})
    calibration.set_active(False)


@pytest.mark.parametrize('values,label,boundary,direction', [
    (range(500, 601, 10), 'Crystal 2', 701, 'rising'),
    (range(900, 799, -10), 'Clear sample', 700, 'falling'),
    (range(790, 891, 10), 'Probe band 4', 951, 'rising'),
    (range(400, 299, -10), 'Opaque', 200, 'falling'),
])
def test_forecasts_nearest_calibrated_label_in_both_directions(clock, values, label, boundary, direction):
    set_ranges([
        dict(min=0, max=200, label='Opaque', category='Very Cloudy'),
        dict(min=201, max=700, label='Clear sample', category='Clear'),
        dict(min=701, max=950, label='Crystal 2', category='Clear'),
        dict(min=951, max=1023, label='Probe band 4', category='Slightly Cloudy'),
    ])
    result = trend(clock, values)
    assert len(result) == 1
    alert = result[0]
    assert (alert['target_label'], alert['limit'], alert['trend']) == (label, boundary, direction)
    assert label in alert['short_message']
    assert alert['eta_seconds'] == pytest.approx((boundary - values[-1]) / alert['trend_rate_per_second'])
    assert forecasts(flask_app.test_client().get('/api/sensors').json['alerts'])[0]['target_label'] == label
    assert forecasts(build_snapshot()[-1])[0]['target_label'] == label


def test_reversal_replaces_old_forecast_and_reached_label_is_retired(clock):
    result = trend(clock, range(450, 551, 10))
    assert result[0]['target_label'] == 'Clear'
    result = trend(clock, range(550, 439, -10))
    assert len(result) == 1 and result[0]['target_label'] == 'Very Cloudy'
    assert send(399) == []


def test_one_range_has_no_transition_to_predict(clock):
    set_ranges([dict(min=0, max=1023, label='My whole range', category='Clear')])
    assert trend(clock, range(500, 601, 10)) == []


def test_ntu_calibration_forecasts_recovery_using_raw_adc(clock):
    from test_ntu_calibration import install
    install()
    result = trend(clock, range(520, 621, 10))
    assert (result[0]['target_label'], result[0]['limit']) == ('Clear custom', 701)
    assert result[0]['unit'] == ' ADC'


def test_new_forecast_replaces_prior_sensor_with_limit_alerts_still_visible(clock):
    from predictions import _fit_window
    start = clock[0] - 30
    data = {'temp': '27', 'ph': '7.4', 'turbidity': 'Clear', 'float_stat': 'Overflow'}
    history = {'temp': [(start + i * 3, 26.5 + i * .05) for i in range(11)]}
    def visible():
        alerts = build_sensor_alerts(data, history)
        assert any(a['sensor'] == 'Water Level' and a['severity'] == 'critical' for a in alerts)
        return [a['sensor'] for a in alerts if a['type'] == 'prediction']
    assert visible() == ['Temperature']
    history['ph'] = [(start + i * 3, 7.2 + i * .02) for i in range(11)]
    assert visible() == ['pH Level']
    before = _fit_window.cache_info()
    assert visible() == ['pH Level']
    assert _fit_window.cache_info().misses == before.misses
    data['ph'] = 'WAITING'
    assert visible() == []


def test_simultaneous_predictions_use_nearest_eta_and_replace_at_target(clock):
    start = clock[0] - 30
    history = {'temp': [(start + i * 3, 28 + i * .09) for i in range(11)],
               'ph': [(start + i * 3, 7.2 + i * .03) for i in range(11)]}
    data = {'temp': 28.9, 'ph': 7.5, 'turbidity': 'Clear', 'float_stat': 'Normal'}
    # Anchor the test thresholds independently of any other settings form test.
    from unittest.mock import patch
    with patch.multiple(config, TEMP_LIMIT_C=29, TEMP_OVERHEAT_LIMIT_C=32, PH_HIGH_LIMIT=8):
        def forecast():
            return [(a['sensor'], a['limit']) for a in build_sensor_alerts(data, history)
                    if a['type'] == 'prediction']
        assert forecast() == [('Temperature', 29)]
        data['temp'] = 29.1
        assert forecast() == [('Temperature', 32)]
