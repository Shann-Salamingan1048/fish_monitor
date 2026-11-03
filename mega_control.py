"""Synchronize settings; the Mega evaluates automatic actuator rules locally."""
import json
import time
import zlib
import config
import calibration
import water_control

_ack = None
_last_send = 0.0
_revision = None
_last_check = 0.0


def reset():
    global _ack, _last_send, _revision, _last_check
    _ack = _revision = None
    _last_send = _last_check = 0.0


def commands():
    profile = calibration.status()['profile']
    ph = profile['ph'] or {'adc686': 0, 'adc918': 0}
    rows = calibration.effective_adc_ranges()
    settings = dict(ph=ph, rows=rows, low=config.PH_CRITICAL_LOW_LIMIT,
                    high=config.PH_CRITICAL_HIGH_LIMIT, wl=config.PH_LOW_LIMIT,
                    wh=config.PH_HIGH_LIMIT, fan=config.TEMP_OVERHEAT_LIMIT_C,
                    confirm=round(config.QUALITY_CONFIRM_SECONDS*1000),
                    settle=round(config.PH_SETTLE_SECONDS*1000), maintenance=calibration.active())
    revision = zlib.crc32(json.dumps(settings,sort_keys=True).encode())
    first = (f"MegaConfig,BEGIN:{revision},PL:{settings['low']:g},PH:{settings['high']:g},"
             f"WL:{settings['wl']:g},WH:{settings['wh']:g},A6:{ph['adc686']:.4f},A9:{ph['adc918']:.4f},"
             f"FAN:{settings['fan']:g},CONF:{settings['confirm']},SETTLE:{settings['settle']},"
             f"MAINT:{int(settings['maintenance'])},ROWS:{len(rows)}\n")
    messages = [first]
    for i,row in enumerate(rows):
        category = calibration.CATEGORIES.index(row['category'])
        messages.append(f"MegaConfig,ROW:{revision},I:{i},MIN:{row['min']},MAX:{row['max']},CAT:{category}\n")
    messages.append(f'MegaConfig,COMMIT:{revision}\n')
    return revision, ''.join(messages)


def sync(force=False):
    global _last_send, _revision, _last_check
    if config.shutting_down or not config.output_serial_ports:
        return False
    with config.sensor_lock:
        now = time.monotonic()
        if not force and now - _last_check < 0.5:
            return _ack == _revision and _ack is not None
        _last_check = now
        revision, payload = commands()
        if not force and revision == _ack:
            return True
        if not force and revision == _revision and now-_last_send < 2:
            return False
        _revision, _last_send = revision, now
        return config.send_to_identified_output_serial_ports(payload)


def handle_status(line):
    global _ack
    fields = dict(p.split(':',1) for p in line.split(',') if ':' in p)
    import water_schedule
    water_schedule.handle_status(fields)
    if 'CFG' in fields:
        try: _ack = int(fields['CFG'])
        except ValueError: pass
    if 'WATER' in fields:
        water_control.accept_mega_status(fields)
