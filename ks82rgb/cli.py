"""Command-line interface for KS82-B RGB control."""

import argparse
import json
import os
import sys
import time

from . import colors, effects, layout
from .controller import DeviceError, Keyboard

PROFILE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "profiles")
FPS = 30.0


# ---------------------------------------------------------------- helpers -----
def _apply_static(kb, base, keys, brightness):
    """base: (r,g,b) for every slot. keys: {name-or-slot: (r,g,b)} overrides."""
    # Fill every slot (0..125) so keys not yet in the label map still light.
    slot_colors = {s: colors.scale(base, brightness)
                   for s in range(layout.NUM_SLOTS)}
    for name, col in keys.items():
        for s in _resolve(name):
            slot_colors[s] = colors.scale(col, brightness)
    kb.send(slot_colors)


def _resolve(name):
    """A key spec is either a raw slot index (int) or a key label."""
    s = str(name).strip()
    if s.isdigit():
        return [int(s)]
    slots = layout.slots_for_name(s)
    if not slots:
        raise SystemExit(f"unknown key: {name!r} (try `ks82rgb list-keys`)")
    return slots


def _run_effect(kb, frame_fn, brightness, duration):
    """Drive an effect at FPS until duration elapses or Ctrl-C."""
    interval = 1.0 / FPS
    start = time.monotonic()
    print("Running effect -- press Ctrl-C to stop.", file=sys.stderr)
    try:
        while True:
            t = time.monotonic() - start
            if duration and t >= duration:
                break
            frame = frame_fn(t)
            if brightness < 1.0:
                frame = {k: colors.scale(v, brightness) for k, v in frame.items()}
            kb.send(frame)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)


# --------------------------------------------------------------- commands -----
def cmd_solid(kb, args):
    _apply_static(kb, colors.parse_color(args.color), {}, args.brightness)
    print(f"Set all keys to {args.color}.")


def cmd_off(kb, args):
    _apply_static(kb, (0, 0, 0), {}, 1.0)
    print("LEDs off.")


def cmd_key(kb, args):
    if len(args.pairs) % 2 != 0:
        raise SystemExit("give KEY COLOR pairs, e.g. key W red A red S red")
    keys = {}
    for i in range(0, len(args.pairs), 2):
        keys[args.pairs[i]] = colors.parse_color(args.pairs[i + 1])
    base = colors.parse_color(args.base)
    _apply_static(kb, base, keys, args.brightness)
    print(f"Set {len(keys)} key(s) over base {args.base}.")


def cmd_effect(kb, args):
    factory = effects.EFFECTS[args.name]
    kwargs = {}
    if args.speed:
        # smaller period = faster; map speed 1..10 -> period
        base_period = {"breathing": 4.0, "wave": 6.0, "rainbow": 8.0}[args.name]
        kwargs["period"] = base_period * (5.0 / args.speed)
    if args.name == "breathing":
        frame_fn = factory(colors.parse_color(args.color), **kwargs)
    else:
        frame_fn = factory(**kwargs)
    _run_effect(kb, frame_fn, args.brightness, args.duration)


def cmd_save(kb, args):
    """Persist a static profile: solid base + optional per-key overrides."""
    keys = {}
    for i in range(0, len(args.pairs), 2):
        keys[args.pairs[i]] = args.pairs[i + 1]
    profile = {"type": "static", "brightness": args.brightness,
               "base": args.base, "keys": keys}
    os.makedirs(PROFILE_DIR, exist_ok=True)
    path = os.path.join(PROFILE_DIR, args.name + ".json")
    with open(path, "w") as f:
        json.dump(profile, f, indent=2)
    print(f"Saved profile -> {path}")


def cmd_load(kb, args):
    path = args.name
    if not os.path.isfile(path):
        path = os.path.join(PROFILE_DIR, args.name + ".json")
    if not os.path.isfile(path):
        raise SystemExit(f"no such profile: {args.name}")
    with open(path) as f:
        p = json.load(f)
    brightness = p.get("brightness", 1.0)
    if p.get("type") == "effect":
        factory = effects.EFFECTS[p["name"]]
        frame_fn = factory(**p.get("params", {}))
        _run_effect(kb, frame_fn, brightness, args.duration)
    else:
        base = colors.parse_color(p.get("base", "#000000"))
        keys = {k: colors.parse_color(v) for k, v in p.get("keys", {}).items()}
        _apply_static(kb, base, keys, brightness)
        print(f"Loaded profile {args.name}.")


def cmd_list_profiles(kb, args):
    if not os.path.isdir(PROFILE_DIR):
        print("(no profiles)")
        return
    names = [f[:-5] for f in sorted(os.listdir(PROFILE_DIR)) if f.endswith(".json")]
    print("\n".join(names) if names else "(no profiles)")


def cmd_list_keys(kb, args):
    print(", ".join(layout.all_key_names()))


def cmd_slot(kb, args):
    """Light a single raw slot index (for probing / manual mapping)."""
    col = colors.parse_color(args.color)
    kb.send({args.index: col})
    print(f"Slot {args.index} = {args.color} (label: {layout.LABELS[args.index]}).")


def cmd_calibrate(kb, args):
    """Interactively map each LED slot to a physical key label."""
    path = layout._CUSTOM_PATH
    slots = {}
    if os.path.isfile(path):
        with open(path) as f:
            slots = {int(k): v for k, v in json.load(f).get("slots", {}).items()}
    print("Calibration -- one slot lights white at a time.")
    print("  type the key that lit up (e.g. Num7, NumEnter, Ins)")
    print("  ENTER = keep current/skip,  '-' = no key here,  'b' = back,  'q' = save & quit\n")
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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"slots": {str(k): v for k, v in slots.items()}}, f, indent=2)
    kb.send({})  # blank
    print(f"\nSaved -> {path}  ({sum(1 for v in slots.values() if v)} keys mapped)")


# ----------------------------------------------------------------- parser -----
def build_parser():
    p = argparse.ArgumentParser(
        prog="ks82rgb",
        description="RGB control for the Redragon KS82-B (Sinowealth 258a:0049).")
    p.add_argument("-b", "--brightness", type=float, default=1.0,
                   help="global brightness 0.0-1.0 (default 1.0)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("solid", help="set every key to one color")
    s.add_argument("color")
    s.set_defaults(func=cmd_solid, needs_device=True)

    s = sub.add_parser("off", help="turn all LEDs off")
    s.set_defaults(func=cmd_off, needs_device=True)

    s = sub.add_parser("key", help="set individual keys: KEY COLOR [KEY COLOR ...]")
    s.add_argument("--base", default="#000000", help="base color for other keys")
    s.add_argument("pairs", nargs="+")
    s.set_defaults(func=cmd_key, needs_device=True)

    s = sub.add_parser("effect", help="run an animated effect")
    s.add_argument("name", choices=sorted(effects.EFFECTS))
    s.add_argument("--color", default="cyan", help="color for breathing")
    s.add_argument("--speed", type=float, default=0, help="1(slow)-10(fast)")
    s.add_argument("--duration", type=float, default=0, help="seconds (0=forever)")
    s.set_defaults(func=cmd_effect, needs_device=True)

    s = sub.add_parser("save", help="save a static profile")
    s.add_argument("name")
    s.add_argument("--base", default="#000000")
    s.add_argument("pairs", nargs="*", help="KEY COLOR pairs")
    s.set_defaults(func=cmd_save, needs_device=False)

    s = sub.add_parser("load", help="load a profile (name or path)")
    s.add_argument("name")
    s.add_argument("--duration", type=float, default=0)
    s.set_defaults(func=cmd_load, needs_device=True)

    s = sub.add_parser("slot", help="light one raw slot index 0-125 (probing)")
    s.add_argument("index", type=int)
    s.add_argument("color", nargs="?", default="white")
    s.set_defaults(func=cmd_slot, needs_device=True)

    s = sub.add_parser("calibrate", help="interactively map slots -> key labels")
    s.add_argument("--start", type=int, default=0, help="start slot (default 0)")
    s.set_defaults(func=cmd_calibrate, needs_device=True)

    sub.add_parser("list-profiles").set_defaults(
        func=cmd_list_profiles, needs_device=False)
    sub.add_parser("list-keys").set_defaults(
        func=cmd_list_keys, needs_device=False)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not getattr(args, "needs_device", True):
        return args.func(None, args)
    try:
        with Keyboard() as kb:
            args.func(kb, args)
    except DeviceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
