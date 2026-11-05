"""Persistent Admin PIN changes from the trusted local desktop."""
import sqlite3
import threading
import hmac
from werkzeug.security import check_password_hash, generate_password_hash
import config
import admin_sessions

lock = threading.RLock()


def verify(pin):
    if not isinstance(pin, str) or not 1 <= len(pin) <= 128:
        return False
    try:
        supplied = pin.encode('utf-8')
    except UnicodeEncodeError:
        return False
    with lock:
        # Only the application-owned key reaches SQLite. Compare the PIN in Python.
        try:
            with config.db_connect() as conn:
                row = conn.execute('SELECT value FROM app_state WHERE key = ?',
                                   ('admin_pin_hash',)).fetchone()
        except sqlite3.Error:
            return False
        if row is None:
            return hmac.compare_digest(supplied, config.ADMIN_PIN.encode())
        return bool(row[0]) and check_password_hash(row[0], pin)


def change(current, new, confirmation):
    if new != confirmation:
        raise ValueError('New PINs do not match.')
    if not isinstance(new, str) or not 4 <= len(new) <= 128 or new != new.strip():
        raise ValueError('Use 4–128 characters, without spaces at the beginning or end.')
    hashed = generate_password_hash(new)
    with lock:
        if not verify(current):
            raise ValueError('Current Admin PIN is incorrect.')
        config.set_db_value('admin_pin_hash', hashed)
        admin_sessions.revoke_all()
