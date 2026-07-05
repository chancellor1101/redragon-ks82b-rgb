"""Command-line interface for KS82-B RGB control.

When the daemon is running it owns the keyboard, so device-touching commands
route to it over the control socket; otherwise they fall back to opening the
device directly for a one-shot.  Daemon/service/mode commands are new in the
daemon milestone.
"""

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager

from . import colors, effects, ipc, layout, sources
from .controller import DeviceError, Keyboard

PROFILE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "profiles")
FPS = 30.0
_EFFECT_BASE_PERIOD = {"breathing": 4.0, "wave": 6.0, "rainbow": 8.0}


# ---------------------------------------------------------------- helpers -----
def _resolve(name):
    """A key spec is either a raw slot index (int) or a key label."""
    s = str(name).strip()
    if s.isdigit():
        return [int(s)]
    slots = layout.slots_for_name(s)
    if not slots:
        raise SystemExit(f"unknown key: {name!r} (try `ks82rgb list-keys`)")
    return slots


def _build_static_frame(base, keys, brightness):
    """Fixed frame for all slots. base:(r,g,b), keys:{name-or-slot:(r,g,b)}."""
    frame = {s: colors.scale(base, brightness) for s in range(layout.NUM_SLOTS)}
    for name, col in keys.items():
        for s in _resolve(name):
            frame[s] = colors.scale(col, brightness)
    return frame


def _dispatch_static(frame):
    """Show a fixed frame via the daemon if up, else a direct one-shot."""
    r = ipc.request({"cmd": "static",
                     "frame": {str(k): list(v) for k, v in frame.items()}})
    if r is None:
        with Keyboard() as kb:
            kb.send(frame)
        return "direct"
    if not r.get("ok"):
        raise SystemExit(f"daemon error: {r.get('error')}")
    return "daemon"


def _effect_params(name, speed, color):
    params = {}
    if speed:
        params["period"] = _EFFECT_BASE_PERIOD[name] * (5.0 / speed)
    if name == "breathing":
        params["color"] = list(colors.parse_color(color))
    return params


def _run_local_effect(frame_fn, brightness, duration):
    """Blocking animation loop used only when no daemon is running."""
    interval = 1.0 / FPS
    start = time.monotonic()
    print("No daemon -- running locally. Ctrl-C to stop.", file=sys.stderr)
    try:
        with Keyboard() as kb:
            while True:
                t = time.monotonic() - start
                if duration and t >= duration:
                    break
                frame = frame_fn(t)
                if brightness < 1.0:
                    frame = {k: colors.scale(v, brightness)
                             for k, v in frame.items()}
                kb.send(frame)
                time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)


@contextmanager
def _direct_device():
    """Exclusive device access for probing/calibration; pauses the daemon."""
    held = ipc.daemon_running()
    if held:
        ipc.request({"cmd": "hold"})
    try:
        with Keyboard() as kb:
            yield kb
    finally:
        if held:
            ipc.request({"cmd": "resume"})


def _where(mode):
    return "daemon" if mode == "daemon" else "direct (one-shot)"


# ------------------------------------------------------- static / effects -----
def cmd_solid(args):
    frame = _build_static_frame(colors.parse_color(args.color), {}, args.brightness)
    print(f"All keys {args.color} [{_where(_dispatch_static(frame))}].")


def cmd_off(args):
    _dispatch_static(_build_static_frame((0, 0, 0), {}, 1.0))
    print("LEDs off.")


def cmd_key(args):
    if len(args.pairs) % 2 != 0:
        raise SystemExit("give KEY COLOR pairs, e.g. key W red A red S red")
    keys = {args.pairs[i]: colors.parse_color(args.pairs[i + 1])
            for i in range(0, len(args.pairs), 2)}
    frame = _build_static_frame(colors.parse_color(args.base), keys, args.brightness)
    print(f"Set {len(keys)} key(s) [{_where(_dispatch_static(frame))}].")


def cmd_effect(args):
    params = _effect_params(args.name, args.speed, args.color)
    r = ipc.request({"cmd": "set_mode", "name": args.name, "params": params})
    if r is None:
        factory = effects.EFFECTS[args.name]
        frame_fn = (factory(colors.parse_color(args.color),
                            **{k: v for k, v in params.items() if k != "color"})
                    if args.name == "breathing" else factory(**params))
        _run_local_effect(frame_fn, args.brightness, args.duration)
    else:
        print(f"Mode -> {args.name} [daemon].")


# ------------------------------------------------------------- profiles -------
def cmd_save(args):
    keys = {args.pairs[i]: args.pairs[i + 1]
            for i in range(0, len(args.pairs), 2)}
    profile = {"type": "static", "brightness": args.brightness,
               "base": args.base, "keys": keys}
    os.makedirs(PROFILE_DIR, exist_ok=True)
    path = os.path.join(PROFILE_DIR, args.name + ".json")
    with open(path, "w") as f:
        json.dump(profile, f, indent=2)
    print(f"Saved profile -> {path}")


def cmd_load(args):
    path = args.name
    if not os.path.isfile(path):
        path = os.path.join(PROFILE_DIR, args.name + ".json")
    if not os.path.isfile(path):
        raise SystemExit(f"no such profile: {args.name}")
    with open(path) as f:
        p = json.load(f)
    brightness = p.get("brightness", 1.0)
    if p.get("type") == "effect":
        params = p.get("params", {})
        r = ipc.request({"cmd": "set_mode", "name": p["name"], "params": params})
        if r is None:
            _run_local_effect(effects.EFFECTS[p["name"]](**params),
                              brightness, args.duration)
        else:
            print(f"Mode -> {p['name']} [daemon].")
    else:
        base = colors.parse_color(p.get("base", "#000000"))
        keys = {k: colors.parse_color(v) for k, v in p.get("keys", {}).items()}
        frame = _build_static_frame(base, keys, brightness)
        print(f"Loaded profile {args.name} [{_where(_dispatch_static(frame))}].")


def cmd_list_profiles(args):
    if not os.path.isdir(PROFILE_DIR):
        print("(no profiles)")
        return
    names = [f[:-5] for f in sorted(os.listdir(PROFILE_DIR)) if f.endswith(".json")]
    print("\n".join(names) if names else "(no profiles)")


def cmd_list_keys(args):
    print(", ".join(layout.all_key_names()))


# ------------------------------------------------------- probe / calibrate ----
def cmd_slot(args):
    with _direct_device() as kb:
        kb.send({args.index: colors.parse_color(args.color)})
    print(f"Slot {args.index} = {args.color} (label: {layout.LABELS[args.index]}).")


def cmd_calibrate(args):
    path = layout._CUSTOM_PATH
    slots = {}
    if os.path.isfile(path):
        with open(path) as f:
            slots = {int(k): v for k, v in json.load(f).get("slots", {}).items()}
    print("Calibration -- one slot lights white at a time.")
    print("  type the key that lit up (e.g. Num7, NumEnter, Ins)")
    print("  ENTER = keep,  '-' = no key,  'b' = back,  'q' = save & quit\n")
    with _direct_device() as kb:
        s = max(0, args.start)
        while s < layout.NUM_SLOTS:
            kb.send({s: (255, 255, 255)})
            cur = slots.get(s, layout.LABELS[s]) or ""
            try:
                ans = input(f"slot {s:3d} [{cur}]: ").strip()
            except EOFError:
                break
            if ans == "q":
                break
            if ans == "b":
                s = max(0, s - 1)
                continue
            if ans == "":
                s += 1
                continue
            slots[s] = "" if ans == "-" else ans
            s += 1
        kb.send({})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"slots": {str(k): v for k, v in slots.items()}}, f, indent=2)
    print(f"\nSaved -> {path}  ({sum(1 for v in slots.values() if v)} keys mapped)")


# ----------------------------------------------------------- daemon / mode ----
def cmd_daemon(args):
    from .daemon import Daemon
    Daemon().run()


def cmd_mode(args):
    params = _effect_params(args.name, args.speed, args.color) \
        if args.name in _EFFECT_BASE_PERIOD else {}
    r = ipc.request({"cmd": "set_mode", "name": args.name, "params": params})
    if r is None:
        raise SystemExit("daemon not running (start: `ks82rgb service install`).")
    if not r.get("ok"):
        raise SystemExit(f"daemon error: {r.get('error')}")
    print(f"Mode -> {args.name}.")


def cmd_brightness(args):
    r = ipc.request({"cmd": "brightness", "value": args.value})
    if r is None:
        raise SystemExit("daemon not running; use -b with a mode/color instead.")
    print(f"Brightness -> {int(r['brightness'] * 100)}%.")


def cmd_list_modes(args):
    r = ipc.request({"cmd": "list_modes"})
    cat = r["modes"] if r else sources.catalog()
    src = "daemon" if r else "built-in (daemon offline; plugins not shown)"
    print(f"available modes [{src}]:")
    for name, kind in cat:
        print(f"  {name:14s} {kind}")


def cmd_status(args):
    r = ipc.request({"cmd": "status"})
    if r is None:
        print("daemon: not running")
        return
    params = dict(r["params"])
    if "frame" in params:                      # static frame: summarize, don't dump
        lit = sum(1 for v in params["frame"].values() if any(v))
        params = f"<static frame, {lit} keys lit>"
    print(f"daemon:     running (pid {r['pid']})")
    print(f"mode:       {r['mode']}  params={params}")
    print(f"brightness: {int(r['brightness'] * 100)}%")
    print(f"keyboard:   {'connected' if r['connected'] else 'DISCONNECTED'}")


def cmd_stop(args):
    r = ipc.request({"cmd": "stop"})
    print("daemon stopping." if r else "daemon not running.")


def cmd_gui(args):
    from . import gui
    return gui.run()


def cmd_service(args):
    from . import service
    if args.action == "install":
        for line in service.install():
            print("  " + line)
    elif args.action == "uninstall":
        for line in service.uninstall():
            print("  " + line)
    elif args.action == "install-gui":
        for line in service.install_gui_autostart():
            print("  " + line)
    elif args.action == "uninstall-gui":
        for line in service.uninstall_gui_autostart():
            print("  " + line)
    elif args.action == "status":
        os.execvp("systemctl", ["systemctl", "--user", "status", service.UNIT_NAME])


# ----------------------------------------------------------------- parser -----
def build_parser():
    p = argparse.ArgumentParser(
        prog="ks82rgb",
        description="RGB control for the Redragon KS82-B (Sinowealth 258a:0049).")
    p.add_argument("-b", "--brightness", type=float, default=1.0,
                   help="global brightness 0.0-1.0 for static commands")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("solid", help="set every key to one color")
    s.add_argument("color")
    s.set_defaults(func=cmd_solid)

    sub.add_parser("off", help="turn all LEDs off").set_defaults(func=cmd_off)

    s = sub.add_parser("key", help="set keys: KEY COLOR [KEY COLOR ...]")
    s.add_argument("--base", default="#000000")
    s.add_argument("pairs", nargs="+")
    s.set_defaults(func=cmd_key)

    s = sub.add_parser("effect", help="run/switch to an animated effect")
    s.add_argument("name", choices=sorted(effects.EFFECTS))
    s.add_argument("--color", default="cyan", help="color for breathing")
    s.add_argument("--speed", type=float, default=0, help="1(slow)-10(fast)")
    s.add_argument("--duration", type=float, default=0,
                   help="seconds when run locally (0=forever)")
    s.set_defaults(func=cmd_effect)

    s = sub.add_parser("mode", help="switch daemon base mode (any source)")
    s.add_argument("name")
    s.add_argument("--color", default="cyan")
    s.add_argument("--speed", type=float, default=0)
    s.set_defaults(func=cmd_mode)

    s = sub.add_parser("brightness", help="set daemon brightness 0.0-1.0")
    s.add_argument("value", type=float)
    s.set_defaults(func=cmd_brightness)

    s = sub.add_parser("save", help="save a static profile")
    s.add_argument("name")
    s.add_argument("--base", default="#000000")
    s.add_argument("pairs", nargs="*")
    s.set_defaults(func=cmd_save)

    s = sub.add_parser("load", help="load a profile (name or path)")
    s.add_argument("name")
    s.add_argument("--duration", type=float, default=0)
    s.set_defaults(func=cmd_load)

    s = sub.add_parser("slot", help="light one raw slot 0-125 (probing)")
    s.add_argument("index", type=int)
    s.add_argument("color", nargs="?", default="white")
    s.set_defaults(func=cmd_slot)

    s = sub.add_parser("calibrate", help="interactively map slots -> keys")
    s.add_argument("--start", type=int, default=0)
    s.set_defaults(func=cmd_calibrate)

    sub.add_parser("daemon", help="run the render daemon (foreground)").set_defaults(
        func=cmd_daemon)
    sub.add_parser("gui", help="run the PyQt5 system-tray control panel").set_defaults(
        func=cmd_gui)
    sub.add_parser("status", help="show daemon status").set_defaults(func=cmd_status)
    sub.add_parser("list-modes", help="list available modes").set_defaults(
        func=cmd_list_modes)
    sub.add_parser("stop", help="stop the daemon").set_defaults(func=cmd_stop)
    sub.add_parser("list-profiles").set_defaults(func=cmd_list_profiles)
    sub.add_parser("list-keys").set_defaults(func=cmd_list_keys)

    s = sub.add_parser("service", help="install/manage autostart (daemon + tray)")
    s.add_argument("action", choices=["install", "uninstall", "status",
                                      "install-gui", "uninstall-gui"])
    s.set_defaults(func=cmd_service)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except DeviceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except SystemExit:
        raise
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
