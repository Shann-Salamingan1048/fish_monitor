"""Install AquaPi as a Raspberry Pi OS boot service from its permanent project folder."""
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile


def unit_text(project, user, python):
    def argument(value):
        return '"' + str(value).replace('%', '%%').replace('\\', '\\\\').replace('"', '\\"') + '"'

    directory = str(project).replace('%', '%%')
    if any(ord(char) < 32 for char in directory):
        raise ValueError("Use a project directory without control characters.")
    return f"""[Unit]
Description=AquaPi aquarium monitor
After=local-fs.target

[Service]
Type=simple
User={user}
WorkingDirectory={directory}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/etc/default/aquapi
ExecStart=:/usr/bin/env {argument(python)} {argument(project / 'main.py')} --headless
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def main():
    if os.getuid() == 0:
        sys.exit("Run python3 install_autostart.py as your normal Pi user. The installer uses sudo for the service.")
    project = Path(__file__).resolve().parent
    user = pwd.getpwuid(os.getuid()).pw_name
    python = project / '.venv/bin/python'
    if not python.is_file():
        python = Path(sys.executable)
    subprocess.run([python, '-c', 'import flask, werkzeug, serial'], check=True)
    with tempfile.TemporaryDirectory() as directory:
        unit = Path(directory) / 'aquapi.service'
        unit.write_text(unit_text(project, user, python))
        subprocess.run(['systemd-analyze', 'verify', str(unit)], check=True)
        subprocess.run(['sudo', 'install', '-m', '0644', str(unit),
                        '/etc/systemd/system/aquapi.service'], check=True)
    for args in (['daemon-reload'], ['enable', 'aquapi.service'], ['restart', 'aquapi.service']):
        subprocess.run(['sudo', 'systemctl', *args], check=True)
    print('AquaPi is enabled at boot. Check it with systemctl status aquapi --no-pager.')


if __name__ == '__main__':
    main()
