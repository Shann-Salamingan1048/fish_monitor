"""Only critical conditions initiate automatic correction; warnings stay visible."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
import pytest
import config
import water_control as water
from test_system import isolated, connected
from test_sensor_controls import packet
from test_fan_confirmation import clock
from predictions import build_sensor_alerts


@pytest.mark.parametrize('values', [
    {'ph':'6.2'}, {'ph':'8.7'}, {'turbidity':'Cloudy'},
    {'turbidity':'Slightly Cloudy'}, {'normal':'0'}, {'temp':'34'},
    {'temp':'34', 'ph':'8.7', 'turbidity':'Cloudy','normal':'0'}])
def test_sustained_warning_never_starts_pumps(clock,values):
    port=connected()
    for t in range(100,121):
        clock[0]=t;packet(**values)
        assert not water.is_pump_active()
        assert not water.get_status()['pendingQuality']
    assert not any('PUMP:BOTH_ON' in m or 'PUMP:INSERT_ON' in m or 'PUMP:EXTRACT_ON' in m for m in port.messages)
    if values.get('temp'):
        assert not any(',TEMP:' in m for m in port.messages)  # Temperature is local.












@pytest.mark.parametrize('level,severity',[('Low','warning'),('Very Low','critical'),('Overflowing','critical')])
def test_level_alert_severity_matches_action_category(level,severity):
    alerts=build_sensor_alerts({'temp':'27','ph':'7','turbidity':'Clear','float_stat':level}, {})
    assert next(a for a in alerts if a['sensor']=='Water Level')['severity']==severity


@pytest.mark.parametrize('clarity,severity',[('Cloudy','warning'),('Slightly Cloudy','warning'),('Very Cloudy','critical')])
def test_clarity_alert_uses_category_without_fake_ntu(clarity,severity):
    alerts=build_sensor_alerts({'temp':'27','ph':'7','turbidity':clarity,'float_stat':'Normal'}, {})
    alert=next(a for a in alerts if a['sensor']=='Turbidity' and a['type']=='limit')
    assert alert['severity']==severity and alert['value']==clarity
    assert 'NTU' not in alert['message']


def test_custom_critical_limits_control_dispatch(clock,monkeypatch):
    import mega_control
    port=connected()
    monkeypatch.setattr(config,'PH_CRITICAL_HIGH_LIMIT',10)
    monkeypatch.setattr(config,'TEMP_OVERHEAT_LIMIT_C',37)
    mega_control.sync(force=True)
    assert 'PH:10,' in port.messages[-1] and 'FAN:37,' in port.messages[-1]
    for t in range(100,113):clock[0]=t;packet(ph='10',temp='36')
    assert not water.is_pump_active()  # The host cannot independently actuate.



class Var:
    def __init__(self,value):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value


def test_gui_settings_choice_updates_header_and_persists():
    from tkinter_gui import FocusedMonitorApp
    from local_controls import LocalControls
    updates=[]
    app=SimpleNamespace(temperature_unit='C',temperature_unit_var=Var('Celsius (°C)'),
        _last_update=None,controls_window=SimpleNamespace(winfo_exists=lambda:True,
            change_temperature_unit=lambda unit:updates.append(unit)))
    panel=SimpleNamespace(unit_choice=Var('Fahrenheit (°F)'),
        on_temperature_unit_change=lambda unit:FocusedMonitorApp.set_temperature_unit(app,unit))
    LocalControls.select_temperature_unit(panel)
    assert app.temperature_unit=='F' and app.temperature_unit_var.get()=='Fahrenheit (°F)'
    assert updates==['F'] and config.get_db_value('gui_temperature_unit')=='F'
    assert FocusedMonitorApp._format_temp_display(app,30)=='86.00 °F'
    panel.unit_choice.set('Celsius (°C)');LocalControls.select_temperature_unit(panel)
    assert app.temperature_unit=='C' and config.get_db_value('gui_temperature_unit')=='C'
