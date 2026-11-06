"""Presentation conversions only; stored readings and control thresholds are Celsius."""
import math
import re


def to_display(value, unit='C'):
    number = float(value)
    return number * 9 / 5 + 32 if unit == 'F' else number


def to_celsius(value, unit='C'):
    number = float(value)
    return (number - 32) * 5 / 9 if unit == 'F' else number


def format_reading(value, unit='C'):
    text = str(value or '--').strip()
    if text.upper() in {'PAUSED', 'DISABLED', 'WAITING', 'ERROR'}:
        return text.upper()
    try:
        number = to_display(value, unit)
        if math.isfinite(number):
            return f'{number:.1f} °{unit}'
    except (TypeError, ValueError):
        pass
    return f'-- °{unit}'


def convert_text(text, unit='C'):
    return re.sub(r'(-?\d+(?:\.\d+)?)\s*°C',
                  lambda m: f'{to_display(m.group(1), unit):.1f}°{unit}', str(text))
