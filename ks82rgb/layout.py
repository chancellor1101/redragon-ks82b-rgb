"""Redragon KS82-B (Sinowealth 258a:0049) key -> LED-slot mapping.

The LED feature report carries **126 RGB slots** (378 data bytes).  Each slot
drives one physical key's LED.  This module maps physical key labels to slot
indices.

Slots 0..95 come from the EvanSunde/Sinodragon 16x6 grid (row-major), which is
correct for the main alpha/function block on this board.  Slots 96..125 are the
extra keys on this variant (numpad + nav cluster) and are filled in by running
``ks82rgb calibrate`` -- that writes ``custom-layout.json`` at the project root,
which this module loads and merges over the defaults if present.
"""

import json
import os

NUM_SLOTS = 126

# 16 rows x 6 cols, row-major -> slot index = row*6 + col.  Each "row" here is a
# physical column of the main block (left->right); entries are top->bottom.
_GRID = [
    ["Esc",   "`",    "Tab",  "Caps",  "Shift", "Ctrl"],
    ["F1",    "1",    "Q",    "A",     "Z",     "Win"],
    ["F2",    "2",    "W",    "S",     "X",     "Alt"],
    ["F3",    "3",    "E",    "D",     "C",     "NAN"],
    ["F4",    "4",    "R",    "F",     "V",     "NAN"],
    ["F5",    "5",    "T",    "G",     "B",     "Space"],
    ["F6",    "6",    "Y",    "H",     "N",     "NAN"],
    ["F7",    "7",    "U",    "J",     "M",     "NAN"],
    ["F8",    "8",    "I",    "K",     ",",     "Alt"],
    ["F9",    "9",    "O",    "L",     ".",     "Fn"],
    ["F10",   "0",    "P",    ";",     "/",     "Ctrl"],
    ["F11",   "-",    "[",    "'",     "NAN",   "NAN"],
    ["F12",   "=",    "]",    "NAN",   "NAN",   "NAN"],
    ["PrtSc", "Bksp", "\\",   "Enter", "Shift", "Left"],
    ["Pause", "NAN",  "NAN",  "NAN",   "Up",    "Down"],
    ["Del",   "Home", "End",  "PgUp",  "PgDn",  "Right"],
]

# LABELS[slot] = physical key name, or None if that slot has no key.
LABELS = [None] * NUM_SLOTS
for _r, _row in enumerate(_GRID):
    for _c, _name in enumerate(_row):
        LABELS[_r * 6 + _c] = None if _name == "NAN" else _name

_CUSTOM_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "custom-layout.json")


def load_custom():
    """Merge slot->name overrides from custom-layout.json (from calibration)."""
    if not os.path.isfile(_CUSTOM_PATH):
        return
    with open(_CUSTOM_PATH) as f:
        data = json.load(f)
    for slot_str, name in data.get("slots", {}).items():
        slot = int(slot_str)
        if 0 <= slot < NUM_SLOTS:
            LABELS[slot] = name if name else None


load_custom()


def lit_slots():
    """Every slot that drives a real key (has a label)."""
    return [s for s in range(NUM_SLOTS) if LABELS[s] is not None]


def slots_for_name(name):
    """All slots whose label matches `name` (case-insensitive)."""
    n = name.strip().lower()
    return [s for s in range(NUM_SLOTS)
            if LABELS[s] is not None and LABELS[s].lower() == n]


def all_key_names():
    return sorted({LABELS[s] for s in range(NUM_SLOTS) if LABELS[s] is not None})


def position(slot):
    """Approximate (x, y) for spatial effects.  x = column-ish, for L->R sweeps."""
    return (slot // 6, slot % 6)
