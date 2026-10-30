import pytest
import config
import sensor_control
from mcu_comm import MCUCommunicator
from flask_server import flask_app
from test_system import isolated


def receive(adc='500', category='Clear', revision=None):
    revision = sensor_control._revision if revision is None else revision
    packet = (f'Temp:27,pH:7,Turbidity:{category},FloatLow:1,'
              f'FloatNormal:1,FloatUpper:0,SENSORCFG:{revision}')
    if adc is not None:
        packet += f',TurbidityADC:{adc}'
    return MCUCommunicator()._parse_sensor_message(packet)[0]


@pytest.mark.parametrize('adc', [0, 500, 1023])
def test_raw_adc_reaches_api_without_changing_category(adc):
    snapshot = receive(str(adc))
    assert snapshot['turbidity_adc'] == adc
    assert snapshot['turbidity'] == 'Clear'
    assert flask_app.test_client().get('/api/sensors').json['turbidity_adc'] == adc


@pytest.mark.parametrize('adc', [None, '', '-1', '1024', 'NaN', '1.5', 'WAITING', 'PAUSED', 'DISABLED'])
def test_missing_or_invalid_adc_clears_previous_reading(adc):
    assert receive()['turbidity_adc'] == 500
    assert receive(adc)['turbidity_adc'] is None


def test_adc_clears_on_disable_reenable_and_reconnect():
    receive()
    sensor_control.apply({'turbidity': False})
    assert config.sensor_data['turbidity_adc'] is None
    assert receive()['turbidity_adc'] is None
    sensor_control.apply({'turbidity': True})
    assert config.sensor_data['turbidity_adc'] is None
    assert receive()['turbidity_adc'] == 500
    sensor_control.input_reconnected()
    assert config.sensor_data['turbidity_adc'] is None


def test_paused_or_waiting_category_hides_adc():
    for category in ('PAUSED', 'WAITING', 'ERROR'):
        assert receive('500', category)['turbidity_adc'] is None


def test_old_revision_cannot_replace_adc():
    receive('500')
    assert receive('900', revision=sensor_control._revision - 1)['turbidity_adc'] == 500
