"""Open this installer in Thonny on the Raspberry Pi and click Run once."""
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import messagebox

PROJECT = Path(__file__).resolve().parent
SESSION_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
                "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP")


def checked(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or
                           "Setup was cancelled or the requested operation failed.")
    return result.stdout.strip()


def desktop_arg(path):
    value = str(path)
    if any(ord(c) < 32 for c in value):
        raise ValueError("Use a folder name without control characters.")
    for char in ('\\', '"', '$', chr(96)):
        value = value.replace(char, '\\' + char)
    return '"' + value.replace('\\', '\\\\').replace('%', '%%') + '"'


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        out.write(data)
    os.replace(out.name, path)


def launch():
    # systemd-run gets the real desktop environment from this login. A fixed
    # unit name also prevents duplicate copies from controlling the aquarium.
    args = ["systemd-run", "--user", "--collect", "--unit=aquapi-desktop",
            "--service-type=exec", "--working-directory=" + str(PROJECT),
            "--property=EnvironmentFile=-/etc/default/aquapi",
            "--property=Restart=on-failure", "--property=RestartSec=5"]
    args.extend("--setenv=" + k + "=" + os.environ[k] for k in SESSION_KEYS if os.environ.get(k))
    checked(args + [sys.executable, str(PROJECT / "main.py"), "--open-browser"])


def install():
    if os.getuid() == 0 or not (PROJECT / "main.py").is_file():
        raise ValueError("Place install_autostart.py beside your working AquaPi main.py, then run it as your usual Pi desktop user.")
    interpreter = PROJECT / ".venv/bin/python"
    if not interpreter.is_file():
        interpreter = Path(sys.executable)
    checked([interpreter, "-c", "import tkinter, flask, werkzeug, serial; tkinter.Tk().destroy()"])
    checked(["systemctl", "--user", "show-environment"])
    settings = Path("/etc/default/aquapi")
    if settings.exists() and not os.access(settings, os.R_OK):
        raise RuntimeError("Your Pi account needs read access to /etc/default/aquapi to use the existing settings.")
    desktop = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart/aquapi-session.desktop"
    entry = ("[Desktop Entry]\nType=Application\nName=AquaPi\nTerminal=false\nExec="
             + desktop_arg(interpreter) + " " + desktop_arg(__file__) + " --launch\n")
    previous = desktop.read_bytes() if desktop.exists() else None
    atomic_write(desktop, entry.encode())
    try:
        # All privileged operations use fixed commands. Delay switching away
        # from the running aquarium process until the user's next reboot.
        status = checked(["pkexec", "/bin/sh", "-ec", """
export SUDO_USER="$1" USER="$1"
/usr/bin/raspi-config nonint do_boot_behaviour B4
tunnel=$(systemctl show cloudflared.service -p LoadState --value)
if [ "$tunnel" = loaded ]; then systemctl enable cloudflared.service; fi
if systemctl is-enabled --quiet aquapi.service; then systemctl disable aquapi.service; fi
printf '%s' "$tunnel"
""", "aquapi-setup", pwd.getpwuid(os.getuid()).pw_name])
    except Exception:
        if previous is None:
            desktop.unlink(missing_ok=True)
        else:
            atomic_write(desktop, previous)
        raise
    return ("Setup complete. Restart the Pi when the pumps and feeder are idle. "
            "AquaPi and the local dashboard will open after desktop login.\n\n" +
            ("Your existing Cloudflare service is enabled for boot." if status.endswith("loaded")
             else "No installed Cloudflare Tunnel service was found. Local startup is ready."))


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--launch"]:
            launch()
        else:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("AquaPi startup", install(), parent=root)
    except (OSError, ValueError, RuntimeError) as error:
        messagebox.showerror("AquaPi startup", str(error))
