"""NTU reference calibration, persistence and exact Mega ADC range conversion."""
import copy
import math
import pytest
import calibration
import config
import mega_control
import sensor_control
from flask_server import flask_app
from test_system import isolated
from test_developer_calibration import packet, clock


def profile():
    # Synthetic software test data only.
    return dict(ph=None, turbidityUnit='NTU',
                turbidityPoints=[dict(adc=900, ntu=0), dict(adc=500, ntu=200), dict(adc=100, ntu=1000)],
                turbidity=[dict(min=0, max=100, label='Clear custom', category='Clear'),
                           dict(min=100, max=600, label='Warning custom', category='Cloudy'),
                           dict(min=600, max=1000, label='Critical custom', category='Very Cloudy')])


def install(value=None):
    calibration.set_active(True)
    calibration.save(profile() if value is None else value)
    calibration.set_active(False)


def test_all_adc_counts_match_web_and_mega_rules():
    install(); rows=calibration.effective_adc_ranges()
    assert [(r['min'],r['max'],r['category']) for r in rows]==[(0,300,'Very Cloudy'),(301,700,'Cloudy'),(701,1023,'Clear')]
    _, commands=mega_control.commands()
    for part in ('MIN:0,MAX:300,CAT:0','MIN:301,MAX:700,CAT:1','MIN:701,MAX:1023,CAT:3'):
        assert part in commands
    assert 'custom' not in commands
    for adc in range(1024):
        snapshot,_=packet(ta=str(adc))
        expected=next(r for r in rows if r['min']<=adc<=r['max'])
        assert snapshot['turbidity']==expected['label']
        assert calibration.category_for_label(snapshot['turbidity'])==expected['category']
    for adc, ntu in [(900,0),(700,100),(500,200),(300,600),(100,1000)]:
        packet(ta=str(adc)); assert calibration.status()['live']['turbidityNTU']==ntu


def test_uncalibrated_outside_and_stale_readings(clock):
    calibration.set_active(True)
    calibration.set_active(False)
    packet(ta='900')
    live = calibration.status()['live']
    assert live['turbidityNTUState'] == 'illustrative'
    assert live['illustrativeNTU'] == pytest.approx(120.2346, abs=.0001)
    install()
    for adc, label in [(0,'Critical custom'),(1023,'Clear custom')]:
        assert packet(ta=str(adc))[0]['turbidity']==label
        live=calibration.status()['live']
        assert live['turbidityADC']==adc and live['turbidityNTU'] is None
        assert live['turbidityNTUState']=='out_of_range'
        assert live['illustrativeNTU'] == pytest.approx((1023-adc)*1000/1023)
    packet(ta='700');clock[0]+=4
    live=calibration.status()['live']
    assert live['turbidityADC'] is None and live['turbidityNTU'] is None
    assert live['illustrativeNTU'] is None
    sensor_control.input_reconnected()
    assert calibration.status()['live']['turbidityNTUState']=='unavailable'


def test_adc_mode_displays_ntu_but_preserves_adc_rules():
    value=profile();value.update(turbidityUnit='ADC',turbidity=copy.deepcopy(calibration.DEFAULT_RANGES))
    install(value);packet(ta='700')
    assert calibration.status()['live']['turbidityNTU']==100
    assert calibration.effective_adc_ranges()==calibration.DEFAULT_RANGES
    calibration.initialize()
    assert calibration.status()['profile']==value
    assert calibration.effective_adc_ranges()==calibration.DEFAULT_RANGES


def test_ntu_restart_preserves_controller_configuration():
    install();before=mega_control.commands();calibration.initialize()
    assert mega_control.commands()==before


@pytest.mark.parametrize('change',['points_missing','point_count','point_boolean','point_nan','point_duplicate',
                                  'point_direction','point_span','point_limit','gap','overlap','end','unit','ranges_missing'])
def test_invalid_profile_is_rejected_atomically(change):
    install();calibration.set_active(True)
    before=config.get_db_value('sensor_calibration');rules=calibration.effective_adc_ranges()
    value=profile();points=value['turbidityPoints'];rows=value['turbidity']
    if change=='points_missing':value['turbidityPoints']=None
    if change=='point_count':value['turbidityPoints']=points[:1]
    if change=='point_boolean':points[0]['adc']=True
    if change=='point_nan':points[0]['ntu']=math.nan
    if change=='point_duplicate':points[1]['ntu']=0
    if change=='point_direction':points[1]['adc']=950
    if change=='point_span':value['turbidityPoints']=[dict(adc=900,ntu=0),dict(adc=890,ntu=1000)]
    if change=='point_limit':points[-1]['ntu']=1001
    if change=='gap':rows[1]['min']=101
    if change=='overlap':rows[1]['min']=99
    if change=='end':rows[-1]['max']=999
    if change=='unit':value['turbidityUnit']='volts'
    if change=='ranges_missing':value['turbidity']=None
    with pytest.raises(ValueError):calibration.save(value)
    assert config.get_db_value('sensor_calibration')==before
    assert calibration.effective_adc_ranges()==rules


def test_fractional_ntu_thresholds():
    value=profile();value['turbidity'][0]['max']=100.25;value['turbidity'][1]['min']=100.25
    install(value)
    assert packet(ta='700')[0]['turbidity']=='Clear custom'
    assert packet(ta='699')[0]['turbidity']=='Warning custom'


def test_admin_api_exposes_both_readings(monkeypatch):
    monkeypatch.setattr(config,'DEVELOPER_OPTIONS',True)
    client=flask_app.test_client()
    assert client.put('/api/developer/calibration',json=profile()).status_code==403
    client.post('/api/auth/login',json={'pin':config.ADMIN_PIN})
    page=client.get('/developer').text
    for control in ('live-turbidity-ntu','live-turbidity-adc','turbidity-unit','ntu-points'):
        assert f'id="{control}"' in page
    client.post('/api/developer/calibration',json={'active':True})
    assert client.put('/api/developer/calibration',json=profile()).status_code==200
    packet(ta='700')
    live=client.get('/api/developer/calibration').json['calibration']['live']
    assert live['turbidityADC']==700 and live['turbidityNTU']==100
    assert live['turbidityNTUState']=='calibrated'


def test_calibration_only_ntu_and_dashboard_categories(clock):
    from gui_snapshot import build_snapshot
    client = flask_app.test_client()
    def check(category, ntu=None):
        for snapshot in (client.get('/api/sensors').json, build_snapshot()[1]):
            assert snapshot['turbidity'] == category
            assert 'turbidityNTU' not in snapshot and 'turbidityNTUState' not in snapshot
        assert calibration.status()['live']['turbidityNTU'] == ntu
    packet(ta='770')
    check('Clear')
    packet(ta='1023', clarity='Very Clear')
    check('Very Clear')
    install()
    packet(ta='700')
    check('Warning custom', 100)
    assert client.get('/api/sensors').json['clarityLabels'] == ['Clear custom', 'Warning custom', 'Critical custom']
    packet(ta='900')
    check('Clear custom', 0)
    packet(ta='1000')
    check('Clear custom')
    clock[0] += 5
    check('WAITING')
    calibration.set_active(True)
    packet(ta='700')
    check('WAITING', 100)
    calibration.set_active(False)
    sensor_control.apply({'turbidity': False})
    packet(ta='700')
    check('DISABLED')


@pytest.mark.parametrize('reading,category', [('18.5 NTU','Clear'), ('300 NTU','Cloudy'), ('600 NTU','Very Cloudy')])
def test_legacy_numeric_readout(reading, category):
    from gui_snapshot import build_snapshot
    packet(ta='WAITING', clarity=reading)
    assert build_snapshot()[1]['turbidity'] == category


def test_two_labels_with_same_category_remain_distinct():
    from gui_snapshot import build_snapshot
    install(dict(ph=None, turbidity=[
        dict(min=0, max=300, label='Hazy 2', category='Cloudy'),
        dict(min=301, max=600, label='Hazy 1', category='Cloudy'),
        dict(min=601, max=1023, label='Clear', category='Clear'),
    ]))
    client = flask_app.test_client()
    for adc, expected in [(200, 'Hazy 2'), (500, 'Hazy 1')]:
        packet(ta=str(adc))
        view = client.get('/api/sensors').json
        assert view['clarityLabels'] == ['Clear', 'Hazy 1', 'Hazy 2']
        assert view['turbidity'] == build_snapshot()[1]['turbidity'] == expected
        assert view['clarityCategory'] == 'Cloudy'
        assert any(expected in alert['message'] for alert in view['alerts'])
