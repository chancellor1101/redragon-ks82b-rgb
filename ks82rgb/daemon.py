"""The ks82rgb daemon: owns the keyboard, runs the render loop, serves control.

Runs as a systemd --user service (see service.py) or in the foreground via
``ks82rgb daemon``.  Renders the compositor at a fixed FPS, but skips re-sending
identical frames so static modes cost almost nothing.  Survives keyboard replug
by transparently reopening the device.
"""

import json
import os
import signal
import threading
import time

from . import ipc, services, sources
from .compositor import Compositor
from .controller import DeviceError, Keyboard
from .overlays import PulseOverlay

FPS = 30
CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "ks82rgb")
STATE_PATH = os.path.join(CONFIG_DIR, "state.json")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
PLUGIN_DIR = os.path.join(CONFIG_DIR, "plugins")

DEFAULT_SERVICES = {"notifications": True}   # enabled unless config says otherwise


class Daemon:
    def __init__(self):
        self.comp = Compositor()
        self.kb = None
        self._connected = False
        self._stop = threading.Event()
        self._paused = threading.Event()   # set = don't drive LEDs (external tool)
        self._t0 = None
        self._base_spec = {"name": "solid", "params": {"color": [0, 0, 0]}}
        self._services = {}                # name -> running Service instance
        self._state_listeners = []         # called on any mode/brightness change

    # ------------------------------------------------------------ lifecycle --
    def run(self):
        os.makedirs(PLUGIN_DIR, exist_ok=True)
        loaded = sources.load_plugins(PLUGIN_DIR)
        if loaded:
            print(f"[ks82rgb] loaded plugins: {', '.join(loaded)}")
        self._t0 = time.monotonic()          # render/service timebase
        self._load_state()
        self._start_enabled_services()

        srv, path = ipc.make_server()
        print(f"[ks82rgb] daemon up: {path}")
        threading.Thread(
            target=ipc.serve_forever,
            args=(srv, self._handle, self._stop.is_set),
            daemon=True).start()

        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *a: self._stop.set())

        self._render_loop()

        for svc in list(self._services.values()):
            svc.stop()
        try:
            srv.close()
            os.unlink(path)
        except OSError:
            pass
        if self.kb:
            self.kb.close()
        print("[ks82rgb] daemon stopped")

    def _now(self):
        return (time.monotonic() - self._t0) if self._t0 else 0.0

    def _ensure_device(self):
        if self._connected:
            return True
        try:
            self.kb = Keyboard()
            self.kb.open()
            self._connected = True
            print(f"[ks82rgb] keyboard connected: {self.kb.node}")
        except DeviceError:
            self._connected = False
        return self._connected

    def _render_loop(self):
        interval = 1.0 / FPS
        last_frame = None
        next_tick = time.monotonic()
        while not self._stop.is_set():
            if self._paused.is_set():         # external tool (calibrate) has it
                time.sleep(0.1)
                last_frame = None
                continue

            if not self._ensure_device():
                time.sleep(1.0)               # keyboard unplugged; poll for it
                last_frame = None
                continue

            t = time.monotonic() - self._t0
            frame = self.comp.render(t)
            if frame != last_frame:
                try:
                    self.kb.send(frame)
                    last_frame = frame
                except DeviceError:
                    self._connected = False   # replug / lost; reconnect next loop
                    last_frame = None
                    continue

            next_tick += interval
            sleep = next_tick - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_tick = time.monotonic()   # fell behind; resync

    # -------------------------------------------------------------- commands --
    def _set_base(self, name, params):
        src = sources.create(name, **params)
        self.comp.set_base(src)
        self._base_spec = src.describe()
        self._save_state()
        self._notify_state()

    def _notify_state(self):
        for cb in list(self._state_listeners):
            try:
                cb()
            except Exception as e:
                print(f"[ks82rgb] state listener error: {e}")

    def _subscribe_state(self, cb):
        self._state_listeners.append(cb)

    # ------------------------------------------------------------- services --
    def _start_enabled_services(self):
        enabled = {**DEFAULT_SERVICES, **self._load_config().get("services", {})}
        for name, on in enabled.items():
            if on and name in services.names():
                self._start_service(name)

    def _start_service(self, name):
        if name in self._services:
            return
        svc = services.create(name)
        if not svc.available():
            print(f"[ks82rgb] service {name} unavailable (missing dependency)")
            return
        ctx = services.ServiceContext(
            self.comp, self._now,
            command=self._handle,
            subscribe=self._subscribe_state,
            status=lambda: self._handle({"cmd": "status"}))
        svc.start(ctx)
        self._services[name] = svc
        print(f"[ks82rgb] service started: {name}")

    def _stop_service(self, name):
        svc = self._services.pop(name, None)
        if svc:
            svc.stop()

    def _handle(self, req):
        cmd = req.get("cmd")
        if cmd == "ping":
            return {"ok": True, "pid": os.getpid()}

        if cmd == "set_mode":
            self._set_base(req["name"], req.get("params", {}))
            return {"ok": True, "mode": req["name"]}

        if cmd == "solid":
            self._set_base("solid", {"color": req["color"]})
            return {"ok": True}

        if cmd == "static":
            self._set_base("static", {"frame": req["frame"]})
            return {"ok": True}

        if cmd == "off":
            self._set_base("solid", {"color": [0, 0, 0]})
            return {"ok": True}

        if cmd == "brightness":
            self.comp.brightness = max(0.0, min(1.0, float(req["value"])))
            self._save_state()
            self._notify_state()
            return {"ok": True, "brightness": self.comp.brightness}

        if cmd == "pulse":
            self.comp.add_overlay(PulseOverlay(
                self._now(),
                color=req.get("color", [255, 255, 255]),
                duration=req.get("duration", 0.9)))
            return {"ok": True}

        if cmd == "service":
            name = req["name"]
            if name not in services.names():
                return {"ok": False, "error": f"unknown service: {name}"}
            if req.get("enable", True):
                self._start_service(name)
            else:
                self._stop_service(name)
            self._save_service_config(name, req.get("enable", True))
            return {"ok": True, "services": sorted(self._services)}

        if cmd == "list_modes":
            return {"ok": True, "modes": sources.catalog()}

        if cmd == "status":
            return {"ok": True,
                    "mode": self._base_spec["name"],
                    "params": self._base_spec["params"],
                    "brightness": self.comp.brightness,
                    "connected": self._connected,
                    "services": sorted(self._services),
                    "fps": FPS,
                    "pid": os.getpid()}

        if cmd == "hold":
            self._paused.set()
            return {"ok": True}

        if cmd == "resume":
            self._paused.clear()
            return {"ok": True}

        if cmd == "stop":
            self._stop.set()
            return {"ok": True}

        return {"ok": False, "error": f"unknown command: {cmd!r}"}

    # ----------------------------------------------------------------- state --
    def _save_state(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"base": self._base_spec,
                       "brightness": self.comp.brightness}, f)
        os.replace(tmp, STATE_PATH)

    def _load_state(self):
        try:
            with open(STATE_PATH) as f:
                st = json.load(f)
        except (OSError, ValueError):
            self._set_base("wave", {})        # first-run default
            return
        self.comp.brightness = st.get("brightness", 1.0)
        base = st.get("base", {})
        try:
            self._set_base(base.get("name", "wave"), base.get("params", {}))
        except KeyError:
            self._set_base("wave", {})

    def _load_config(self):
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_service_config(self, name, enabled):
        cfg = self._load_config()
        cfg.setdefault("services", {})[name] = enabled
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
