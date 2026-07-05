"""Animated lighting effects.

Each effect is a callable ``frame(t) -> {slot: (r,g,b)}`` where ``t`` is seconds
since the animation started.  Effects cover every lit slot (see layout).
"""

import colorsys
import math

from . import layout


def _hsv(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def _slots():
    # Cover every slot (0..125) so keys not yet in the label map still animate.
    return list(range(layout.NUM_SLOTS))


def solid(color):
    """Every key one static color."""
    frame = {s: color for s in _slots()}
    return lambda t: frame


def breathing(color, period=4.0):
    """Fade one color in and out. `period` = full cycle seconds."""
    r0, g0, b0 = color

    def frame(t):
        b = (math.sin(2 * math.pi * t / period - math.pi / 2) + 1) / 2
        b = 0.08 + 0.92 * b
        col = (int(r0 * b), int(g0 * b), int(b0 * b))
        return {s: col for s in _slots()}

    return frame


def wave(period=6.0, sat=1.0, val=1.0, spread=1.4):
    """Rainbow wave sweeping left -> right (phased by slot x-position)."""
    slots = _slots()
    xs = [layout.position(s)[0] for s in slots]
    xmax = max(xs) if xs else 1

    def frame(t):
        out = {}
        for s in slots:
            x = layout.position(s)[0]
            hue = (t / period) + (x / max(1, xmax)) * spread
            out[s] = _hsv(hue, sat, val)
        return out

    return frame


def rainbow(period=8.0, sat=1.0, val=1.0):
    """Whole-board hue cycle (all keys share one slowly rotating color)."""
    def frame(t):
        col = _hsv(t / period, sat, val)
        return {s: col for s in _slots()}

    return frame


EFFECTS = {
    "breathing": breathing,
    "wave": wave,
    "rainbow": rainbow,
}
