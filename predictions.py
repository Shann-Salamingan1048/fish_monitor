"""Backend sensor alerts and improved trend-prediction calculations."""

import math
import re
import statistics
import time

import config
import sensor_control
import calibration


def extract_number(value):
    if value is None:
        return None

    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return None

    try:
        number = float(match.group(0))
    except ValueError:
        return None

    return number if math.isfinite(number) else None


def get_sensor_numeric(sensor_key, value):
    """Convert displayed sensor values into numbers for limit alerts."""
    if sensor_key != "turbidity":
        return extract_number(value)

    value = calibration.category_for_label(value)
    # Prefer a real numeric turbidity value when one exists.
    numeric = extract_number(value)
    if numeric is not None:
        return numeric

    text = str(value).strip().lower() if value is not None else ""

    # Category thresholds are classification scores, not measured NTU.
    if text in {"very cloudy", "muddy", "dirty"}:
        return config.TURBIDITY_CRITICAL_NTU

    if any(
        word in text
        for word in (
            "very cloudy",
            "cloudy",
            "slightly cloudy",
            "muddy",
            "dirty",
        )
    ):
        return config.TURBIDITY_LIMIT_NTU

    if any(
        word in text
        for word in (
            "clear",
            "very clear",
            "clean",
        )
    ):
        return 0.0

    return None


def get_prediction_numeric(sensor_key, value):
    """Predictions use only actual numeric measurements."""
    if sensor_key == 'turbidity' and calibration.is_custom_label(value):
        return None
    return extract_number(value)


def format_float_status(value):
    """Normalize the Nano's water-level states."""
    if value is None or not str(value).strip() or str(value).strip() == "--":
        return "--"

    raw = str(value).strip()
    text = raw.lower().replace("_", " ").replace("-", " ").strip()

    if "overflow" in text or ("upper" in text and "on" in text):
        return "Water Level is Overflowing"

    if "very low" in text:
        return "Water Level is Very Low"

    if "normal" in text:
        return "Water Level is Normal"

    if text in {"low", "water level is low"} or "water level is low" in text:
        return "Water Level is Low"

    if text in {
        "on",
        "1",
        "true",
        "yes",
        "high",
        "full",
        "detected",
        "water detected",
        "float on",
    }:
        return "Water Level Detected"

    if text in {
        "off",
        "0",
        "false",
        "no",
        "empty",
        "not detected",
        "water not detected",
        "float off",
    }:
        return "Water Level is Very Low"

    return raw


def record_sensor_history_locked(temp_value, turbidity_value, ph_value):
    """Record numeric sensor history. config.sensor_lock must already be held."""
    now = time.time()

    for key, raw_value in (
        ("temp", temp_value),
        ("turbidity", turbidity_value),
        ("ph", ph_value),
    ):
        value = get_prediction_numeric(key, raw_value)

        if value is None:
            continue

        if (
            now - config.last_history_sample_at[key]
            < config.HISTORY_SAMPLE_INTERVAL
        ):
            continue

        config.sensor_history[key].append((now, value))
        config.last_history_sample_at[key] = now

        oldest_allowed = now - config.HISTORY_KEEP_SECONDS

        config.sensor_history[key] = [
            sample
            for sample in config.sensor_history[key]
            if sample[0] >= oldest_allowed
        ]


def format_duration(seconds):
    seconds = max(1, int(round(seconds)))

    if seconds < 60:
        return f"{seconds} sec"

    if seconds < 3600:
        return f"{seconds / 60.0:.1f} min"

    return f"{seconds / 3600.0:.1f} hr"


def _median_smooth(samples, window_size=3):
    """Trailing median filter to suppress isolated sensor spikes."""
    if not samples:
        return []

    smoothed = []

    for index, (timestamp, _) in enumerate(samples):
        start = max(0, index - window_size + 1)

        values = [
            value
            for _, value in samples[start:index + 1]
        ]

        smoothed.append(
            (timestamp, statistics.median(values))
        )

    return smoothed


def _linear_regression(samples):
    """Return slope per second, intercept and R-squared."""
    if len(samples) < 2:
        return None

    start_time = samples[0][0]

    x_values = [
        timestamp - start_time
        for timestamp, _ in samples
    ]

    y_values = [
        value
        for _, value in samples
    ]

    count = len(samples)
    mean_x = sum(x_values) / count
    mean_y = sum(y_values) / count

    denominator = sum(
        (x - mean_x) ** 2
        for x in x_values
    )

    if denominator <= 0:
        return None

    numerator = sum(
        (x - mean_x) * (y - mean_y)
        for x, y in zip(x_values, y_values)
    )

    slope = numerator / denominator
    intercept = mean_y - slope * mean_x

    total_variance = sum(
        (y - mean_y) ** 2
        for y in y_values
    )

    residual_variance = sum(
        (
            y -
            (intercept + slope * x)
        ) ** 2
        for x, y in zip(x_values, y_values)
    )

    if total_variance <= 1e-12:
        r_squared = 0.0
    else:
        r_squared = max(
            0.0,
            min(
                1.0,
                1.0 - residual_variance / total_variance,
            ),
        )

    return slope, intercept, r_squared


def _direction_consistency(samples, direction, tolerance):
    if len(samples) < 2:
        return 0.0

    good = 0
    total = 0

    for index in range(1, len(samples)):
        difference = samples[index][1] - samples[index - 1][1]
        directional_change = difference * direction

        total += 1

        # Tiny reversals inside the configured noise band are accepted.
        if directional_change >= -tolerance:
            good += 1

    return good / total if total else 0.0


def _prediction(history, key, name, unit, safe, critical, danger, trend):
    now = time.time()

    trend_window = max(
        1.0,
        float(config.TREND_WINDOW_SECONDS),
    )

    samples = [
        sample
        for sample in history.get(key, [])
        if now - sample[0] <= trend_window + 5.0
    ]

    # With the default 3-second sampling interval this requires several
    # readings instead of trusting only a few points.
    minimum_samples = 6

    if len(samples) < minimum_samples:
        return None

    span = samples[-1][0] - samples[0][0]

    if span < trend_window:
        return None

    smoothed = _median_smooth(
        samples,
        window_size=3,
    )

    regression = _linear_regression(smoothed)

    if regression is None:
        return None

    slope, _, r_squared = regression

    direction = 1 if trend == "rising" else -1
    directional_slope = slope * direction

    minimum_slope = config.MIN_RISE_PER_SECOND.get(key, 0.0)

    if directional_slope < minimum_slope:
        return None

    minimum_r_squared = 0.45 if key == "turbidity" else 0.55

    if r_squared < minimum_r_squared:
        return None

    tolerance = config.NOISE_TOLERANCE.get(key, 0.0)

    consistency = _direction_consistency(
        smoothed,
        direction,
        tolerance,
    )

    if consistency < 0.70:
        return None

    current = statistics.median(
        [value for _, value in smoothed[-3:]]
    )

    if trend == "rising":
        if current >= critical:
            return None
        target = critical if current >= safe else safe
    else:
        if current <= critical:
            return None
        target = critical if current <= safe else safe

    remaining = (target - current) * direction

    if remaining <= 0:
        return None

    eta = remaining / directional_slope

    if not math.isfinite(eta) or eta <= 0:
        return None

    # Avoid presenting long extrapolations as useful near-term predictions.
    if eta > 30 * 60:
        return None

    if target == critical:
        effect = danger
    else:
        effect = (
            "exceed its configured limit"
            if trend == "rising"
            else "fall below its configured limit"
        )

    verb = "rising" if trend == "rising" else "dropping"
    confidence_percent = int(round(r_squared * 100))

    return {
        "sensor": name,
        "type": "prediction",
        "severity": "prediction",
        "trend": trend,
        "title": f"{name} {trend} trend detected",
        "message": (
            f"{name} has shown a consistent {verb} trend for about "
            f"{format_duration(span)}. If this trend continues, it may reach "
            f"{target:g}{unit} within about {format_duration(eta)}. "
            f"Trend confidence: {confidence_percent}%."
        ),
        "short_message": (
            f"If this trend continues, {name.lower()} may {effect} "
            f"within about {format_duration(eta)}."
        ),
        "value": current,
        "unit": unit,
        "limit": target,
        "eta_seconds": eta,
        "trend_rate_per_second": slope,
        "confidence": r_squared,
        "direction_consistency": consistency,
    }


def _add_limit(
    alerts,
    sensor,
    title,
    message,
    value,
    unit,
    limit,
    severity,
):
    alerts.append(
        {
            "sensor": sensor,
            "type": "limit",
            "severity": severity,
            "title": title,
            "message": message,
            "value": value,
            "unit": unit,
            "limit": limit,
        }
    )


def _active_predictions(candidates):
    now = time.time()

    duration = max(
        1.0,
        float(config.PREDICTION_ALERT_DURATION_SECONDS),
    )

    with config.prediction_lock:
        expired_keys = [
            key
            for key, stored in config.active_prediction_alerts.items()
            if stored["expires_at"] <= now
        ]

        for key in expired_keys:
            del config.active_prediction_alerts[key]

        for alert in candidates:
            if alert is None:
                continue

            key = (
                alert["sensor"].lower().replace(" ", "_")
                + ":"
                + alert["trend"]
            )

            stored = alert.copy()

            stored.update(
                {
                    "created_at": now,
                    "expires_at": now + duration,
                    "lasts_seconds": duration,
                    "prediction_delay_seconds": config.PREDICTION_DELAY_SECONDS,
                }
            )

            # Refresh active prediction so its ETA follows the current trend.
            config.active_prediction_alerts[key] = {
                "expires_at": stored["expires_at"],
                "alert": stored,
            }

        return [
            stored["alert"]
            for stored in config.active_prediction_alerts.values()
        ]


def build_sensor_alerts(sensor_snapshot, history_snapshot):
    with config.sensor_lock:
        return _build_sensor_alerts_locked(sensor_snapshot, history_snapshot)


def _build_sensor_alerts_locked(sensor_snapshot, history_snapshot):
    """Return limit alerts and trend predictions."""
    sensor_snapshot = dict(sensor_snapshot)
    history_snapshot = dict(history_snapshot)
    for key in ('temp', 'ph', 'turbidity'):
        if not sensor_control.enabled(key) or (calibration.active() and key in ('ph', 'turbidity')):
            sensor_snapshot[key] = 'DISABLED'
            history_snapshot[key] = []
    if not sensor_control.all_floats_enabled():
        sensor_snapshot['float_stat'] = sensor_control.water_level()
    alerts = []

    temp = get_sensor_numeric(
        "temp",
        sensor_snapshot.get("temp"),
    )

    turbidity = get_sensor_numeric(
        "turbidity",
        sensor_snapshot.get("turbidity"),
    )

    ph = get_sensor_numeric(
        "ph",
        sensor_snapshot.get("ph"),
    )

    # ------------------------- Water level -------------------------

    water_level = format_float_status(
        sensor_snapshot.get("float_stat")
    )

    water_text = str(water_level).lower()

    if "overflow" in water_text:
        alerts.append(
            {
                "sensor": "Water Level",
                "type": "limit",
                "severity": "critical",
                "title": "Water Level: OVERFLOWING",
                "message": (
                    "Critical: the water is above the normal level. "
                    "Check the tank now."
                ),
                "short_message": (
                    "Critical: water is overflowing. Check the tank now."
                ),
                "value": water_level,
                "unit": "",
                "limit": "Normal",
            }
        )

    elif "very low" in water_text:
        alerts.append(
            {
                "sensor": "Water Level",
                "type": "limit",
                "severity": "critical",
                "title": "Water Level: VERY LOW",
                "message": (
                    "Critical: the water is very low. Refill the tank soon."
                ),
                "short_message": (
                    "Critical: water is very low. Refill the tank soon."
                ),
                "value": water_level,
                "unit": "",
                "limit": "Normal",
            }
        )

    elif "low" in water_text and "normal" not in water_text:
        alerts.append(
            {
                "sensor": "Water Level",
                "type": "limit",
                "severity": "warning",
                "title": "Water Level: LOW",
                "message": (
                    "Warning: the water level is low. "
                    "Check if the tank needs more water."
                ),
                "short_message": (
                    "Warning: water level is low. Check the tank."
                ),
                "value": water_level,
                "unit": "",
                "limit": "Normal",
            }
        )

    # ------------------------- Temperature -------------------------

    if temp is not None and temp >= config.TEMP_LIMIT_C:
        severity = (
            "critical"
            if temp >= config.TEMP_OVERHEAT_LIMIT_C
            else "warning"
        )

        _add_limit(
            alerts,
            "Temperature",
            "Temperature is above its limit",
            (
                f"Current temperature is {temp:g}°C. "
                f"The configured limit is {config.TEMP_LIMIT_C:g}°C."
            ),
            temp,
            "°C",
            config.TEMP_LIMIT_C,
            severity,
        )

    # ------------------------- Turbidity -------------------------

    if (
        turbidity is not None
        and turbidity >= config.TURBIDITY_LIMIT_NTU
    ):
        severity = (
            "critical"
            if turbidity >= config.TURBIDITY_CRITICAL_NTU
            else "warning"
        )

        category = str(sensor_snapshot.get("turbidity", "")).strip()
        categorical = calibration.is_custom_label(category) or category.lower() in {"very cloudy", "cloudy", "slightly cloudy", "muddy", "dirty"}
        _add_limit(
            alerts, "Turbidity", "Water clarity needs attention",
            (f"Water clarity is {category}." if categorical else
             f"Current turbidity reading is {turbidity:g} NTU."),
            category if categorical else turbidity,
            "" if categorical else " NTU",
            "Clear" if categorical else config.TURBIDITY_LIMIT_NTU,
            severity,
        )

    # ------------------------- pH -------------------------

    if ph is not None and ph >= config.PH_HIGH_LIMIT:
        severity = (
            "critical"
            if ph >= config.PH_CRITICAL_HIGH_LIMIT
            else "warning"
        )

        _add_limit(
            alerts,
            "pH Level",
            "pH is too alkaline",
            (
                f"Current pH is {ph:g}. "
                f"{severity.title()} upper limit: "
                f"{config.PH_CRITICAL_HIGH_LIMIT if severity == 'critical' else config.PH_HIGH_LIMIT:g}."
            ),
            ph,
            " pH",
            config.PH_CRITICAL_HIGH_LIMIT if severity == 'critical' else config.PH_HIGH_LIMIT,
            severity,
        )

    elif ph is not None and ph <= config.PH_LOW_LIMIT:
        severity = (
            "critical"
            if ph <= config.PH_CRITICAL_LOW_LIMIT
            else "warning"
        )

        _add_limit(
            alerts,
            "pH Level",
            "pH is too acidic",
            (
                f"Current pH is {ph:g}. "
                f"{severity.title()} lower limit: "
                f"{config.PH_CRITICAL_LOW_LIMIT if severity == 'critical' else config.PH_LOW_LIMIT:g}."
            ),
            ph,
            " pH",
            config.PH_CRITICAL_LOW_LIMIT if severity == 'critical' else config.PH_LOW_LIMIT,
            severity,
        )

    # ------------------------- Predictions -------------------------

    candidates = [
        _prediction(
            history_snapshot,
            "temp",
            "Temperature",
            "°C",
            config.TEMP_LIMIT_C,
            config.TEMP_OVERHEAT_LIMIT_C,
            "overheat",
            "rising",
        ),
        _prediction(
            history_snapshot,
            "turbidity",
            "Turbidity",
            " NTU",
            config.TURBIDITY_LIMIT_NTU,
            config.TURBIDITY_CRITICAL_NTU,
            "become too cloudy",
            "rising",
        ),
        _prediction(
            history_snapshot,
            "ph",
            "pH Level",
            " pH",
            config.PH_HIGH_LIMIT,
            config.PH_CRITICAL_HIGH_LIMIT,
            "become too alkaline",
            "rising",
        ),
        _prediction(
            history_snapshot,
            "ph",
            "pH Level",
            " pH",
            config.PH_LOW_LIMIT,
            config.PH_CRITICAL_LOW_LIMIT,
            "become too acidic",
            "falling",
        ),
    ]

    alerts.extend(
        _active_predictions(candidates)
    )

    return sensor_control.filter_alerts(alerts)
