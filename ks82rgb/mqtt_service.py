"""MQTT / Home Assistant integration (Milestone 3).

Registers a `mqtt` daemon service.  On connect it publishes Home Assistant MQTT
discovery configs so the keyboard shows up as a device with three entities:

  * light  (ks82rgb/light/set|state)  -- on/off, brightness, RGB color
  * select (ks82rgb/mode/set|state)   -- pick any mode (built-ins + plugins)
  * button (ks82rgb/pulse/set)        -- fire a pulse overlay

It drives the daemon through ``ctx.command`` and republishes state on every
change (from MQTT, the CLI, or the tray) so HA stays in sync.

Broker config: ~/.config/ks82rgb/mqtt.json
  {"host": "...", "port": 1883, "username": "...", "password": "...",
   "discovery_prefix": "homeassistant", "base_topic": "ks82rgb"}
"""

import json
import os

from . import colors, sources
from .services import Service, register_service

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "ks82rgb")
MQTT_CONFIG = os.path.join(CONFIG_DIR, "mqtt.json")

DEVICE = {
    "identifiers": ["ks82rgb"],
    "name": "Redragon KS82-B",
    "manufacturer": "Redragon",
    "model": "KS82-B (Sinowealth 258a:0049)",
}

DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 1883,
    "username": "",
    "password": "",
    "discovery_prefix": "homeassistant",
    "base_topic": "ks82rgb",
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(MQTT_CONFIG) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def write_template():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.exists(MQTT_CONFIG):
        with open(MQTT_CONFIG, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        os.chmod(MQTT_CONFIG, 0o600)      # holds the broker password
    return MQTT_CONFIG


@register_service
class MqttService(Service):
    name = "mqtt"

    def __init__(self):
        self._ctx = None
        self._client = None
        self._cfg = load_config()
        self._last_color = [255, 255, 255]
        b = self._cfg["base_topic"]
        self.t_avail = f"{b}/availability"
        self.t_light_set, self.t_light_state = f"{b}/light/set", f"{b}/light/state"
        self.t_mode_set, self.t_mode_state = f"{b}/mode/set", f"{b}/mode/state"
        self.t_pulse_set = f"{b}/pulse/set"

    def available(self):
        try:
            import paho.mqtt.client  # noqa: F401
        except ImportError:
            print("[ks82rgb] mqtt: paho-mqtt not installed "
                  "(`pip install --user --break-system-packages paho-mqtt`)")
            return False
        return True

    # ---------------------------------------------------------------- start --
    def start(self, ctx):
        import paho.mqtt.client as mqtt

        self._ctx = ctx
        self._cfg = load_config()
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ks82rgb")
        if self._cfg.get("username"):
            c.username_pw_set(self._cfg["username"], self._cfg.get("password", ""))
        c.will_set(self.t_avail, "offline", retain=True)
        c.on_connect = self._on_connect
        c.on_message = self._on_message
        self._client = c
        try:
            c.connect(self._cfg["host"], int(self._cfg["port"]), keepalive=45)
        except Exception as e:
            print(f"[ks82rgb] mqtt: connect to {self._cfg['host']} failed: {e}")
            return
        c.loop_start()
        if ctx.subscribe:
            ctx.subscribe(self._publish_state)
        print(f"[ks82rgb] mqtt: connecting to {self._cfg['host']}:{self._cfg['port']}")

    def stop(self):
        if self._client:
            try:
                self._client.publish(self.t_avail, "offline", retain=True)
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None

    # -------------------------------------------------------------- discovery --
    def _publish_discovery(self):
        pre = self._cfg["discovery_prefix"]
        common = {"availability_topic": self.t_avail, "device": DEVICE}
        self._pub(f"{pre}/light/ks82rgb/rgb/config", {
            **common, "name": "Keyboard", "unique_id": "ks82rgb_light",
            "schema": "json", "brightness": True,
            "supported_color_modes": ["rgb"],
            "command_topic": self.t_light_set, "state_topic": self.t_light_state,
        }, retain=True)
        self._pub(f"{pre}/select/ks82rgb/mode/config", {
            **common, "name": "Keyboard Mode", "unique_id": "ks82rgb_mode",
            "options": [n for n, _ in sources.catalog()],
            "command_topic": self.t_mode_set, "state_topic": self.t_mode_state,
        }, retain=True)
        self._pub(f"{pre}/button/ks82rgb/pulse/config", {
            **common, "name": "Keyboard Pulse", "unique_id": "ks82rgb_pulse",
            "command_topic": self.t_pulse_set, "payload_press": "PULSE",
        }, retain=True)

    # --------------------------------------------------------------- callbacks --
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            print(f"[ks82rgb] mqtt: connect refused ({reason_code})")
            return
        print("[ks82rgb] mqtt: connected")
        client.publish(self.t_avail, "online", retain=True)
        self._publish_discovery()
        for topic in (self.t_light_set, self.t_mode_set, self.t_pulse_set):
            client.subscribe(topic)
        self._publish_state()

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode(errors="replace").strip()
        try:
            if msg.topic == self.t_mode_set:
                self._ctx.command({"cmd": "set_mode", "name": payload, "params": {}})
            elif msg.topic == self.t_pulse_set:
                color = ([255, 255, 255] if payload.upper() in ("PULSE", "PRESS", "ON", "")
                         else list(colors.parse_color(payload)))
                self._ctx.command({"cmd": "pulse", "color": color})
            elif msg.topic == self.t_light_set:
                self._handle_light(json.loads(payload))
        except Exception as e:
            print(f"[ks82rgb] mqtt: bad message on {msg.topic}: {e}")

    def _handle_light(self, p):
        if p.get("state") == "OFF":
            self._ctx.command({"cmd": "off"})
            return
        if "color" in p:
            col = [p["color"]["r"], p["color"]["g"], p["color"]["b"]]
            self._last_color = col
            self._ctx.command({"cmd": "solid", "color": col})
        if "brightness" in p:
            self._ctx.command({"cmd": "brightness", "value": p["brightness"] / 255.0})

    # ------------------------------------------------------------------ state --
    def _publish_state(self):
        if not self._client:
            return
        st = self._ctx.status() if self._ctx.status else {}
        bright = st.get("brightness", 1.0)
        on = bright > 0 and st.get("mode") != "solid" or any(self._last_color)
        self._pub(self.t_light_state, {
            "state": "ON" if bright > 0 else "OFF",
            "brightness": int(bright * 255),
            "color_mode": "rgb",
            "color": {"r": self._last_color[0], "g": self._last_color[1],
                      "b": self._last_color[2]},
        })
        self._client.publish(self.t_mode_state, st.get("mode", ""))

    # ---------------------------------------------------------------- helpers --
    def _pub(self, topic, obj, retain=False):
        if self._client:
            self._client.publish(topic, json.dumps(obj), retain=retain)
