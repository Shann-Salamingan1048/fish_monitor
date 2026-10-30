"""Configurable active web admin sessions for AquaPi's single application process.

Tabs sharing a signed Flask cookie share one slot. Idle slots expire; an old
cookie cannot regain privileges after expiration, logout or process restart.
"""
import secrets
import threading
import time
from flask import session
import config

IDLE_TIMEOUT_SECONDS = 300
_lock = threading.RLock()
_active = {}


def _prune(now):
    for token, last_seen in list(_active.items()):
        if now - last_seen >= IDLE_TIMEOUT_SECONDS:
            del _active[token]
    # A lower limit takes effect now, retaining the most recently active sessions.
    excess = len(_active) - config.MAX_ADMIN_SESSIONS
    for token in sorted(_active, key=_active.get)[:max(0, excess)]:
        del _active[token]


def is_admin():
    with _lock:
        now = time.monotonic()
        _prune(now)
        token = session.get('admin_session_id')
        if session.get('role') == 'admin' and token in _active:
            _active[token] = now
            return True
        session.pop('admin_session_id', None)
        if session.get('role') == 'admin':
            session['role'] = 'audience'
        return False


def login():
    """Reserve atomically after the caller verifies the PIN."""
    with _lock:
        now = time.monotonic()
        _prune(now)
        if is_admin():
            return True
        if len(_active) >= config.MAX_ADMIN_SESSIONS:
            return False
        token = secrets.token_urlsafe(32)
        _active[token] = now
        session['admin_session_id'] = token
        session['role'] = 'admin'
        return True


def logout():
    with _lock:
        _active.pop(session.pop('admin_session_id', None), None)
        session['role'] = 'audience'


def status():
    with _lock:
        _prune(time.monotonic())
        return {'active': len(_active), 'max': config.MAX_ADMIN_SESSIONS,
                'idleTimeoutSeconds': IDLE_TIMEOUT_SECONDS}


def revoke_all():
    with _lock:
        _active.clear()


def enforce_limit():
    with _lock:
        _prune(time.monotonic())
