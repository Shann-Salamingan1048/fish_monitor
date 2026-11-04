"""Manual pump requests and display state for the single Mega controller.

Automatic decisions are evaluated by AquaPi_Mega.ino and reported over USB.
The Pi never echoes those automatic decisions as new pump commands.
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


def reset_confirmation():
    with _lock:
        _quality_samples.clear()


def calibration_changed():
    """Stop quality correction immediately; preserve active level protection."""
    with _lock:
        reset_confirmation()
        _state['phSettlingRequired'] = True
        _state['phSettlingSince'] = None
        import mega_control
        mega_control.sync(force=True)




def _ph_settling_status(now):
    waiting = bool(_state.get('phSettlingRequired'))
    since = _state.get('phSettlingSince')
    # Countdowns end only when another packet confirms Normal and eligibility.
    remaining = max(0, math.ceil(config.PH_SETTLE_SECONDS - (now - since))) if since is not None else None
    return {'active': waiting and _state['mode'] != 'auto_extract_ph',
            'remainingSeconds': remaining, 'durationSeconds': config.PH_SETTLE_SECONDS,
            'waitingForNormal': waiting and since is None}








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
        # Temperature remains active in every pump mode.
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

    status["temperatureActive"] = sensor_control.enabled("temp")

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
    import water_schedule
    with _lock:
        return bool(
            water_schedule.busy() or _state["extractionOn"]
            or _state["insertionOn"]
        )


def get_sensor_policy():
    with _lock:
        mode = _state["sensorMode"]

    return {
        "mode": mode,
        "float": any(sensor_control.enabled(k) for k in sensor_control.FLOAT_KEYS),
        "temp": sensor_control.enabled('temp'),
        "ph": sensor_control.enabled('ph') and mode in {"NORMAL", "AUTO_QUALITY"},
        "turbidity": sensor_control.enabled('turbidity') and mode in {"NORMAL", "AUTO_QUALITY"},
    }


def sensor_settings_changed(changed):
    """Cancel an automatic action whose input was deliberately removed."""
    with _lock:
        reset_confirmation()
        # The updated sensor mask lets the Mega cancel affected local actions.


def restore_sensor_mode_to_hardware():
    """Re-send the current mode after the MegaInput reconnects/resets."""
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




def pause_for_connection_loss():
    """Latch pumps stopped across USB loss; operator explicitly resumes control."""
    with _lock:
        reset_confirmation()
        _send_pump('STOP_ALL')
        _set_state_locked('manual_idle', 'connection_lost', False, False,
                          manual_override=True)
        _state['levelCorrection'] = None
        _state['phSettlingRequired'] = True
        _state['phSettlingSince'] = None


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
            raise ValueError("MegaOutput disconnected; cannot start a pump")
        # Stop old motion before AUTO; ensure an idle stop also exits AUTO.
        if action == 'auto_resume':
            _send_pump('STOP_ALL')
        else:
            config.send_to_identified_output_serial_ports('MegaOutput,WATERMODE:MANUAL\n')
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

        if action == 'auto_resume':
            config.send_to_identified_output_serial_ports('MegaOutput,WATERMODE:AUTO\n')
        return get_status()


def accept_mega_status(fields):
    """Display hardware decisions without sending them back as manual commands."""
    allowed = {'idle','manual_idle','manual_extract','manual_insert','manual_both',
               'auto_extract_ph','auto_extract_turbidity','auto_extract_overflow','auto_insert',
               'scheduled_drain','scheduled_refill'}
    mode = fields.get('WATER')
    if mode not in allowed or fields.get('MODE') not in {'NORMAL','AUTO_QUALITY','FLOAT_ONLY'}:
        return
    with _lock:
        if config.shutting_down:
            return
        _state.update(mode=mode, reason='mega_local', extractionOn=fields.get('EXTRACT')=='1',
                      insertionOn=fields.get('INSERT')=='1', manualOverride=fields.get('MANUAL')=='1',
                      sensorMode=fields['MODE'], updatedAt=time.time(),
                      phSettlingRequired=fields.get('SETTLING')=='1')
        remaining = int(fields.get('REMAIN','-1'))
        _state['phSettlingSince'] = (time.monotonic() - max(0, config.PH_SETTLE_SECONDS-remaining)
                                    if remaining >= 0 else None)
        _state['startedAt'] = (_state['startedAt'] or time.time()) if is_pump_active() else None


def process_sensor_update(
    water_level,
    turbidity,
    ph_value,
):
    """Evaluate automatic water-control rules using the newest input packet."""
    with _lock:
        # Callers cannot bypass an operator override by supplying old values.
        if not sensor_control.all_floats_enabled():
            water_level = sensor_control.water_level()
        if not sensor_control.enabled('turbidity'):
            turbidity = 'DISABLED'
        if not sensor_control.enabled('ph'):
            ph_value = 'DISABLED'
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

        # The status frame reports hardware decisions; never echo them as commands.
        return get_status()
