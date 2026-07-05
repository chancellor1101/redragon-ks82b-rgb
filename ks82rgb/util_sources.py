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
@register
class AudioVUSource(Source):
    """Left-to-right VU bar (green->red) from a chosen audio monitor.

    `device` selects the capture source: a monitor name, or "default"/None to
    follow the default sink.  The reader self-heals -- if parec dies or the
    device disappears (output switched), it re-resolves and restarts.
    """

    name = "vu"
    kind = "utility"

    def __init__(self, gain=4.0, device=None, **kw):
        super().__init__(gain=gain, device=device)
        self.gain = gain
        self.device = device
        self._level = 0.0
        self._stop = threading.Event()
        self._proc = None
        self._xmax = max((layout.position(s)[0] for s in layout.lit_slots()),
                         default=1)
        threading.Thread(target=self._reader, daemon=True).start()

    def _resolve_device(self):
        if self.device and self.device != "default":
            return self.device
        return default_monitor() or "@DEFAULT_MONITOR@"

    def _reader(self):
        if not shutil.which("parec"):
            print("[ks82rgb] vu: `parec` not found; VU meter will stay flat.")
            return
        frame_bytes = 882 * 2                       # ~20ms @ 44.1kHz, mono s16
        while not self._stop.is_set():
            dev = self._resolve_device()
            try:
                self._proc = subprocess.Popen(
                    ["parec", "--format=s16le", "--rate=44100",
                     "--channels=1", "-d", dev],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except Exception as e:
                print(f"[ks82rgb] vu: could not start parec: {e}")
                time.sleep(2)
                continue
            while not self._stop.is_set():
                data = self._proc.stdout.read(frame_bytes)
                if not data:
                    break                            # parec exited -> re-resolve
                n = len(data) // 2
                if not n:
                    continue
                samples = struct.unpack(f"<{n}h", data[:n * 2])
                rms = math.sqrt(sum(s * s for s in samples) / n) / 32768.0
                lvl = min(1.0, rms * self.gain)
                self._level = max(lvl, self._level * 0.85)  # fast attack, slow release
            if not self._stop.is_set():
                time.sleep(1.0)                      # device gone; retry

    def close(self):
        self._stop.set()
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

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
