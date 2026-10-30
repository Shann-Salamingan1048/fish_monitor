from concurrent.futures import ThreadPoolExecutor
import threading
import config
import admin_sessions
from flask_server import flask_app
from test_system import isolated


def login(client, pin=None):
    return client.post('/api/auth/login', json={'pin': config.ADMIN_PIN if pin is None else pin})


def test_two_admins_third_viewer_and_logout_frees_slot():
    first, second, third = [flask_app.test_client() for _ in range(3)]
    assert login(first).status_code == login(second).status_code == 200
    assert login(third).status_code == 409
    assert third.get('/api/auth/status').json['role'] == 'audience'
    assert third.post('/api/feeder', json={'action': 'pause'}).status_code == 403
    assert third.get('/api/sensors').status_code == 200
    assert first.get('/api/auth/status').json['adminSessions']['active'] == 2
    first.post('/api/auth/logout')
    assert login(third).status_code == 200
    assert second.get('/api/auth/status').json['role'] == 'admin'


def test_repeat_login_and_shared_cookie_use_one_slot():
    first, same_browser, second, third = [flask_app.test_client() for _ in range(4)]
    assert login(first).status_code == 200
    same_browser.set_cookie('session', first.get_cookie('session').value)
    assert login(same_browser).status_code == 200
    assert login(first).json['adminSessions']['active'] == 1
    assert login(second).status_code == 200
    assert login(third).status_code == 409
    same_browser.post('/api/auth/logout')
    # A different tab's older signed cookie must not restore the logged-out slot.
    assert first.get('/api/auth/status').json['role'] == 'audience'
    assert login(third).status_code == 200


def test_expiry_frees_slot_and_expired_cookie_stays_viewer(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(admin_sessions.time, 'monotonic', lambda: clock[0])
    first, second, third = [flask_app.test_client() for _ in range(3)]
    login(first); login(second)
    clock[0] += 200
    assert second.get('/api/auth/status').json['role'] == 'admin'
    clock[0] += 101
    assert login(third).status_code == 200
    assert first.post('/api/feeder', json={'action': 'pause'}).status_code == 403
    assert first.get('/api/auth/status').json['role'] == 'audience'
    assert second.get('/api/auth/status').json['role'] == 'admin'


def test_simultaneous_logins_never_exceed_two():
    barrier = threading.Barrier(4)
    def attempt(_):
        client = flask_app.test_client()
        barrier.wait()
        return login(client).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(attempt, range(4)))
    assert sorted(codes) == [200, 200, 409, 409]
    assert admin_sessions.status()['active'] == 2


def test_invalid_pin_does_not_reserve_and_restart_revokes_cookie():
    client = flask_app.test_client()
    assert login(client, 'wrong-pin').status_code == 401
    assert admin_sessions.status()['active'] == 0
    assert login(client).status_code == 200
    with admin_sessions._lock:
        admin_sessions._active.clear()  # A new application process has no slots.
    assert client.post('/api/feeder', json={'action': 'pause'}).status_code == 403
    with client.session_transaction() as session:
        session['role'] = 'admin'  # Legacy signed cookie alone is insufficient.
    assert client.get('/api/auth/status').json['role'] == 'audience'
