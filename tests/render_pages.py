"""Render isolated pages for frontend_sensor_test.cjs without live hardware."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from flask_server import flask_app
with TemporaryDirectory() as directory:
    config.DB_FILE = str(Path(directory) / 'render.db')
    config.init_database()
    client = flask_app.test_client()
    for name in ('index', 'feeding', 'controls', 'sensors', 'camera', 'graphs', 'settings', 'access'):
        response = client.get('/' if name == 'index' else '/' + name)
        assert response.status_code == 200
        Path('/tmp/fish-' + name + '.html').write_text(response.text)
print('Rendered 8 pages to /tmp/fish-*.html')
