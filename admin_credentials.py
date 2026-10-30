"""Persistent Admin PIN changes from the trusted local desktop."""
import threading
import hmac
from werkzeug.security import check_password_hash, generate_password_hash
import config
import admin_sessions

lock = threading.RLock()


def verify(pin):
    with lock:
        saved = config.get_db_value('admin_pin_hash', '')
        if saved:
            return check_password_hash(saved, str(pin))
        return hmac.compare_digest(str(pin).encode(), config.ADMIN_PIN.encode())


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
