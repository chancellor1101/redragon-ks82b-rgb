"""Example ks82rgb plugin: a red/blue 'police' flasher.

Copy this into ~/.config/ks82rgb/plugins/ and it shows up in `ks82rgb list-modes`
as `police`. That's the entire extension model -- one file, one Source subclass.

Inside a plugin these names are provided for you: register, Source, effects,
layout. (You can also `from ks82rgb import ...` if you prefer explicit imports.)
"""

import math


@register  # noqa: F821  (injected by the plugin loader)
class PoliceSource(Source):  # noqa: F821
    name = "police"
    kind = "effect"

    def __init__(self, period=1.0, **kw):
        super().__init__(period=period)
        self.period = period

    def render(self, t):
        # left half red, right half blue, swapping each half-period
        phase = math.floor((t / self.period) * 2) % 2
        left, right = ((255, 0, 0), (0, 0, 255)) if phase == 0 else ((0, 0, 255), (255, 0, 0))
        frame = {}
        for slot in layout.lit_slots():  # noqa: F821
            x = layout.position(slot)[0]  # noqa: F821
            frame[slot] = left if x < 9 else right
        return frame
