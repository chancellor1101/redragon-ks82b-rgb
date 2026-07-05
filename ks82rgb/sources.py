"""Sources: the pluggable units that produce LED frames.

A Source renders a frame -- a ``{slot: (r,g,b)}`` dict -- given a time ``t``.
Built-in sources wrap the effects in ``effects.py``; utility sources (CPU meter,
audio VU, ...) and user plugins register the same way.

Register a source by decorating its class with ``@register``.  Drop-in plugins
live in ``~/.config/ks82rgb/plugins/*.py`` and are loaded at daemon start; each
plugin module just imports ``register``/``Source`` and decorates its classes.
"""

import os
import runpy

from . import effects, layout

_REGISTRY = {}


class Source:
    """Base class. Subclasses set ``name`` and implement ``render(t)``.

    ``kind`` is metadata for the GUI: "effect" (decorative) or "utility"
    (data-driven).  ``describe()`` returns the params needed to recreate it, so
    the daemon can persist and restore the active source.
    """

    name = "base"
    kind = "effect"

    def __init__(self, **params):
        self.params = params

    def render(self, t):
        return {}

    def describe(self):
        return {"name": self.name, "params": dict(self.params)}

    def close(self):
        """Release any resources (audio streams, fds). Called on switch-away."""


def register(cls):
    _REGISTRY[cls.name] = cls
    return cls


def create(name, **params):
    if name not in _REGISTRY:
        raise KeyError(f"unknown source: {name!r}")
    return _REGISTRY[name](**params)


def names():
    return sorted(_REGISTRY)


def catalog():
    """[(name, kind)] for the GUI / `list-modes`."""
    return sorted((n, c.kind) for n, c in _REGISTRY.items())


def load_plugins(plugin_dir):
    """Execute every ``*.py`` in `plugin_dir` so their @register calls run."""
    if not plugin_dir or not os.path.isdir(plugin_dir):
        return []
    loaded = []
    for fn in sorted(os.listdir(plugin_dir)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        path = os.path.join(plugin_dir, fn)
        try:
            # init_globals gives plugins easy access to the plugin API
            runpy.run_path(path, init_globals={
                "register": register, "Source": Source,
                "effects": effects, "layout": layout,
            })
            loaded.append(fn)
        except Exception as e:  # a bad plugin must not crash the daemon
            print(f"[ks82rgb] plugin {fn} failed to load: {e}")
    return loaded


# --------------------------------------------------------------- built-ins ----
@register
class SolidSource(Source):
    name = "solid"
    kind = "effect"

    def __init__(self, color=(255, 255, 255), **kw):
        super().__init__(color=list(color))
        self._frame = {s: tuple(color) for s in layout.lit_slots()}

    def render(self, t):
        return self._frame


@register
class StaticSource(Source):
    """A fixed per-key frame (from `solid`, `key`, or a static profile)."""

    name = "static"
    kind = "effect"

    def __init__(self, frame=None, **kw):
        frame = frame or {}
        self._frame = {int(k): tuple(v) for k, v in frame.items()}
        super().__init__(frame={str(k): list(v) for k, v in self._frame.items()})

    def render(self, t):
        return self._frame


class _EffectSource(Source):
    """Wraps an effects.py factory. Subclasses set `name` and `_factory`."""

    kind = "effect"
    _factory = None
    _defaults = {}

    def __init__(self, **params):
        merged = {**self._defaults, **params}
        super().__init__(**merged)
        self._frame_fn = self._make(merged)

    def _make(self, params):
        return type(self)._factory(**params)

    def render(self, t):
        return self._frame_fn(t)


@register
class WaveSource(_EffectSource):
    name = "wave"
    _factory = staticmethod(effects.wave)
    _defaults = {"period": 6.0, "spread": 1.4}


@register
class RainbowSource(_EffectSource):
    name = "rainbow"
    _factory = staticmethod(effects.rainbow)
    _defaults = {"period": 8.0}


@register
class BreathingSource(_EffectSource):
    name = "breathing"
    _defaults = {"color": (0, 200, 255), "period": 4.0}

    def _make(self, params):
        p = dict(params)
        color = tuple(p.pop("color"))
        return effects.breathing(color, **p)
