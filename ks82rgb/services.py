"""Background services: daemon-hosted workers that react to the outside world
and push overlays/state (as opposed to Sources, which render the base layer).

A Service is given a ServiceContext with the compositor and a ``now()`` clock in
the daemon's render timebase.  The notification listener is the first one; MQTT
(Milestone 3) will register here too.
"""

import shutil
import subprocess
import threading

from .overlays import PulseOverlay

_SERVICES = {}


class ServiceContext:
    def __init__(self, compositor, now, command=None, subscribe=None, status=None):
        self.compositor = compositor
        self.now = now                      # callable -> current render time
        self.command = command              # command(dict) -> resp; drive daemon
        self.subscribe = subscribe          # subscribe(cb): cb() on state change
        self.status = status                # status() -> current status dict


class Service:
    name = "service"

    def available(self):
        return True

    def start(self, ctx):
        ...

    def stop(self):
        ...


def register_service(cls):
    _SERVICES[cls.name] = cls
    return cls


def names():
    return sorted(_SERVICES)


def create(name):
    return _SERVICES[name]()


# ---------------------------------------------------------- notifications -----
@register_service
class NotificationService(Service):
    """Pulse the keyboard on every desktop notification (via dbus-monitor)."""

    name = "notifications"

    def __init__(self, color=(120, 180, 255)):
        self.color = color
        self._ctx = None
        self._proc = None
        self._stop = threading.Event()

    def available(self):
        return bool(shutil.which("dbus-monitor"))

    def start(self, ctx):
        self._ctx = ctx
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        cmd = ["dbus-monitor", "--session",
               "interface='org.freedesktop.Notifications',member='Notify'"]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except Exception as e:
            print(f"[ks82rgb] notifications: dbus-monitor failed: {e}")
            return
        for line in self._proc.stdout:
            if self._stop.is_set():
                break
            # each Notify method call begins a new "method call ... member=Notify"
            if "member=Notify" in line:
                self._ctx.compositor.add_overlay(
                    PulseOverlay(self._ctx.now(), color=self.color))

    def stop(self):
        self._stop.set()
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass


# Register the MQTT service (imports paho lazily, so this import is cheap/safe).
from . import mqtt_service  # noqa: E402,F401
