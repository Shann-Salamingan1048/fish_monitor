"""Automatic and manual aquarium water-pump controller.

Confirmed Very Cloudy or critical pH starts both pumps. Warnings alone do not start correction. Enabled float
switches take immediate priority: overflow extracts only; Very Low starts
refilling until Normal. Low protects an already-active quality correction. Quality readings stay active during correction.
Turbidity correction continues through Slightly Cloudy until Clear/Very Clear.
Manual drain, refill or both run until stopped and bypass automatic rules.
Temperature pauses while pumping; feeding is postponed while either pump runs.
"""

import math
import time

import config
import sensor_control
import calibration


_lock = config.sensor_lock

_state = {
    "mode": "idle",
    "reason": "normal",
    "extractionOn": False,
    "insertionOn": False,
    "manualOverride": False,
    "sensorMode": "NORMAL",
    "startedAt": None,
    "updatedAt": time.time(),
    "lastWaterLevel": "--",
    "lastTurbidity": "--",
    "lastPh": "--",
    "phCondition": "unknown",
    "levelCorrection": None,
    "phSettlingRequired": False,
    "phSettlingSince": None,
}


_quality_samples = {}
_quality_limits = None


def reset_confirmation():
    with _lock:
        _quality_samples.clear()


def calibration_changed():
    """Stop quality correction immediately; preserve active level protection."""
    with _lock:
        reset_confirmation()
        _state['phSettlingRequired'] = True
        _state['phSettlingSince'] = None
        if _state['mode'] in {'auto_extract_ph', 'auto_extract_turbidity'}:
            level = _state.get('levelCorrection')
            if level == 'overflow':
                _set_state_locked('auto_extract_overflow', 'overflowing', True, False)
            elif level == 'low':
                _set_state_locked('auto_insert', 'low_water_level', False, True)
            else:
                _set_state_locked('idle', 'calibration', False, False)


def _update_ph_settling(level, now):
    if not _state.get('phSettlingRequired'):
        return False
    if (_state['extractionOn'] or _state['insertionOn'] or level != 'normal'
            or calibration.active()):
        _state['phSettlingSince'] = None
    elif _state.get('phSettlingSince') is None:
        _state['phSettlingSince'] = now
    elif now - _state['phSettlingSince'] >= config.PH_SETTLE_SECONDS:
        _state['phSettlingRequired'] = False
        _state['phSettlingSince'] = None
    return _state['phSettlingRequired']


def _ph_settling_status(now):
    waiting = bool(_state.get('phSettlingRequired'))
    since = _state.get('phSettlingSince')
    # Countdowns end only when another packet confirms Normal and eligibility.
    remaining = max(0, math.ceil(config.PH_SETTLE_SECONDS - (now - since))) if since is not None else None
    return {'active': waiting and _state['mode'] != 'auto_extract_ph',
            'remainingSeconds': remaining, 'durationSeconds': config.PH_SETTLE_SECONDS,
            'waitingForNormal': waiting and since is None}


def _quality_confirmed(key, condition, now):
    """A condition, not an identical numeric value, must persist across packets."""
    if condition is None:
        _quality_samples.pop(key, None)
        return False
    prior = _quality_samples.get(key)
    if (prior is None or prior['condition'] != condition or
            now - prior['last'] > config.QUALITY_MAX_PACKET_GAP_SECONDS):
        prior = {'condition': condition, 'since': now, 'last': now, 'elapsed': 0.0}
        _quality_samples[key] = prior
    prior['last'] = now
    prior['elapsed'] = now - prior['since']
    return prior['elapsed'] >= config.QUALITY_CONFIRM_SECONDS


def _normalize_level(value):
    text = str(value or "").strip().lower().replace("_", " ").replace("-", " ")

    if "overflow" in text:
        return "overflow"
    if "very low" in text:
        return "very_low"
    if "normal" in text:
        return "normal"
    if "low" in text:
        return "low"

    return "unknown"


def _normalize_turbidity(value):
    value = calibration.category_for_label(value)
    text = " ".join(
        str(value or "")
        .strip()
        .lower()
        .replace("_", " ")
        .split()
    )

    if text == "very cloudy":
        return "very_cloudy"
    if text == "cloudy":
        return "cloudy"
    if text == "slightly cloudy":
        return "slightly_cloudy"
    if text == "very clear":
        return "very_clear"
    if text == "clear":
        return "clear"

    # Legacy numeric turbidity packets use the same thresholds as alerts.
    try:
        number = float(text)
        if math.isfinite(number) and number >= 0:
            if number >= config.TURBIDITY_CRITICAL_NTU: return "very_cloudy"
            if number >= config.TURBIDITY_LIMIT_NTU: return "cloudy"
            return "clear"
    except (TypeError, ValueError):
        pass
    return "unknown"


def _parse_ph(value):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    # Practical validation only; configured limits decide whether it is safe.
    if number < 0.0 or number > 14.0:
        return None

    return number


def _ph_condition(value):
    ph = _parse_ph(value)

    if ph is None:
        return "unknown"

    if ph < float(config.PH_LOW_LIMIT):
        return "acidic"

    if ph > float(config.PH_HIGH_LIMIT):
        return "alkaline"

    return "safe"


def _critical_ph_condition(value):
    ph = _parse_ph(value)
    if ph is None: return None
    if ph <= config.PH_CRITICAL_LOW_LIMIT: return 'acidic'
    if ph >= config.PH_CRITICAL_HIGH_LIMIT: return 'alkaline'
    return None


def _send_pump(command):
    message = config.build_pump_serial_message(command)
    sent = config.send_to_identified_output_serial_ports(message)
    print(f"[WATER] {command} sent={sent}")
    return sent


def _send_sensor_mode(mode):
    mode = str(mode).upper()
    message = config.build_input_sensor_mode_message(mode)
    sent = config.send_to_input_serial_ports(message)
    print(f"[SENSORS] mode={mode} sent={sent}")
    return sent


def _set_sensor_mode_locked(mode):
    mode = str(mode).upper()

    if _state["sensorMode"] == mode:
        return

    if mode == "FLOAT_ONLY":
        reset_confirmation()
    _state["sensorMode"] = mode
    _send_sensor_mode(mode)


def _set_state_locked(
    mode,
    reason,
    extraction,
    insertion,
    manual_override=None,
):
    old_extract = bool(_state["extractionOn"])
    old_insert = bool(_state["insertionOn"])
    if (old_extract, old_insert) != (bool(extraction), bool(insertion)):
        # One command expresses the entire desired pair; single-pump commands
        # turn the other channel off before starting the requested channel.
        _send_pump(_pump_command(extraction, insertion))

    if mode in {"idle", "manual_idle", "manual_extract", "manual_insert", "manual_both"}:
        _state["levelCorrection"] = None

    if mode in {"auto_extract_ph", "auto_extract_turbidity"}:
        _state["qualitySensor"] = "ph" if mode == "auto_extract_ph" else "turbidity"
    else:
        _state["qualitySensor"] = None

    _state["mode"] = mode
    _state["reason"] = reason
    _state["extractionOn"] = bool(extraction)
    _state["insertionOn"] = bool(insertion)
    _state["updatedAt"] = time.time()

    if manual_override is not None:
        _state["manualOverride"] = bool(manual_override)

    if extraction or insertion:
        _state["phSettlingRequired"] = True
        _state["phSettlingSince"] = None
        if (
            _state["startedAt"] is None
            or (not old_extract and not old_insert)
        ):
            _state["startedAt"] = time.time()

        # Predictions from paused sensors must not remain displayed.
        config.clear_prediction_alert_cache()
    else:
        _state["startedAt"] = None

    if mode in {
        "auto_extract_turbidity",
        "auto_extract_ph",
    }:
        # Floats + turbidity + pH.
        # Temperature stays off while a water pump is running.
        _set_sensor_mode_locked("AUTO_QUALITY")

    elif extraction or insertion:
        # Overflow correction, insertion, and manual pumping need floats only.
        _set_sensor_mode_locked("FLOAT_ONLY")

    else:
        _set_sensor_mode_locked("NORMAL")


def get_status():
    with _lock:
        status = dict(_state)
        now = time.monotonic()
        status['phSettling'] = _ph_settling_status(now)
        status['calibrationActive'] = calibration.active()
        status['confirmationSeconds'] = config.QUALITY_CONFIRM_SECONDS
        status['pendingQuality'] = [
            {'sensor': key, 'condition': item['condition'],
             'seconds': round(item['elapsed'], 1)}
            for key, item in _quality_samples.items()
            if now - item['last'] <= config.QUALITY_MAX_PACKET_GAP_SECONDS
            and item['elapsed'] < config.QUALITY_CONFIRM_SECONDS
        ]

    status["pumpActive"] = bool(
        status["extractionOn"]
        or status["insertionOn"]
    )

    status["sensorsPaused"] = (
        status["sensorMode"] != "NORMAL"
    )

    status["temperatureActive"] = (
        status["sensorMode"] == "NORMAL"
    )

    status["phActive"] = (
        status["sensorMode"]
        in {"NORMAL", "AUTO_QUALITY"}
    )

    status["turbidityActive"] = (
        status["sensorMode"]
        in {"NORMAL", "AUTO_QUALITY"}
    )

    policy = get_sensor_policy()
    status.update(temperatureActive=policy["temp"], phActive=policy["ph"],
                  turbidityActive=policy["turbidity"], floatActive=policy["float"])
    status["outputConnected"] = bool(
        config.output_serial_ports
    )
    status["inputConnected"] = bool(
        config.input_serial_ports
    )

    return status


def is_pump_active():
    with _lock:
        return bool(
            _state["extractionOn"]
            or _state["insertionOn"]
        )


def get_sensor_policy():
    with _lock:
        mode = _state["sensorMode"]

    return {
        "mode": mode,
        "float": any(sensor_control.enabled(k) for k in sensor_control.FLOAT_KEYS),
        "temp": sensor_control.enabled('temp') and mode == "NORMAL",
        "ph": sensor_control.enabled('ph') and mode in {"NORMAL", "AUTO_QUALITY"},
        "turbidity": sensor_control.enabled('turbidity') and mode in {"NORMAL", "AUTO_QUALITY"},
    }


def sensor_settings_changed(changed):
    """Cancel an automatic action whose input was deliberately removed."""
    with _lock:
        reset_confirmation()
        mode = _state['mode']
        affected = (bool(set(changed) & set(sensor_control.FLOAT_KEYS)) or
                    (mode == 'auto_extract_ph' and 'ph' in changed) or
                    (mode == 'auto_extract_turbidity' and 'turbidity' in changed))
        if affected and not _state['manualOverride']:
            _set_state_locked('idle', 'sensor_settings_changed', False, False)


def restore_sensor_mode_to_hardware():
    """Re-send the current mode after the input Nano reconnects/resets."""
    with _lock:
        reset_confirmation()
        mode = _state["sensorMode"]
        return _send_sensor_mode(mode)


def _pump_command(extraction, insertion):
    if extraction and insertion:
        return "BOTH_ON"
    if extraction:
        return "EXTRACT_ON"
    if insertion:
        return "INSERT_ON"
    return "STOP_ALL"


def restore_pump_state_to_hardware():
    """Re-send the complete pump pair after the output Nano reconnects."""
    with _lock:
        return _send_pump(_pump_command(_state["extractionOn"], _state["insertionOn"]))


def stop_all_for_shutdown():
    with _lock:
        _send_pump("STOP_ALL")
        _set_state_locked(
            "idle",
            "shutdown",
            False,
            False,
            manual_override=False,
        )


def manual_action(action):
    """Apply an admin-only manual action. Flask enforces authentication."""
    if config.shutting_down:
        raise ValueError('System is off. Restart the application to resume.')
    action = str(action or "").strip().lower()

    with _lock:
        reset_confirmation()
        if action in {"extract_start", "insert_start", "both_start"} and not config.output_serial_ports:
            raise ValueError("Output Nano disconnected; cannot start a pump")
        if action == "both_start":
            _set_state_locked("manual_both", "manual", True, True, manual_override=True)

        elif action == "extract_start":
            _set_state_locked(
                "manual_extract",
                "manual",
                True,
                False,
                manual_override=True,
            )

        elif action == "extract_stop":
            _set_state_locked(
                "manual_insert" if _state["insertionOn"] else "manual_idle",
                "manual_stopped",
                False,
                _state["insertionOn"],
                manual_override=True,
            )

        elif action == "insert_start":
            _set_state_locked(
                "manual_insert",
                "manual",
                False,
                True,
                manual_override=True,
            )

        elif action == "insert_stop":
            _set_state_locked(
                "manual_extract" if _state["extractionOn"] else "manual_idle",
                "manual_stopped",
                _state["extractionOn"],
                False,
                manual_override=True,
            )

        elif action == "stop_all":
            _set_state_locked(
                "manual_idle",
                "manual_stopped",
                False,
                False,
                manual_override=True,
            )

        elif action == "auto_resume":
            _set_state_locked(
                "idle",
                "auto_resumed",
                False,
                False,
                manual_override=False,
            )

        else:
            raise ValueError(
                "Unknown water-control action"
            )

        return get_status()


def process_sensor_update(
    water_level,
    turbidity,
    ph_value,
):
    """Evaluate automatic water-control rules using the newest input packet."""
    global _quality_limits
    with _lock:
        # Callers cannot bypass an operator override by supplying old values.
        if not sensor_control.all_floats_enabled():
            water_level = sensor_control.water_level()
        if not sensor_control.enabled('turbidity'):
            turbidity = 'DISABLED'
        if not sensor_control.enabled('ph'):
            ph_value = 'DISABLED'
        level = _normalize_level(water_level)
        clarity = _normalize_turbidity(turbidity)
        ph_condition = _ph_condition(ph_value)
        _state["lastWaterLevel"] = str(
            water_level or "--"
        )
        _state["lastTurbidity"] = str(
            turbidity or "--"
        )
        _state["lastPh"] = str(
            ph_value or "--"
        )
        _state["phCondition"] = ph_condition
        _state["updatedAt"] = time.time()

        limits = (config.PH_LOW_LIMIT, config.PH_HIGH_LIMIT, config.PH_CRITICAL_LOW_LIMIT,
                  config.PH_CRITICAL_HIGH_LIMIT, config.TURBIDITY_LIMIT_NTU,
                  config.TURBIDITY_CRITICAL_NTU, config.QUALITY_CONFIRM_SECONDS)
        if _quality_limits != limits:
            reset_confirmation()
            _quality_limits = limits
        now = time.monotonic()
        settling = _update_ph_settling(level, now)
        if calibration.active() or _state['manualOverride'] or _state['sensorMode'] not in ('NORMAL', 'AUTO_QUALITY'):
            reset_confirmation()
            quality_ph = quality_clarity = 'unknown'
        else:
            ph_ready = _quality_confirmed('ph', None if settling else _critical_ph_condition(ph_value), now)
            turb_ready = _quality_confirmed('turbidity', 'very_cloudy' if clarity == 'very_cloudy' else None, now)
            quality_ph = ph_condition if ph_ready else 'unknown'
            quality_clarity = clarity if turb_ready else 'unknown'

        # Manual mode intentionally ignores all automatic stop conditions.
        if _state["manualOverride"]:
            return get_status()

        mode = _state["mode"]

        # Preserve a confirmed quality correction through level overrides.
        # Invalid/disabled quality data cannot keep its own correction running.
        turb_active = (not calibration.active() and mode == "auto_extract_turbidity" and
                       clarity in {"cloudy", "very_cloudy", "slightly_cloudy"})
        ph_active = (not calibration.active() and mode == "auto_extract_ph" and
                     ph_condition in {"acidic", "alkaline"})
        turb_active = turb_active or quality_clarity in {"cloudy", "very_cloudy"}
        ph_active = ph_active or quality_ph in {"acidic", "alkaline"}

        # Latch a level correction until Normal, including the intermediate
        # float state between the low and normal switches.
        level_correction = _state.get("levelCorrection")
        if level == "overflow":
            level_correction = "overflow"
        elif level == "very_low" or (level == "low" and (turb_active or ph_active)):
            # A Low warning cannot start idle refilling. It can prevent draining
            # during an already-confirmed critical quality correction.
            level_correction = "low"
        elif level == "low" and level_correction == "overflow":
            # A warning can stop unsafe draining without starting a new refill.
            level_correction = None
        elif level == "normal":
            level_correction = None
        elif (level_correction == "overflow" and
              not sensor_control.enabled("float_normal") and
              sensor_control.float_reading("float_upper") is False):
            level_correction = None
        _state["levelCorrection"] = level_correction

        if turb_active or ph_active:
            quality_mode = "auto_extract_turbidity" if turb_active else "auto_extract_ph"
            reason = "cloudy_water" if turb_active else "ph_too_" + ph_condition
            extraction = level_correction != "low"
            insertion = level_correction != "overflow"
            if level_correction:
                reason += "_" + level_correction + "_priority"
            _set_state_locked(quality_mode, reason, extraction, insertion)
        elif level_correction == "overflow":
            # Preserve the existing upper-stop fallback when Normal is disabled:
            # a refill stops at Upper; an independent overflow can drain until
            # Upper clears. Never infer a Normal signal from a disabled switch.
            if mode == "auto_insert" and not sensor_control.enabled("float_normal"):
                _set_state_locked("idle", "upper_level_stop", False, False)
            else:
                _set_state_locked("auto_extract_overflow", "overflowing", True, False)
        elif level_correction == "low":
            _set_state_locked("auto_insert", "low_water_level", False, True)
        else:
            _set_state_locked("idle", "normal", False, False)

        _update_ph_settling(level, now)
        return get_status()
