"""Utility (data-driven) modes: CPU meter and audio VU meter.

Imported at the end of sources.py so their @register calls run.
"""

import colorsys
import math
import shutil
import struct
import subprocess
import threading
import time

from . import layout
from .sources import Source, register


def default_monitor():
    """The default sink's monitor source name, or None."""
    try:
        sink = subprocess.check_output(
            ["pactl", "get-default-sink"], text=True).strip()
        return sink + ".monitor" if sink else None
    except Exception:
        return None


def list_monitor_sources():
    """Available monitor sources: [{name, label, running}] for the GUI chooser."""
    out = []
    default = default_monitor()
    try:
        lines = subprocess.check_output(
            ["pactl", "list", "short", "sources"], text=True).splitlines()
    except Exception:
        return out
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 2 or not parts[1].endswith(".monitor"):
            continue
        name = parts[1]
        state = parts[-1] if parts else ""
        # friendlier label: drop the alsa_output prefix and .monitor suffix
        label = name.replace("alsa_output.", "").replace(".monitor", "")
        label = label.replace("usb-", "").replace("pci-", "")
        out.append({"name": name, "label": label[:48],
                    "running": state.upper() == "RUNNING",
                    "default": name == default})
    return out


def _hsv(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


# ----------------------------------------------------------------- CPU meter --
def _read_cpu():
    """(total, idle) jiffies from /proc/stat's aggregate cpu line."""
    with open("/proc/stat") as f:
        vals = [int(x) for x in f.readline().split()[1:]]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)   # idle + iowait
    return sum(vals), idle


@register
class CpuMeterSource(Source):
    """Whole-board tint by CPU load: calm pale-green -> hot dark-red."""

    name = "cpu"
    kind = "utility"

    def __init__(self, **kw):
        super().__init__()
        self._last = _read_cpu()
        self._last_t = -1.0
        self._usage = 0.0

    def render(self, t):
        if t - self._last_t >= 0.3:
            total, idle = _read_cpu()
            dt, di = total - self._last[0], idle - self._last[1]
            if dt > 0:
                u = 1.0 - di / dt
                self._usage += (u - self._usage) * 0.5    # EMA smoothing
            self._last, self._last_t = (total, idle), t
        u = max(0.0, min(1.0, self._usage))
        # calm: pale green (high value, low saturation); hot: dark saturated red
        col = _hsv((150 * (1 - u)) / 360.0, 0.35 + 0.65 * u, 1.0 - 0.55 * u)
        return {s: col for s in layout.lit_slots()}


# ------------------------------------------------------------------ audio VU --
_RATE = 44100
_WINDOW = 2048                                       # FFT window (~46ms)
_NBANDS = 6                                          # key rows (y = slot % 6)
# log-spaced band edges (Hz), bass -> treble; _NBANDS+1 edges
_BAND_EDGES = [40, 120, 300, 700, 1700, 4000, 16000]
_DIRECTIONS = ("right", "left", "up", "down")
_STYLES = ("bar", "spectrum")


@register
class AudioVUSource(Source):
    """Audio meter from a chosen monitor. Two styles, four directions.

    style="bar":      one meter for overall loudness.
    style="spectrum": one bar per key row, each row a frequency band
                      (bass at the bottom -> treble at the top).
    direction:        right/left/up/down (bar); right/left (spectrum fill).
    device:           monitor name, or "default"/None to follow the default sink.

    The reader self-heals -- if parec dies or the output switches, it
    re-resolves the device and restarts.
    """

    name = "vu"
    kind = "utility"

    def __init__(self, gain=4.0, device=None, style="spectrum", direction="right",
                 **kw):
        style = style if style in _STYLES else "spectrum"
        direction = direction if direction in _DIRECTIONS else "right"
        super().__init__(gain=gain, device=device, style=style, direction=direction)
        self.gain = gain
        self.device = device
        self.style = style
        self.direction = direction
        self._level = 0.0
        self._bands = [0.0] * _NBANDS
        self._stop = threading.Event()
        self._proc = None
        slots = layout.lit_slots()
        self._xmax = max((layout.position(s)[0] for s in slots), default=1)
        self._ymax = max((layout.position(s)[1] for s in slots), default=1)
        threading.Thread(target=self._reader, daemon=True).start()

    def _resolve_device(self):
        if self.device and self.device != "default":
            return self.device
        return default_monitor() or "@DEFAULT_MONITOR@"

    # -------------------------------------------------------------- capture --
    def _reader(self):
        if not shutil.which("parec"):
            print("[ks82rgb] vu: `parec` not found; VU meter will stay flat.")
            return
        try:
            import numpy as np
        except ImportError:
            np = None
            print("[ks82rgb] vu: numpy not found; spectrum falls back to bar.")

        hann = band_bins = None
        if np is not None:
            hann = np.hanning(_WINDOW).astype(np.float32)
            freqs = np.fft.rfftfreq(_WINDOW, 1.0 / _RATE)
            band_bins = [(int(np.searchsorted(freqs, _BAND_EDGES[i])),
                          int(np.searchsorted(freqs, _BAND_EDGES[i + 1])))
                         for i in range(_NBANDS)]

        read_n = 1024                                # samples per read (~23ms)
        while not self._stop.is_set():
            dev = self._resolve_device()
            try:
                self._proc = subprocess.Popen(
                    ["parec", "--format=s16le", "--rate=%d" % _RATE,
                     "--channels=1", "-d", dev],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except Exception as e:
                print(f"[ks82rgb] vu: could not start parec: {e}")
                time.sleep(2)
                continue
            buf = np.zeros(_WINDOW, dtype=np.float32) if np is not None else None
            while not self._stop.is_set():
                data = self._proc.stdout.read(read_n * 2)
                if not data:
                    break                            # parec exited -> re-resolve
                n = len(data) // 2
                if not n:
                    continue
                if np is not None:
                    s = np.frombuffer(data[:n * 2], dtype="<i2").astype(np.float32)
                    rms = float(np.sqrt(np.mean(s * s))) / 32768.0
                    buf = np.roll(buf, -n)
                    buf[-n:] = s
                    if self.style == "spectrum":
                        self._update_bands(np, buf, hann, band_bins)
                else:
                    ss = struct.unpack(f"<{n}h", data[:n * 2])
                    rms = math.sqrt(sum(x * x for x in ss) / n) / 32768.0
                lvl = min(1.0, rms * self.gain)
                self._level = max(lvl, self._level * 0.85)  # fast attack, slow release
            if not self._stop.is_set():
                time.sleep(1.0)                      # device gone; retry

    def _update_bands(self, np, buf, hann, band_bins):
        spec = np.abs(np.fft.rfft(buf * hann)) / (_WINDOW * 32768.0)
        for i, (lo, hi) in enumerate(band_bins):
            e = float(spec[lo:hi].sum()) if hi > lo else 0.0
            lvl = min(1.0, math.sqrt(e) * self.gain * 0.75)
            self._bands[i] = max(lvl, self._bands[i] * 0.80)

    def close(self):
        self._stop.set()
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

    # --------------------------------------------------------------- render --
    def _lit(self, frac, pos, axis_max):
        """Whether a cell at `pos` (along the meter axis) is lit for `frac`."""
        if self.direction in ("right", "down"):
            return pos <= frac * axis_max
        return pos >= axis_max - frac * axis_max     # left / up

    def render(self, t):
        if self.style == "spectrum":
            return self._render_spectrum()
        return self._render_bar()

    def _render_bar(self):
        vertical = self.direction in ("up", "down")
        axis_max = self._ymax if vertical else self._xmax
        frame = {}
        for s in layout.lit_slots():
            x, y = layout.position(s)
            pos = y if vertical else x
            if self._lit(self._level, pos, axis_max):
                frac = pos / max(1, axis_max)
                frame[s] = _hsv((1 - frac) * 0.33, 1.0, 1.0)  # green -> red
            else:
                frame[s] = (2, 2, 6)
        return frame

    def _render_spectrum(self):
        frame = {}
        for s in layout.lit_slots():
            x, y = layout.position(s)
            band = self._ymax - y                    # bass at the bottom row
            band = max(0, min(_NBANDS - 1, band))
            lvl = self._bands[band]
            lit = (x <= lvl * self._xmax if self.direction != "left"
                   else x >= self._xmax - lvl * self._xmax)
            if lit:
                hue = (band / max(1, _NBANDS - 1)) * 0.66   # bass red -> treble blue
                frame[s] = _hsv(hue, 1.0, 1.0)
            else:
                frame[s] = (2, 2, 6)
        return frame

    def render(self, t):
        fill = self._level * self._xmax
        frame = {}
        for s in layout.lit_slots():
            x = layout.position(s)[0]
            if x <= fill:
                frac = x / max(1, self._xmax)         # green(0) -> red(1)
                frame[s] = _hsv((1 - frac) * 0.33, 1.0, 1.0)
            else:
                frame[s] = (2, 2, 6)                  # dim unlit portion
        return frame
