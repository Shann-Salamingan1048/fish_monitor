"""Shared, persistent operator sensor overrides for desktop and web.

Disabled means intentionally excluded from readings, alarms and automation;
it is not a missing-sensor fault. The input mask stops acquisition as well.
"""
import json
import secrets
import time
import config

SENSORS = {
    'temp': ('Temperature', 'Disables temperature alerts and automatic cooling. Manual fan control remains available.'),
    'ph': ('pH', 'Disables pH alerts and pH-triggered water replacement.'),
    'turbidity': ('Turbidity', 'Disables cloudiness alerts and turbidity-triggered extraction.'),
    'float_low': ('Low float switch', 'Bypasses low-level refill requests and low-level extraction protection.'),
    'float_normal': ('Normal float switch', 'Bypasses the normal-level pump stop. An enabled upper switch can still stop refilling.'),
    'float_upper': ('Upper float switch', 'Bypasses overflow alerts and upper-level pump protection.'),
}
FLOAT_KEYS = ('float_low', 'float_normal', 'float_upper')
_enabled = {key: True for key in SENSORS}
_raw = {key: None for key in SENSORS}
_raw['turbidity_adc'] = None
_revision = 0
_input_revision = None
_cooler_confirmed = None
_legacy_allowed = True
_session = secrets.token_hex(8)
_last_input_sync = 0.0
_last_output_sync = 0.0


def initialize():
    global _revision, _input_revision, _cooler_confirmed, _legacy_allowed
    with config.sensor_lock:
        import water_control
        water_control.reset_confirmation()
        saved = json.loads(config.get_db_value('sensor_enabled', '{}'))
        for key in SENSORS:
            _enabled[key] = saved.get(key, True) is True
            _raw[key] = None
        _raw['turbidity_adc'] = None
        _revision = secrets.randbelow(1_000_000_000) + 1
        _input_revision = None
        _cooler_confirmed = None
        _legacy_allowed = all(_enabled.values())
        for key in config.sensor_history:
            config.sensor_history[key].clear()
            config.last_history_sample_at[key] = 0
        config.clear_prediction_alert_cache()
        refresh_display()


def enabled(key):
    with config.sensor_lock:
        return _enabled[key]


def all_floats_enabled():
    return all(enabled(key) for key in FLOAT_KEYS)


def float_reading(key):
    with config.sensor_lock:
        return _raw[key] if _enabled[key] else None


def water_level():
    """Never treat a disabled/unknown float as a dry (false) switch."""
    with config.sensor_lock:
        if not any(_enabled[key] for key in FLOAT_KEYS):
            return 'DISABLED'
        low, normal, upper = (float_reading(key) for key in FLOAT_KEYS)
        if upper is True:
            return 'Water Level is Overflowing'
        if normal is True:
            return 'Water Level is Normal'
        if low is False:
            return 'Water Level is Very Low'
        if low is True and normal is False:
            return 'Water Level is Low'
        return 'PARTIAL' if not all_floats_enabled() else 'WAITING'


def sync_input():
    global _last_input_sync
    with config.sensor_lock:
        _last_input_sync = time.monotonic()
        mask = sum(1 << i for i, key in enumerate(SENSORS) if _enabled[key])
        return config.send_to_input_serial_ports(f'MegaInput,SENSORS:{mask},REV:{_revision}\n')


def sync_output():
    global _last_output_sync, _cooler_confirmed
    with config.sensor_lock:
        _last_output_sync = time.monotonic()
        _cooler_confirmed = None
        state = 'ON' if _enabled['temp'] else 'OFF'
        return config.send_to_identified_output_serial_ports(f'MegaOutput,TEMPSENSOR:{state}\n')


def retry_pending_sync():
    with config.sensor_lock:
        if config.input_serial_ports and _input_revision != _revision and time.monotonic() - _last_input_sync >= 2:
            sync_input()
        if config.output_serial_ports and _cooler_confirmed != _enabled['temp'] and time.monotonic() - _last_output_sync >= 2:
            sync_output()


def input_reconnected():
    global _input_revision
    with config.sensor_lock:
        _input_revision = None
        import calibration
        calibration.reset_readings()
        config.sensor_history['turbidity_adc'].clear()
        config.last_history_sample_at['turbidity_adc'] = 0
        with config.prediction_lock:
            config.active_prediction_alerts.pop('turbidity:adc', None)
            config.active_prediction_alerts.pop('turbidity:rising', None)
        import water_control
        water_control.reset_confirmation()
        for key in _raw:
            _raw[key] = None
        refresh_display()
        return sync_input()


def output_status(line):
    global _cooler_confirmed
    for part in line.split(','):
        if part in ('TEMPSENSOR:ON', 'TEMPSENSOR:OFF'):
            with config.sensor_lock:
                _cooler_confirmed = part == 'TEMPSENSOR:ON'


def accept_packet(fields):
    """Reject queued packets from an older configuration after a toggle."""
    global _input_revision
    with config.sensor_lock:
        if 'SENSORCFG' in fields:
            if fields['SENSORCFG'] != str(_revision):
                if time.monotonic() - _last_input_sync >= 2:
                    sync_input()
                return False
            _input_revision = _revision
        elif not _legacy_allowed:
            return False
        import calibration
        calibration.apply_packet(fields)
        for key, field in (('temp', 'Temp'), ('ph', 'pH'), ('turbidity', 'Turbidity')):
            value = fields.get(field, 'WAITING')
            _raw[key] = value if _enabled[key] else None
        # Older firmware has no ADC field; never retain its previous value.
        try:
            adc = int(fields.get('TurbidityADC', ''))
        except (TypeError, ValueError):
            adc = None
        _raw['turbidity_adc'] = adc if (_enabled['turbidity'] and adc is not None
                                       and 0 <= adc <= 1023) else None
        for key, field in (('float_low', 'FloatLow'), ('float_normal', 'FloatNormal'), ('float_upper', 'FloatUpper')):
            value = fields.get(field)
            _raw[key] = (value == '1') if _enabled[key] and value in ('0', '1') else None
        return True


def refresh_display():
    # Imported here to keep the shared controller free of import cycles.
    import water_control
    with config.sensor_lock:
        policy = water_control.get_sensor_policy()
        for key in ('temp', 'ph', 'turbidity'):
            config.sensor_data[key] = ('DISABLED' if not _enabled[key] else
                                       'PAUSED' if not policy[key] else
                                       _raw[key] if _raw[key] is not None else 'WAITING')
        config.sensor_data['float_stat'] = water_level()
        config.sensor_data['turbidity_adc'] = (
            _raw['turbidity_adc'] if _enabled['turbidity'] and policy['turbidity']
            and str(config.sensor_data['turbidity']).upper() not in
            {'WAITING', 'DISABLED', 'PAUSED', 'ERROR', '--'} else None)


def apply(changes):
    global _revision, _input_revision, _legacy_allowed, _cooler_confirmed
    if not isinstance(changes, dict) or not changes or set(changes) - set(SENSORS):
        raise ValueError('Provide one or more known sensor names.')
    if any(type(value) is not bool for value in changes.values()):
        raise ValueError('Sensor enabled values must be true or false.')
    with config.sensor_lock:
        changed = {key for key, value in changes.items() if _enabled[key] != value}
        if not changed:
            return get_status()
        proposed = dict(_enabled, **changes)
        # Persist first; if saving fails, leave the running configuration intact.
        with config.db_connect() as conn:
            conn.execute('INSERT INTO app_state (key,value,updated_at) VALUES (?,?,?) '
                         'ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
                         ('sensor_enabled', json.dumps(proposed), time.time()))
        if changed & {'ph', 'turbidity'}:
            import calibration
            calibration.reset_readings()
        _enabled.update(proposed)
        _revision += 1
        _input_revision = None
        _legacy_allowed = False
        if 'temp' in changed:
            _cooler_confirmed = None
        for key in changed:
            _raw[key] = None
            if key == 'turbidity':
                _raw['turbidity_adc'] = None
                config.sensor_history['turbidity_adc'].clear()
                config.last_history_sample_at['turbidity_adc'] = 0
            if key in config.sensor_history:
                config.sensor_history[key].clear()
                config.last_history_sample_at[key] = 0
        config.clear_prediction_alert_cache()
        import water_control
        water_control.sensor_settings_changed(changed)
        refresh_display()
        config.settings_version += 1
        sync_input()
        if 'temp' in changed:
            sync_output()
        return get_status()


def get_status():
    with config.sensor_lock:
        import water_control
        policy = water_control.get_sensor_policy()
        import calibration
        sensors = {}
        for key, (label, effect) in SENSORS.items():
            raw = _raw[key]
            if key == 'turbidity' and raw is not None:
                raw = calibration.clarity_readout(raw) if policy[key] else 'PAUSED'
            status = ('disabled' if not _enabled[key] else
                      'paused' if key in ('temp', 'ph', 'turbidity') and not policy[key] else
                      'waiting' if raw is None or str(raw).upper() in ('WAITING', '--', 'DISABLED') else
                      'paused' if str(raw).upper() == 'PAUSED' else
                      'error' if str(raw).upper() == 'ERROR' else 'active')
            sensors[key] = {'label': label, 'effect': effect, 'enabled': _enabled[key],
                            'status': status, 'reading': raw if _enabled[key] else None}
        disabled = [key for key in SENSORS if not _enabled[key]]
        return {'sensors': sensors, 'disabled': disabled, 'revision': _revision, 'session': _session,
                'inputApplied': bool(config.input_serial_ports) and _input_revision == _revision,
                'coolerApplied': bool(config.output_serial_ports) and _cooler_confirmed == _enabled['temp'],
                'allDisabled': len(disabled) == len(SENSORS)}


def filter_alerts(alerts):
    with config.sensor_lock:
        names = {'Temperature': 'temp', 'Turbidity': 'turbidity', 'pH Level': 'ph'}
        return [alert for alert in alerts if
                (alert.get('sensor') not in names or _enabled[names[alert['sensor']]]) and
                (alert.get('sensor') != 'Water Level' or all_floats_enabled() or
                 str(alert.get('value')) == water_level())]
