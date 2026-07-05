"""Transient overlays painted on top of the base mode by the compositor."""

import math

from . import layout
from .compositor import Overlay


class PulseOverlay(Overlay):
    """A single sine-enveloped flash: fades in and out over `duration` seconds.

    Painted over the current mode, so a notification pulses your wave/cpu/etc.
    without replacing it.  `t0` is in the daemon's render timebase.
    """

    def __init__(self, t0, color=(255, 255, 255), duration=0.9, peak=0.9, slots=None):
        self.t0 = t0
        self.color = tuple(color)
        self.duration = duration
        self.peak = peak
        self.slots = slots

    def paint(self, t):
        dt = t - self.t0
        if dt < 0 or dt > self.duration:
            return None
        alpha = math.sin(math.pi * (dt / self.duration)) * self.peak
        slots = self.slots if self.slots is not None else layout.lit_slots()
        return ({s: self.color for s in slots}, alpha)

    def done(self, t):
        return (t - self.t0) > self.duration
