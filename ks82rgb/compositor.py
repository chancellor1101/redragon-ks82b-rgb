"""Compositor: blends a base source with transient overlays into one frame.

The base layer is the active mode (wave, cpu-meter, solid, ...).  Overlays are
short-lived effects painted *on top* -- e.g. a notification pulse that flashes
without disturbing the mode underneath.  Overlays are added in Milestone 2; the
blend loop already supports them.
"""

import threading

from . import layout, sources


def _blend(dst, src, alpha):
    """Alpha-blend src (r,g,b) over dst (r,g,b). alpha 0..1."""
    a = max(0.0, min(1.0, alpha))
    return (
        int(dst[0] * (1 - a) + src[0] * a),
        int(dst[1] * (1 - a) + src[1] * a),
        int(dst[2] * (1 - a) + src[2] * a),
    )


class Overlay:
    """Base class for transient top-layer effects.

    `paint(t)` returns {slot: (r,g,b)} and an alpha 0..1 as ``(frame, alpha)``,
    or None when it has nothing to draw this tick.  `done(t)` marks expiry.
    """

    def paint(self, t):
        return None

    def done(self, t):
        return False


class Compositor:
    def __init__(self):
        self._base = sources.create("solid", color=(0, 0, 0))
        self._overlays = []
        self.brightness = 1.0
        self._lock = threading.Lock()

    # -- base layer --
    def set_base(self, source):
        with self._lock:
            old, self._base = self._base, source
        if old is not None and old is not source:
            try:
                old.close()
            except Exception:
                pass

    @property
    def base(self):
        return self._base

    # -- overlays --
    def add_overlay(self, overlay):
        with self._lock:
            self._overlays.append(overlay)

    def clear_overlays(self):
        with self._lock:
            self._overlays.clear()

    # -- render --
    def render(self, t):
        with self._lock:
            base = self._base
            overlays = list(self._overlays)
            bright = self.brightness

        frame = dict(base.render(t))

        if overlays:
            alive = []
            for ov in overlays:
                painted = ov.paint(t)
                if painted:
                    ofr, alpha = painted
                    for slot, col in ofr.items():
                        frame[slot] = _blend(frame.get(slot, (0, 0, 0)), col, alpha)
                if not ov.done(t):
                    alive.append(ov)
            if len(alive) != len(overlays):
                with self._lock:
                    self._overlays = [o for o in self._overlays if o in alive]

        if bright < 1.0:
            frame = {s: (int(r * bright), int(g * bright), int(b * bright))
                     for s, (r, g, b) in frame.items()}
        return frame
