"""Shared fan override; requested mode and Mega-confirmed state stay distinct."""
import time
import config

_mode = 'AUTO'
_supported = False
_reported_mode = None
_on = None
_pending = 'NONE'
_reason = 'Waiting for MegaOutput'
_last_seen = 0.0
_last_sent = 0.0


def initialize():
    global _mode
    with config.sensor_lock:
        _mode = 'AUTO'  # Manual overrides last until changed or application restart.
        disconnected()


def disconnected():
    global _supported, _reported_mode, _on, _last_seen, _pending
    with config.sensor_lock:
        _supported, _reported_mode, _on = False, None, None
        _last_seen, _pending = 0.0, 'NONE'


def sync():
    global _last_sent
    with config.sensor_lock:
        if not _supported or not config.output_serial_ports:
            return False
        _last_sent = time.monotonic()
        return config.send_to_identified_output_serial_ports(f'MegaOutput,FANMODE:{_mode}\n')


def retry_sync():
    with config.sensor_lock:
        if _supported and _reported_mode != _mode and time.monotonic() - _last_sent >= 2:
            sync()


def handle_status(line):
    global _supported, _reported_mode, _on, _pending, _reason, _last_seen
    fields = dict(p.split(':', 1) for p in line.split(',') if ':' in p)
    with config.sensor_lock:
        if fields.get('READY') == '1':
            disconnected()
        if fields.get('FANCTRL') == 'V1':
            first = not _supported
            _supported = True
            if first:
                sync()
        if _supported and fields.get('FANMODE') in ('AUTO', 'ON', 'OFF') and fields.get('COOLER') in ('ON', 'OFF'):
            _reported_mode = fields['FANMODE']
            _on = fields['COOLER'] == 'ON'
            _pending = fields.get('FANPENDING', 'NONE')
            _reason = fields.get('REASON', '')
            _last_seen = time.monotonic()


def set_mode(mode):
    if config.shutting_down:
        raise ValueError('System is off. Restart the application to resume.')
    global _mode, _reported_mode
    if not isinstance(mode, str) or mode.upper() not in ('AUTO', 'ON', 'OFF'):
        raise ValueError('Choose Auto, On or Off for the fan.')
    with config.sensor_lock:
        if not get_status()['available']:
            raise ValueError('Fan unavailable: connect the MegaOutput with the updated fan firmware.')
        previous = _mode
        _mode = mode.upper()
        if not sync():
            _mode = previous
            raise ValueError('Fan command could not be sent. Check the MegaOutput connection.')
        _reported_mode = None  # Wait for firmware acknowledgement.
        return get_status()


def get_status():
    with config.sensor_lock:
        fresh = _on is not None and time.monotonic() - _last_seen <= 6
        available = bool(config.output_serial_ports) and _supported and fresh
        return {'mode': _mode, 'reportedMode': _reported_mode,
                'on': _on if available else None, 'available': available,
                'supported': _supported, 'confirmed': available and _reported_mode == _mode,
                'pending': _pending if available else 'NONE', 'reason': _reason,
                'confirmationSeconds': 5}


def stop_for_shutdown():
    global _mode
    with config.sensor_lock:
        _mode = 'OFF'
        sync()
