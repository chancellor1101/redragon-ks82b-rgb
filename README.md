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
