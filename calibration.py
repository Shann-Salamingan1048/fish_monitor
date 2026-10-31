"""Pi-side calibration. Nano firmware/EEPROM are never modified.

All runtime state is protected by config.sensor_lock. Persistence is one JSON
record so a failed save cannot leave half of a calibration installed.
"""
import copy
import json
import math
import time
import config

CATEGORIES = ('Very Cloudy', 'Cloudy', 'Slightly Cloudy', 'Clear', 'Very Clear')
DEFAULT_RANGES = [
    {'min': 0, 'max': 170, 'label': 'Very Cloudy', 'category': 'Very Cloudy'},
    {'min': 171, 'max': 409, 'label': 'Cloudy', 'category': 'Cloudy'},
    {'min': 410, 'max': 750, 'label': 'Slightly Cloudy', 'category': 'Slightly Cloudy'},
    {'min': 751, 'max': 954, 'label': 'Clear', 'category': 'Clear'},
    {'min': 955, 'max': 1023, 'label': 'Very Clear', 'category': 'Very Clear'},
]
_profile = {'ph': None, 'turbidity': None}
_maintenance = False
_ph_filtered = None
_ph_time = None
_live = {'phADC': None, 'turbidityADC': None, 'receivedAt': None}


def _number(value, name, lo=0, hi=1023):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a number.')
    if not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f'{name} must be between {lo} and {hi}.')
    return value


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != {'ph', 'turbidity'}:
        raise ValueError('Provide pH and turbidity settings.')
    ph = payload['ph']
    if ph is not None:
        if not isinstance(ph, dict) or set(ph) != {'adc686', 'adc918'}:
            raise ValueError('Provide ADC readings for pH 6.86 and 9.18.')
        a = _number(ph['adc686'], 'pH 6.86 ADC', 1, 1022)
        b = _number(ph['adc918'], 'pH 9.18 ADC', 1, 1022)
        if a - b < 20:
            raise ValueError('6.86 ADC must exceed 9.18 ADC by at least 20 counts. Check the buffers and readings.')
        ph = {'adc686': a, 'adc918': b}
    rows = payload['turbidity']
    if rows is not None:
        if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
            raise ValueError('Use 1 to 20 turbidity ranges.')
        result, labels = [], set()
        reserved = {'waiting', 'paused', 'disabled', 'error', '--', 'unknown', 'nan'}
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'min', 'max', 'label', 'category'}:
                raise ValueError('Each range needs minimum ADC, maximum ADC, display name and category.')
            low = _number(row['min'], 'Minimum ADC')
            high = _number(row['max'], 'Maximum ADC')
            if int(low) != low or int(high) != high or low > high:
                raise ValueError('ADC ranges must use whole numbers with minimum ≤ maximum.')
            label = row['label']
            if not isinstance(label, str) or not 1 <= len(label.strip()) <= 32 or any(ord(c) < 32 for c in label):
                raise ValueError('Display names must contain 1 to 32 printable characters.')
            label = label.strip()
            if label.casefold() in labels or label.casefold() in reserved:
                raise ValueError('Use unique display names, excluding sensor status words.')
            labels.add(label.casefold())
            if row['category'] not in CATEGORIES:
                raise ValueError('Choose a standard clarity category for each range.')
            result.append(dict(min=int(low), max=int(high), label=label, category=row['category']))
        rows = sorted(result, key=lambda row: row['min'])
        next_adc = 0
        for row in rows:
            if row['min'] != next_adc:
                raise ValueError('Ranges must cover every ADC count from 0 to 1023, without gaps or overlaps.')
            next_adc = row['max'] + 1
        if next_adc != 1024:
            raise ValueError('Ranges must end at ADC 1023.')
    return {'ph': ph, 'turbidity': rows}


def reset_readings():
    global _ph_filtered, _ph_time
    with config.sensor_lock:
        _ph_filtered = _ph_time = None
        _live.update(phADC=None, turbidityADC=None, receivedAt=None)


def initialize():
    global _profile, _maintenance
    with config.sensor_lock:
        # Invalid stored calibration is an error, never silently reinterpret water.
        _profile = validate(json.loads(config.get_db_value('sensor_calibration', '{"ph":null,"turbidity":null}')))
        _maintenance = config.get_db_value('calibration_active', '0') == '1'
        reset_readings()


def active():
    with config.sensor_lock:
        return _maintenance


def _clear_history():
    for key in ('ph', 'turbidity'):
        config.sensor_history[key].clear()
        config.last_history_sample_at[key] = 0
    config.clear_prediction_alert_cache()


def _persist(key, value):
    # config.set_db_value is best-effort for legacy settings; calibration must
    # report disk failures and keep the previous in-memory profile intact.
    with config.db_connect() as conn:
        conn.execute('INSERT INTO app_state (key,value,updated_at) VALUES (?,?,?) '
                     'ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
                     (key, value, time.time()))


def set_active(enabled):
    global _maintenance
    if type(enabled) is not bool:
        raise ValueError('Calibration mode must be true or false.')
    with config.sensor_lock:
        if enabled != _maintenance:
            _persist('calibration_active', '1' if enabled else '0')
            _maintenance = enabled
            _clear_history()
            reset_readings()
            import water_control
            water_control.calibration_changed()
        return status()


def save(payload):
    global _profile
    proposed = validate(payload)
    with config.sensor_lock:
        if not _maintenance:
            raise ValueError('Start calibration before saving changes.')
        _persist('sensor_calibration', json.dumps(proposed, allow_nan=False))
        _profile = proposed
        reset_readings()
        _clear_history()
        import water_control
        water_control.calibration_changed()
        config.settings_version += 1
        return status()


def category_for_label(value):
    """Only the explicit category drives rules; labels may contain any text."""
    with config.sensor_lock:
        for row in _profile['turbidity'] or []:
            if str(value).strip().casefold() == row['label'].casefold():
                return row['category']
    return value


def is_custom_label(value):
    with config.sensor_lock:
        return any(str(value).strip().casefold() == row['label'].casefold()
                   for row in _profile['turbidity'] or [])


def _adc(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and 0 <= result <= 1023 else None
    except (TypeError, ValueError):
        return None


def apply_packet(fields):
    """Called only after SENSORCFG validation, under the shared sensor lock."""
    global _ph_filtered, _ph_time
    import sensor_control
    import water_control
    policy = water_control.get_sensor_policy()
    now = time.monotonic()
    pa = _adc(fields.get('pHADC')) if policy['ph'] else None
    ta = _adc(fields.get('TurbidityADC')) if policy['turbidity'] else None
    if ta is not None and int(ta) != ta:
        ta = None
    _live.update(phADC=pa, turbidityADC=ta, receivedAt=now)
    anchor = _profile['ph']
    if pa is None:
        _ph_filtered = _ph_time = None
    elif anchor:
        # Same nominal response as Nano alpha=.20 per 2 seconds, but elapsed-time
        # weighting prevents 500 ms retransmissions from speeding up the filter.
        if _ph_time is None or now - _ph_time > config.QUALITY_MAX_PACKET_GAP_SECONDS:
            _ph_filtered = pa
        else:
            alpha = 1 - math.pow(0.8, max(0, now - _ph_time) / 2.0)
            _ph_filtered += alpha * (pa - _ph_filtered)
        _ph_time = now
    if anchor:
        fields['pH'] = ('WAITING' if pa is None else
                        f"{6.86 + (_ph_filtered - anchor['adc686']) * 2.32 / (anchor['adc918'] - anchor['adc686']):.2f}")
    if _profile['turbidity'] is not None:
        fields['Turbidity'] = 'WAITING'
        if ta is not None:
            fields['Turbidity'] = next(row['label'] for row in _profile['turbidity'] if row['min'] <= ta <= row['max'])
    # Never turn disabled acquisition into a valid reading.
    for key, field in (('ph', 'pH'), ('turbidity', 'Turbidity')):
        if not sensor_control.enabled(key):
            fields[field] = 'DISABLED'
        elif not policy[key]:
            fields[field] = 'PAUSED'


def status():
    with config.sensor_lock:
        age = None if _live['receivedAt'] is None else max(0, time.monotonic() - _live['receivedAt'])
        fresh = age is not None and age <= config.QUALITY_MAX_PACKET_GAP_SECONDS
        return dict(profile=copy.deepcopy(_profile), defaults=copy.deepcopy(DEFAULT_RANGES),
                    categories=list(CATEGORIES), active=_maintenance,
                    live={'phADC': _live['phADC'] if fresh else None,
                          'turbidityADC': _live['turbidityADC'] if fresh else None,
                          'ageSeconds': age, 'fresh': fresh})
