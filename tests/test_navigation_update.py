import threading
import queue
import importlib
import pytest
import config
import admin_credentials
from flask_server import flask_app
from gui_snapshot import SnapshotWorker
from test_system import isolated


def test_pin_change_persists_hash_and_revokes_both_sessions():
    first, second = flask_app.test_client(), flask_app.test_client()
    for client in (first, second):
        assert client.post('/api/auth/login', json={'pin': config.ADMIN_PIN}).status_code == 200
    admin_credentials.change(config.ADMIN_PIN, 'new-test-4567', 'new-test-4567')
    saved = config.get_db_value('admin_pin_hash', '')
    assert saved and saved != 'new-test-4567'
    importlib.reload(admin_credentials)
    assert admin_credentials.verify('new-test-4567')
    assert not admin_credentials.verify(config.ADMIN_PIN)
    for client in (first, second):
        assert client.get('/api/auth/status').json['role'] == 'audience'
        assert client.post('/api/feeder', json={'action': 'feed', 'cycles': 1}).status_code == 403
    assert first.post('/api/auth/login', json={'pin': config.ADMIN_PIN}).status_code == 401
    assert first.post('/api/auth/login', json={'pin': 'new-test-4567'}).status_code == 200


@pytest.mark.parametrize('current,new,confirm', [('wrong', '1234', '1234'), ('original', '1234', '9999'), ('original', '12', '12'), ('original', ' 1234', ' 1234')])
def test_invalid_pin_changes_do_not_replace_current(current, new, confirm):
    with pytest.raises(ValueError):
        admin_credentials.change(config.ADMIN_PIN if current == 'original' else current, new, confirm)
    assert admin_credentials.verify(config.ADMIN_PIN)
    assert config.get_db_value('admin_pin_hash', '') == ''


def test_slow_snapshot_does_not_block_request_and_coalesces_updates():
    entered, release = threading.Event(), threading.Event()
    threads = []
    calls = []
    def slow():
        threads.append(threading.current_thread())
        entered.set()
        assert release.wait(3)
        calls.append(len(calls) + 1)
        return calls[-1]
    worker = SnapshotWorker(slow)
    try:
        assert entered.wait(1)
        for _ in range(100): worker.request()
        assert worker.results.empty()
        assert threads[0] is not threading.current_thread()
        release.set()
        assert worker.results.get(timeout=2) == 1
        assert worker.results.get(timeout=2) == 2
        assert len(calls) == 2
    finally:
        release.set()
        worker.close()
        worker.thread.join(2)
    assert not worker.thread.is_alive()


@pytest.mark.parametrize('page', ['', 'feeding', 'water-changes', 'cooling-fan', 'sensors', 'camera', 'graphs', 'settings', 'access'])
def test_separate_pages_render_with_navigation(page):
    response = flask_app.test_client().get('/' + page)
    assert response.status_code == 200
    html = response.text
    assert 'aria-label="Main navigation"' in html
    assert '<title>AquaPi' in html
    if page not in ('feeding',): assert 'id="feeding-schedule-form"' not in html
    if page != 'settings': assert 'id="phCriticalHighLimit"' not in html
    assert 'Waiting for the Mega to confirm completion' not in html


def test_rounded_card_skips_unchanged_draw_and_repaints_accent():
    from types import SimpleNamespace
    from tkinter_gui import RoundedFrame
    draws = []
    card = SimpleNamespace(_bg='white', _border_color='gray', _left_accent=None,
                           _radius=14, _outline_width=1, _pad_x=10, _pad_y=8,
                           _draw_signature=None, winfo_width=lambda: 300,
                           winfo_height=lambda: 100, delete=lambda *a: None,
                           _round_rect=lambda *a, **k: draws.append(k),
                           tag_lower=lambda *a: None,
                           create_rectangle=lambda *a, **k: None,
                           inner=SimpleNamespace(place=lambda **k: None))
    RoundedFrame._redraw(card)
    for _ in range(50): RoundedFrame._redraw(card)
    assert len(draws) == 1
    card._left_accent='red'
    RoundedFrame._redraw(card)
    assert len(draws) == 2


def test_notification_change_updates_only_changed_card():
    from tkinter_gui import FocusedMonitorApp, LIGHT_THEME
    class Widget:
        def __init__(self): self.updates=0; self.inner=self
        def config(self, **kwargs): self.updates+=1
        def grid(self, **kwargs): pass
        def grid_remove(self): pass
        def itemconfig(self, *args, **kwargs): pass
        def _redraw(self): pass
    app = FocusedMonitorApp.__new__(FocusedMonitorApp)
    app.dark_mode=False
    app.temperature_unit='C'
    app._last_alert_signature=None
    app._desktop_controls={'disabled':[], 'allDisabled':False}
    app.accent_danger='red'; app.accent_warning='orange';app.accent_green='green';app.accent_prediction='purple'
    app.text_sub='gray'
    app._tint=lambda color:color
    app.status_badge=Widget();app.status_badge_label=Widget();app.empty_alert_card=Widget()
    app.alert_cards=[{key:Widget() for key in ('frame','title','message','meta','severity_badge','severity_label')} for _ in range(config.MAX_TK_ALERTS)]
    first={'sensor':'pH Level','severity':'critical','title':'pH is too acidic','message':'pH 5.9','value':5.9,'limit':6}
    second={'sensor':'Temperature','severity':'warning','title':'Warm water','message':'Temperature 31°C','value':31,'limit':30,'unit':'°C'}
    app._render_alerts([first,second])
    assert app.alert_cards[0]['title'].updates == app.alert_cards[1]['title'].updates == 1
    app._render_alerts([first,dict(second,value=32,message='Temperature 32°C')])
    assert app.alert_cards[0]['title'].updates == 1
    assert app.alert_cards[1]['title'].updates == 2
    app._render_alerts([])
    app._render_alerts([first])
    assert app.alert_cards[0]['title'].updates == 2


def test_water_and_fan_pages_own_their_controls():
    client = flask_app.test_client()
    assert client.get('/controls').location == '/water-changes'
    for path, present, absent in [
        ('/water-changes', 'water-schedule-form', 'fan-mode-actions'),
        ('/cooling-fan', 'fan-mode-actions', 'water-schedule-form'),
    ]:
        html = client.get(path).text
        assert f'id="{present}"' in html and f'id="{absent}"' not in html
        assert f'href="{path}" aria-current="page"' in html
        assert 'href="/controls"' not in html


def test_login_treats_sql_as_text_and_rejects_malformed_input():
    client = flask_app.test_client()
    chosen = "Aqua'Pi-27"
    admin_credentials.change(config.ADMIN_PIN, chosen, chosen)
    saved = config.get_db_value('admin_pin_hash')
    for body in [{"pin": "' OR 1=1 --"}, {"pin": "'; DROP TABLE app_state; --"},
                 {"pin": ["1234"]}, {"pin": 1234}, {"pin": "9" * 129}, [],
                 {"pin": chr(0xd800)}]:
        assert client.post('/api/auth/login', json=body).status_code == 401
        assert client.get('/api/auth/status').json['role'] == 'audience'
        assert client.post('/api/water-control', json={'action': 'stop_all'}).status_code == 403
    assert config.get_db_value('admin_pin_hash') == saved
    assert client.post('/api/auth/login', json={'pin': chosen}).status_code == 200


def test_login_denies_access_if_credentials_cannot_be_read(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'DB_FILE', str(tmp_path / 'missing' / 'state.db'))
    response = flask_app.test_client().post('/api/auth/login', json={'pin': config.ADMIN_PIN})
    assert response.status_code == 401
