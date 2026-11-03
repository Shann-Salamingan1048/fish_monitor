"""Render Developer option without hardware for the DOM regression test."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import calibration
from flask_server import flask_app

with tempfile.TemporaryDirectory() as directory:
    config.DB_FILE = str(Path(directory) / 'render.db')
    config.init_database()
    calibration.initialize()
    config.DEVELOPER_OPTIONS = True
    client = flask_app.test_client()
    client.post('/api/auth/login', json={'pin': config.ADMIN_PIN})
    response = client.get('/developer')
    assert response.status_code == 200
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/fish-developer.html')
    destination.write_text(response.text)
    print(destination)
