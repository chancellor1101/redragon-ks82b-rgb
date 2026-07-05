"""Low-level LED control for the Redragon KS82-B (Sinowealth 258a:0049).

We talk to the kernel's ``/dev/hidrawN`` node directly and send the LED packet
as a HID **Feature report** via the ``HIDIOCSFEATURE`` ioctl (a USB SET_REPORT).

Why not hidapi?  The pip ``hidapi`` wheel uses its libusb backend, which reaches
the device through root-only ``/dev/bus/usb/...`` and would have to detach the
keyboard's kernel driver.  hidraw needs only the (udev-granted) ``/dev/hidraw``
node, coexists with the normal keyboard driver, and never disturbs typing.

Protocol (verified against the device report descriptor and the Sinodragon
project):

    byte 0        : 0x08   report id (vendor feature report)
    bytes 1..3    : 0x0A 0x7A 0x01   command header
    bytes 4..381  : 126 RGB triples (378 bytes), one per key LED slot
    total         : 382 bytes

Only this LED feature report is ever sent -- no firmware / bootloader / flash
commands -- so it cannot brick the keyboard.
"""

import fcntl
import glob
import os

from . import layout

VENDOR_ID = 0x258A
PRODUCT_ID = 0x0049
CONTROL_INTERFACE = "01"                   # IF1 = vendor control channel

PACKET_HEADER = [0x08, 0x0A, 0x7A, 0x01]
PACKET_LENGTH = 382                         # 4 header + 126*3 = 382

_HID_ID_TOKEN = f"{VENDOR_ID:04X}:{PRODUCT_ID:08X}"   # e.g. 258A:00000049


class DeviceError(RuntimeError):
    pass


def _hidiocsfeature(length):
    """ioctl request for HIDIOCSFEATURE(length): _IOC(READ|WRITE, 'H', 0x06, len)."""
    return (3 << 30) | (length << 16) | (ord("H") << 8) | 0x06


def _interface_number(hidraw_sysfs):
    """Walk up from a hidraw sysfs dir to its USB interface's bInterfaceNumber."""
    d = os.path.realpath(os.path.join(hidraw_sysfs, "device"))
    for _ in range(6):
        f = os.path.join(d, "bInterfaceNumber")
        if os.path.exists(f):
            with open(f) as fh:
                return fh.read().strip()
        d = os.path.dirname(d)
    return None


def _find_node():
    """Return the /dev/hidrawN path for the keyboard's control interface (IF1)."""
    matches = []
    for sysfs in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(sysfs, "device", "uevent")) as fh:
                uevent = fh.read().upper()
        except OSError:
            continue
        if _HID_ID_TOKEN not in uevent:
            continue
        matches.append(sysfs)
        if _interface_number(sysfs) == CONTROL_INTERFACE:
            return "/dev/" + os.path.basename(sysfs)
    if not matches:
        raise DeviceError(
            f"No keyboard found at {VENDOR_ID:04x}:{PRODUCT_ID:04x}. Plugged in?")
    # Fallback: only one hidraw for it -> use that.
    return "/dev/" + os.path.basename(matches[-1])


def build_packet(slot_colors):
    """Build the 382-byte feature report.

    `slot_colors` maps slot index (0..125) -> (r, g, b); missing slots -> black.
    """
    packet = bytearray(PACKET_HEADER)
    for s in range(layout.NUM_SLOTS):
        col = slot_colors.get(s, (0, 0, 0))
        packet.extend(
            (max(0, min(255, int(col[0]))),
             max(0, min(255, int(col[1]))),
             max(0, min(255, int(col[2])))))
    if len(packet) < PACKET_LENGTH:
        packet.extend(bytes(PACKET_LENGTH - len(packet)))
    return bytes(packet[:PACKET_LENGTH])


class Keyboard:
    """Context-managed handle to the keyboard's LED control node."""

    def __init__(self):
        self._fd = None
        self.node = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def open(self):
        self.node = _find_node()
        try:
            self._fd = os.open(self.node, os.O_RDWR)
        except OSError as e:
            raise DeviceError(
                f"Cannot open {self.node}: {e}. "
                "Install the udev rule and run `sudo udevadm trigger` "
                "(see README), or run once with sudo."
            ) from e
        return self

    def close(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None

    def send(self, slot_colors):
        """Push one frame. `slot_colors`: slot index -> (r,g,b)."""
        if self._fd is None:
            raise DeviceError("Device not open")
        pkt = build_packet(slot_colors)
        try:
            fcntl.ioctl(self._fd, _hidiocsfeature(len(pkt)), pkt)
        except OSError as e:
            raise DeviceError(f"Failed to send LED report: {e}") from e
