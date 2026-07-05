"""Install/uninstall the ks82rgb daemon as a systemd --user service."""

import os
import subprocess

UNIT_NAME = "ks82rgb.service"
UNIT_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "systemd", "user")


def _launcher_path():
    """Absolute path to bin/ks82rgb in this checkout."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "bin", "ks82rgb")


def unit_text():
    return f"""[Unit]
Description=KS82-B RGB keyboard daemon
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
ExecStart={_launcher_path()} daemon
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""


def _systemctl(*args):
    return subprocess.run(["systemctl", "--user", *args],
                          capture_output=True, text=True)


def install(enable=True, start=True):
    os.makedirs(UNIT_DIR, exist_ok=True)
    path = os.path.join(UNIT_DIR, UNIT_NAME)
    with open(path, "w") as f:
        f.write(unit_text())
    _systemctl("daemon-reload")
    steps = [f"wrote {path}"]
    if enable:
        r = _systemctl("enable", UNIT_NAME)
        steps.append("enabled (starts at login)" if r.returncode == 0
                     else f"enable failed: {r.stderr.strip()}")
    if start:
        r = _systemctl("restart", UNIT_NAME)
        steps.append("started" if r.returncode == 0
                     else f"start failed: {r.stderr.strip()}")
    return steps


def uninstall():
    steps = []
    _systemctl("stop", UNIT_NAME)
    _systemctl("disable", UNIT_NAME)
    path = os.path.join(UNIT_DIR, UNIT_NAME)
    try:
        os.unlink(path)
        steps.append(f"removed {path}")
    except FileNotFoundError:
        steps.append("no unit file to remove")
    _systemctl("daemon-reload")
    return steps
