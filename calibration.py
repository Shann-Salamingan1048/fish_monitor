"""Calibration stored on the Pi; ADC rules synchronized to Mega RAM.

All runtime state is protected by config.sensor_lock. Persistence is one JSON
record so a failed save cannot leave half of a calibration installed.
"""
import copy
import json
import math
import time
import config

PH_BUFFERS = {'adc401': '4.01', 'adc686': '6.86', 'adc918': '9.18'}
PH_PAIRS = {'686_918': ('adc686', 'adc918'), '401_686': ('adc401', 'adc686'),
            '401_918': ('adc401', 'adc918')}

CATEGORIES = ('Very Cloudy', 'Cloudy', 'Slightly Cloudy', 'Clear', 'Very Clear')
DEFAULT_PH = {'points': 3, 'adc401': 917, 'adc686': 828.33, 'adc918': 758.11}
DEFAULT_RANGES = [
    {'min': 0, 'max': 399, 'label': 'Very Cloudy', 'category': 'Very Cloudy'},
    {'min': 400, 'max': 599, 'label': 'Cloudy', 'category': 'Cloudy'},
    {'min': 600, 'max': 1023, 'label': 'Clear', 'category': 'Clear'},
]
_profile = {'ph': DEFAULT_PH.copy(), 'turbidity': None}
_compiled_ranges = copy.deepcopy(DEFAULT_RANGES)
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
    if (not isinstance(payload, dict) or not {'ph', 'turbidity'} <= set(payload)
            or set(payload) - {'ph', 'turbidity', 'turbidityUnit', 'turbidityPoints'}):
        raise ValueError('Provide pH and turbidity settings.')
    ph = payload['ph']
    if ph is not None:
        if not isinstance(ph, dict):
            raise ValueError('Provide pH calibration values.')
        points = ph.get('points', 2)
        if isinstance(points, bool) or points not in (2, 3):
            raise ValueError('Choose 2-point or 3-point pH calibration.')
        pair = ph.get('pair', '686_918')
        if not isinstance(pair, str) or pair not in PH_PAIRS:
            raise ValueError('Choose a valid pH buffer pair.')
        keys = ('adc401', 'adc686', 'adc918') if points == 3 else PH_PAIRS[pair]
        allowed = set(keys) | {'points'} | ({'pair'} if points == 2 else set())
        if set(ph) - allowed or not set(keys) <= set(ph):
            raise ValueError('Enter ADC values only for the selected pH buffers.')
        values = {key: _number(ph[key], 'pH ' + PH_BUFFERS[key] + ' ADC', 1, 1022) for key in keys}
        for lower, upper in zip(keys, keys[1:]):
            if values[lower] - values[upper] < 20:
                raise ValueError(PH_BUFFERS[lower] + ' ADC must exceed ' + PH_BUFFERS[upper] + ' ADC by at least 20 counts.')
        # Preserve legacy two-/three-point profiles without requiring a migration.
        if 'points' in ph: values['points'] = points
        if 'pair' in ph: values['pair'] = pair
        ph = values
    unit = payload.get('turbidityUnit', 'ADC')
    if unit not in ('ADC', 'NTU'):
        raise ValueError('Choose ADC or NTU for turbidity ranges.')
    points = payload.get('turbidityPoints')
    if points is not None:
        if not isinstance(points, list) or not 2 <= len(points) <= 8:
            raise ValueError('Use 2 to 8 known-NTU reference points.')
        checked = []
        for point in points:
            if not isinstance(point, dict) or set(point) != {'adc', 'ntu'}:
                raise ValueError('Each NTU reference needs an ADC reading and known NTU value.')
            checked.append(dict(adc=_number(point['adc'], 'Reference ADC'),
                                ntu=_number(point['ntu'], 'Reference NTU', 0, 1000)))
        points = sorted(checked, key=lambda point: point['ntu'])
        for left, right in zip(points, points[1:]):
            if right['ntu'] <= left['ntu'] or left['adc'] - right['adc'] < 1:
                raise ValueError('NTU must increase as ADC decreases, with at least 1 ADC count between references.')
        if points[0]['adc'] - points[-1]['adc'] < 20:
            raise ValueError('Reference points must span at least 20 ADC counts.')
    if unit == 'NTU' and (points is None or payload['turbidity'] is None):
        raise ValueError('NTU mode requires reference calibration and custom clarity ranges.')
    rows = payload['turbidity']
    if rows is not None:
        if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
            raise ValueError('Use 1 to 20 turbidity ranges.')
        result, labels = [], set()
        reserved = {'waiting', 'paused', 'disabled', 'error', '--', 'unknown', 'nan'}
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'min', 'max', 'label', 'category'}:
                raise ValueError('Each range needs minimum, maximum, display name and category.')
            limit = 1023 if unit == 'ADC' else 1000
            low = _number(row['min'], f'Minimum {unit}', 0, limit)
            high = _number(row['max'], f'Maximum {unit}', 0, limit)
            if unit == 'ADC' and (int(low) != low or int(high) != high or low > high):
                raise ValueError('ADC ranges must use whole numbers with minimum ≤ maximum.')
            if unit == 'NTU' and low >= high:
                raise ValueError('NTU minimum must be less than maximum.')
            label = row['label']
            if not isinstance(label, str) or not 1 <= len(label.strip()) <= 32 or any(ord(c) < 32 for c in label):
                raise ValueError('Display names must contain 1 to 32 printable characters.')
            label = label.strip()
            if label.casefold() in labels or label.casefold() in reserved:
                raise ValueError('Use unique display names, excluding sensor status words.')
            labels.add(label.casefold())
            if row['category'] not in CATEGORIES:
                raise ValueError('Choose a standard clarity category for each range.')
            result.append(dict(min=int(low) if unit == 'ADC' else low,
                               max=int(high) if unit == 'ADC' else high,
                               label=label, category=row['category']))
        rows = sorted(result, key=lambda row: row['min'])
        next_value = 0
        for row in rows:
            if row['min'] != next_value:
                raise ValueError('Ranges must cover the full scale without gaps or overlaps. '
                                 + ('ADC: 0–1023 inclusive.' if unit == 'ADC' else
                                    'NTU: 0–1000; each maximum equals the next minimum.'))
            next_value = row['max'] + (1 if unit == 'ADC' else 0)
        if next_value != (1024 if unit == 'ADC' else 1000):
            raise ValueError('Ranges must end at ' + ('ADC 1023.' if unit == 'ADC' else 'NTU 1000.'))
    result = {'ph': ph, 'turbidity': rows}
    # Preserve old saved profiles and older clients without a migration.
    if 'turbidityUnit' in payload or 'turbidityPoints' in payload:
        result.update(turbidityUnit=unit, turbidityPoints=points)
    compile_adc_ranges(result)
    return result


def estimate_ntu(adc, points):
    """Piecewise linear interpolation only inside the measured reference span."""
    if adc is None or not points or not points[-1]['adc'] <= adc <= points[0]['adc']:
        return None
    for left, right in zip(points, points[1:]):
        if right['adc'] <= adc <= left['adc']:
            fraction = (left['adc'] - adc) / (left['adc'] - right['adc'])
            return left['ntu'] + fraction * (right['ntu'] - left['ntu'])
    return None


def compile_adc_ranges(profile):
    """Compile once on save/load; host labels and Mega rules share exact bins."""
    rows = profile['turbidity'] or DEFAULT_RANGES
    if profile.get('turbidityUnit', 'ADC') == 'ADC':
        return copy.deepcopy(rows)
    points, compiled = profile['turbidityPoints'], []
    for adc in range(1024):
        value = estimate_ntu(adc, points)
        # No invented NTU measurement outside references. For protection only,
        # use the extreme clarity row on that side of the calibration span.
        if value is None:
            row = rows[-1] if adc < points[-1]['adc'] else rows[0]
        else:
            row = next(row for row in rows if row['min'] <= value < row['max']
                       or value == 1000 == row['max'])
        if compiled and all(compiled[-1][key] == row[key] for key in ('label', 'category')):
            compiled[-1]['max'] = adc
        else:
            compiled.append(dict(min=adc, max=adc, label=row['label'], category=row['category']))
    if len(compiled) > 20:
        raise ValueError('NTU ranges generate too many Mega ranges (maximum 20).')
    return compiled


def effective_adc_ranges():
    with config.sensor_lock:
        return copy.deepcopy(_compiled_ranges)


def reset_readings():
    global _ph_filtered, _ph_time
    with config.sensor_lock:
        _ph_filtered = _ph_time = None
        _live.update(phADC=None, turbidityADC=None, receivedAt=None)


def initialize():
    global _profile, _maintenance, _compiled_ranges
    with config.sensor_lock:
        # Invalid stored calibration is an error, never silently reinterpret water.
        saved = config.get_db_value('sensor_calibration')
        _profile = validate(json.loads(saved)) if saved is not None else {'ph': DEFAULT_PH.copy(), 'turbidity': None}
        _compiled_ranges = compile_adc_ranges(_profile)
        _maintenance = config.get_db_value('calibration_active', '0') == '1'
        reset_readings()


def active():
    with config.sensor_lock:
        return _maintenance


def _clear_history():
    for key in ('ph', 'turbidity', 'turbidity_adc'):
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
    global _profile, _compiled_ranges
    proposed = validate(payload)
    compiled = compile_adc_ranges(proposed)
    with config.sensor_lock:
        if not _maintenance:
            raise ValueError('Start calibration before saving changes.')
        _persist('sensor_calibration', json.dumps(proposed, allow_nan=False))
        _profile = proposed
        _compiled_ranges = compiled
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
    if ta is not None and (int(ta) != ta or str(fields.get('Turbidity', '')).strip().upper()
                           in {'WAITING', 'PAUSED', 'DISABLED', 'ERROR', '--'}):
        ta = None
    _live.update(phADC=pa, turbidityADC=ta, receivedAt=now)
    if pa is None:
        _ph_filtered = _ph_time = None
    # The Mega reports the calibrated/filtered pH used by its local controller.
    if ta is not None:
        fields['Turbidity'] = next(row['label'] for row in _compiled_ranges if row['min'] <= ta <= row['max'])
    elif _profile['turbidity'] is not None:
        fields['Turbidity'] = 'WAITING'
    # Never turn disabled acquisition into a valid reading.
    for key, field in (('ph', 'pH'), ('turbidity', 'Turbidity')):
        if not sensor_control.enabled(key):
            fields[field] = 'DISABLED'
        elif not policy[key]:
            fields[field] = 'PAUSED'


def _ntu_reading(adc):
    points = _profile.get('turbidityPoints')
    ntu = estimate_ntu(adc, points)
    result = dict(turbidityNTU=ntu, turbidityNTUState='unavailable',
                  estimatedNTU=None, estimateState=None, turbidityVoltage=None)
    if adc is None:
        return result
    voltage = adc * 5.0 / 1023
    result['turbidityVoltage'] = voltage
    if ntu is not None:
        result['turbidityNTUState'] = 'calibrated'
        return result
    result['turbidityNTUState'] = 'out_of_range' if points else 'estimated'
    if voltage < 2.5:
        result.update(estimatedNTU=3000.0, estimateState='cloudy_limit')
    elif voltage > 4.2:
        result.update(estimatedNTU=0.0, estimateState='clear_limit')
    else:
        estimate = (-1120.4 * voltage + 5742.3) * voltage - 4352.9
        result.update(estimatedNTU=max(0.0, min(3000.0, estimate)), estimateState='curve')
    return result


def clarity_labels():
    """Display names from the active calibration, ordered from clearest to cloudiest."""
    rows = _profile['turbidity'] or DEFAULT_RANGES
    # Keep neighboring ranges in measurement order when they share a category.
    first = CATEGORIES.index(rows[0]['category'])
    last = CATEGORIES.index(rows[-1]['category'])
    if first < last or (first == last and _profile.get('turbidityUnit', 'ADC') == 'ADC'):
        rows = reversed(rows)
    ordered = sorted(rows, key=lambda row: CATEGORIES.index(row['category']), reverse=True)
    return [row['label'] for row in ordered]


def clarity_readout(reading):
    """Return the active range's display name, or an acquisition status."""
    text = str(reading).strip().upper()
    if text in {'PAUSED', 'DISABLED', 'WAITING', 'ERROR'}:
        return text
    received = _live['receivedAt']
    if _maintenance or received is None or time.monotonic() - received > config.QUALITY_MAX_PACKET_GAP_SECONDS:
        return 'WAITING'
    rows = _profile['turbidity'] or DEFAULT_RANGES
    for row in rows:
        if str(reading).strip().casefold() == row['label'].casefold():
            return row['label']
    if _profile['turbidity']:
        return 'WAITING'
    # Older uncalibrated firmware may report numeric NTU directly.
    try:
        ntu = float(text.removesuffix('NTU').strip())
    except ValueError:
        return 'WAITING'
    if not math.isfinite(ntu) or ntu < 0:
        return 'WAITING'
    if ntu >= config.TURBIDITY_CRITICAL_NTU:
        return 'Very Cloudy'
    return 'Cloudy' if ntu >= config.TURBIDITY_LIMIT_NTU else 'Clear'


def status():
    with config.sensor_lock:
        age = None if _live['receivedAt'] is None else max(0, time.monotonic() - _live['receivedAt'])
        fresh = age is not None and age <= config.QUALITY_MAX_PACKET_GAP_SECONDS
        import water_control
        policy = water_control.get_sensor_policy()
        adc = _live['turbidityADC'] if fresh and policy['turbidity'] else None
        return dict(profile=copy.deepcopy(_profile), defaults=copy.deepcopy(DEFAULT_RANGES),
                    categories=list(CATEGORIES), active=_maintenance,
                    live={'phADC': _live['phADC'] if fresh and policy['ph'] else None,
                          'turbidityADC': adc, **_ntu_reading(adc),
                          'ageSeconds': age, 'fresh': fresh})
