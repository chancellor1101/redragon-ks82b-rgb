# ks82rgb — Linux RGB control for the Redragon KS82-B

A small CLI to set colors, per-key lighting, saved profiles, and animated
effects on the **Redragon KS82-B** mechanical keyboard (Sinowealth controller,
USB `258a:0049`) — replacing the Windows-only vendor software.

It writes directly to the keyboard's vendor HID node (`/dev/hidrawN`, interface
1) and sends a single **LED feature report** via the `HIDIOCSFEATURE` ioctl —
the same thing the board does in normal operation. It **never** issues firmware
/ bootloader / flash commands, so it cannot brick the keyboard. (This is why we
don't use OpenRGB's Sinowealth driver, which was disabled over bricking reports
tied to its firmware-flash path.)

No Python dependencies and no build step — pure standard library, runs on
`python3` directly.

## Setup

```bash
git clone https://github.com/chancellor1101/redragon-ks82b-rgb.git
cd redragon-ks82b-rgb

# one-time: allow non-root access to the keyboard's hidraw node
sudo cp 60-ks82-rgb.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
# verify: /dev/hidrawN for 258a:0049 should now be crw-rw-rw- (or your rule's mode)

./bin/ks82rgb solid white   # first light-up
```

Note: `reload-rules` alone does **not** re-apply to an already-plugged device —
`udevadm trigger` (or a physical replug) is required. If you scope the rule to
`plugdev` instead of `0666`, you must be in that group *and* start a fresh login
session for it to take effect.

Optionally symlink the launcher onto your PATH:

```bash
ln -s "$PWD/bin/ks82rgb" ~/.local/bin/ks82rgb
```

## Usage

```bash
ks82rgb solid red                 # all keys red
ks82rgb solid "#ff8800"           # hex
ks82rgb solid 0,128,255           # r,g,b
ks82rgb -b 0.4 solid white        # 40% brightness
ks82rgb off                       # lights off

# per-key (everything else stays on --base)
ks82rgb key --base "#111111" W red A red S red D red Space cyan

# animated effects (Ctrl-C to stop)
ks82rgb effect wave
ks82rgb effect breathing --color purple --speed 7
ks82rgb effect rainbow --duration 30

# profiles
ks82rgb save gaming --base "#111111" W red A red S red D red Space cyan
ks82rgb load gaming
ks82rgb load rainbow-wave          # an effect profile (runs until Ctrl-C)
ks82rgb list-profiles
ks82rgb list-keys
```

## Daemon & autostart

For always-on lighting, ambient/utility modes, and (soon) notifications, meters,
and Home Assistant control, run the daemon — a systemd `--user` service that owns
the keyboard and renders at ~30 fps (skipping re-sends of unchanged frames, so
static modes are nearly free).

```bash
ks82rgb service install     # install + enable + start the autostart daemon
ks82rgb status              # what's running
ks82rgb list-modes          # available modes (built-ins + plugins)
ks82rgb mode wave           # switch base mode live
ks82rgb brightness 0.6      # 0.0-1.0
ks82rgb stop                # stop the daemon
ks82rgb service uninstall   # remove autostart
```

When the daemon is running, `solid` / `key` / `off` / `effect` / `load`
automatically route **through** it (no flicker, no fighting for the device).
When it isn't, they fall back to a direct one-shot. `slot` / `calibrate` briefly
pause the daemon so they can drive the device directly.

### Architecture

```
ks82d daemon ──owns──▶ /dev/hidraw1        control socket (JSON over UNIX)
  Compositor: base source + overlays          ▲            ▲
  Sources (plugins): effects + utility    ks82rgb CLI   PyQt5 tray (planned)
```

**Modes are plugins.** Built-ins: `wave`, `breathing`, `rainbow`, `solid`,
`static`. Drop a `.py` into `~/.config/ks82rgb/plugins/` exposing a `Source`
subclass and it appears in `list-modes` — that's the whole extension model (see
`examples/plugins/police.py`). State (current mode + brightness) persists to
`~/.config/ks82rgb/state.json`.

### Ambient / utility modes

The keyboard as a status display:

```bash
ks82rgb mode cpu     # whole-board tint by CPU load: calm pale-green -> hot dark-red
ks82rgb mode vu      # left-to-right audio VU bar (green->red)
```

`vu` needs PulseAudio/PipeWire's `parec` (usually preinstalled). CPU reads
`/proc/stat`.

**VU styles, directions, and audio source.**

```bash
ks82rgb mode vu --style spectrum --direction right   # per-row spectrum (default)
ks82rgb mode vu --style bar --direction up            # single loudness bar
ks82rgb audio-sources                                 # list monitor sources
ks82rgb mode vu --device <monitor>                    # capture a specific device
```

- **`spectrum`** (default, needs numpy): one bar per key row — the board becomes
  a 6-band analyzer, **bass at the bottom (red) → treble at the top (blue)**.
- **`bar`**: a single meter for overall loudness (green→red).
- **`--direction`** `right`/`left`/`up`/`down` sets the fill direction (bar); for
  spectrum, `left`/`right` flips which end the bars grow from.

All of this is in the tray too: **VU options** (style + direction) and
**Audio source (VU)** (● marks a device that's playing). By default `vu` follows
the default sink, and the capture self-heals if `parec` dies or you switch
outputs.

### Notification pulses + overlays

A D-Bus listener flashes the board on every desktop notification, painted *over*
whatever mode is running (uses the compositor's overlay layer):

```bash
ks82rgb notify off / on   # toggle notification pulses (default on)
ks82rgb pulse "#00ff88"   # fire a one-off pulse (test, or trigger from scripts)
```

Overlays and `pulse` are how other integrations (Home Assistant, CI status, …)
signal you without taking over the base mode. Needs `dbus-monitor`
(`dbus` / `dbus-bin` package).

## Home Assistant / MQTT

Control the keyboard from Home Assistant and pulse it from any automation. Uses
MQTT discovery, so it auto-appears as one device with three entities:

| Entity | What |
|---|---|
| **Keyboard** (light) | on/off, brightness, RGB color (color sets a solid mode) |
| **Keyboard Mode** (select) | pick any mode — built-ins *and* plugins |
| **Keyboard Pulse** (button) | fire a pulse overlay (doorbell, alarm, CI, …) |

State is republished on every change (from HA, the CLI, or the tray) so entities
stay in sync.

```bash
pip install --user --break-system-packages paho-mqtt   # if not already present
ks82rgb mqtt setup            # writes ~/.config/ks82rgb/mqtt.json (chmod 600)
# edit that file: host, port, username, password
ks82rgb mqtt on               # connect + publish discovery (persists across restarts)
ks82rgb mqtt off              # disable
```

Config (`~/.config/ks82rgb/mqtt.json`):

```json
{ "host": "homeassistant.local", "port": 1883,
  "username": "mqtt_user", "password": "secret",
  "discovery_prefix": "homeassistant", "base_topic": "ks82rgb" }
```

Raw topics (for non-HA use): `ks82rgb/mode/set` (mode name), `ks82rgb/light/set`
(HA JSON light schema), `ks82rgb/pulse/set` (`PULSE` or a color), with state on
`ks82rgb/*/state` and `ks82rgb/availability`.

## GUI (system tray)

A PyQt5 tray app that steers the daemon over the same socket:

```bash
ks82rgb gui                  # launch the tray control panel
ks82rgb service install-gui  # autostart the tray at login (XDG .desktop)
```

Left-click the tray icon for the control panel; right-click for the menu (mode
picker, solid color dialog, brightness, off, daemon controls). It reflects
live daemon state and any mode plugins you've added. Requires PyQt5
(`python3-pyqt5` on Debian/Ubuntu; usually preinstalled on KDE).

## Profiles

Profiles are JSON in `profiles/`. Two shapes:

```json
{ "type": "static", "brightness": 1.0, "base": "#000000",
  "keys": { "W": "red", "Space": "cyan" } }

{ "type": "effect", "name": "wave", "params": { "period": 6.0 } }
```

## How it works

`ks82rgb/controller.py` documents the wire format. The packet is a 382-byte HID
feature report: report id `0x08`, header `0x0A 0x7A 0x01`, then **126 RGB
triples** (378 bytes), one per key LED slot. The slot→key map lives in
`ks82rgb/layout.py`, with this board's exact layout (108 keys, full-size with
numpad) in `custom-layout.json` — produced by `ks82rgb calibrate`.

Protocol derived from the [Sinodragon](https://github.com/EvanSunde/Sinodragon)
project (which targets this exact PID/VID) and cross-checked against the
device's own HID report descriptor. The 16×6 grid from Sinodragon was for a
numpad-less variant; calibration remapped it to the full 126-slot layout.
