"""PyQt5 system-tray control panel for the ks82rgb daemon.

Talks to the daemon over the same control socket the CLI uses (ipc.request), so
it just reflects/steers daemon state.  Tray menu covers the common actions;
"Control panel..." opens a compact window with the same controls plus a live
status line.  Run with ``ks82rgb gui``.
"""

import subprocess
import sys

from PyQt5 import QtCore, QtGui, QtWidgets

from . import ipc

REFRESH_MS = 2000


# --------------------------------------------------------------- daemon glue --
def _req(req, timeout=1.5):
    return ipc.request(req, timeout=timeout)


def _daemon_status():
    return _req({"cmd": "status"})


def _start_daemon():
    subprocess.run(["systemctl", "--user", "start", "ks82rgb.service"],
                   capture_output=True)


# ------------------------------------------------------------------- icon ------
def _make_icon():
    """Draw a small rainbow-keyboard icon so we don't ship a binary asset."""
    pm = QtGui.QPixmap(64, 64)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    grad = QtGui.QLinearGradient(0, 0, 64, 0)
    for i, hue in enumerate(range(0, 360, 40)):
        grad.setColorAt(i / 9.0, QtGui.QColor.fromHsv(hue, 255, 255))
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor(30, 30, 34))
    p.drawRoundedRect(4, 16, 56, 34, 8, 8)
    p.setBrush(grad)
    p.drawRoundedRect(9, 21, 46, 12, 3, 3)
    p.setBrush(QtGui.QColor(70, 70, 78))
    for x in range(11, 52, 8):
        p.drawRoundedRect(x, 37, 6, 8, 2, 2)
    p.end()
    return QtGui.QIcon(pm)


# --------------------------------------------------------------- controls ------
class ControlWindow(QtWidgets.QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle("KS82-B RGB")
        self.setMinimumWidth(300)
        v = QtWidgets.QVBoxLayout(self)

        self.status = QtWidgets.QLabel("...")
        self.status.setStyleSheet("font-weight: bold;")
        v.addWidget(self.status)

        v.addWidget(QtWidgets.QLabel("Mode"))
        self.modebox = QtWidgets.QComboBox()
        self.modebox.activated[str].connect(self.app.set_mode)
        v.addWidget(self.modebox)

        row = QtWidgets.QHBoxLayout()
        cbtn = QtWidgets.QPushButton("Solid color...")
        cbtn.clicked.connect(self.app.pick_color)
        off = QtWidgets.QPushButton("Off")
        off.clicked.connect(lambda: self.app.send({"cmd": "off"}))
        row.addWidget(cbtn)
        row.addWidget(off)
        v.addLayout(row)

        v.addWidget(QtWidgets.QLabel("Brightness"))
        self.bright = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.bright.setRange(0, 100)
        self.bright.sliderReleased.connect(
            lambda: self.app.set_brightness(self.bright.value() / 100.0))
        v.addWidget(self.bright)

    def refresh(self, st, modes):
        if not st:
            self.status.setText("daemon: not running")
            return
        self.status.setText(
            f"{st['mode']}  •  {int(st['brightness']*100)}%  •  "
            f"{'connected' if st['connected'] else 'DISCONNECTED'}")
        cur = [self.modebox.itemText(i) for i in range(self.modebox.count())]
        want = [m for m, _ in modes]
        if cur != want:
            self.modebox.blockSignals(True)
            self.modebox.clear()
            self.modebox.addItems(want)
            self.modebox.blockSignals(False)
        idx = self.modebox.findText(st["mode"])
        if idx >= 0:
            self.modebox.blockSignals(True)
            self.modebox.setCurrentIndex(idx)
            self.modebox.blockSignals(False)
        if not self.bright.isSliderDown():
            self.bright.blockSignals(True)
            self.bright.setValue(int(st["brightness"] * 100))
            self.bright.blockSignals(False)


# --------------------------------------------------------------- tray app ------
class TrayApp:
    def __init__(self):
        self.app = QtWidgets.QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.icon = _make_icon()
        self.tray = QtWidgets.QSystemTrayIcon(self.icon)
        self.menu = QtWidgets.QMenu()
        self.header = self.menu.addAction("KS82-B RGB")
        self.header.setEnabled(False)
        self.menu.addSeparator()
        self.modes_menu = self.menu.addMenu("Mode")
        self.menu.addAction("Solid color...", self.pick_color)
        self.menu.addAction("Off", lambda: self.send({"cmd": "off"}))
        self.bright_menu = self.menu.addMenu("Brightness")
        self._build_brightness_menu()
        self.audio_menu = self.menu.addMenu("Audio source (VU)")
        self.audio_menu.aboutToShow.connect(self._rebuild_audio)
        self.vu_menu = self.menu.addMenu("VU options")
        self.vu_menu.aboutToShow.connect(self._rebuild_vu_options)
        self.menu.addSeparator()
        self.win = ControlWindow(self)
        self.menu.addAction("Control panel...", self._show_window)
        self.menu.addAction("Start daemon", lambda: (_start_daemon(), self.refresh()))
        self.menu.addSeparator()
        self.menu.addAction("Quit tray", self.app.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip("KS82-B RGB")
        self.tray.activated.connect(self._on_activate)
        self.modes_menu.aboutToShow.connect(self._rebuild_modes)
        self.tray.show()

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(REFRESH_MS)
        self.refresh()

    # -- daemon actions --
    def send(self, req):
        r = _req(req)
        if r is None:
            self.tray.showMessage("KS82-B RGB", "Daemon not running.",
                                  QtWidgets.QSystemTrayIcon.Warning, 3000)
        self.refresh()
        return r

    def set_mode(self, name):
        self.send({"cmd": "set_mode", "name": name, "params": {}})

    def _vu_params(self):
        st = _daemon_status()
        return dict(st.get("params", {})) if st and st.get("mode") == "vu" else {}

    def set_vu_option(self, **kw):
        # switch to vu, preserving other vu params
        p = self._vu_params()
        p.update(kw)
        self.send({"cmd": "set_mode", "name": "vu", "params": p})

    def set_vu_device(self, device):
        self.set_vu_option(device=device)

    def set_brightness(self, value):
        self.send({"cmd": "brightness", "value": value})

    def pick_color(self):
        c = QtWidgets.QColorDialog.getColor(parent=None, title="Solid color")
        if c.isValid():
            self.send({"cmd": "solid", "color": [c.red(), c.green(), c.blue()]})

    # -- menu building --
    def _build_brightness_menu(self):
        for pct in (25, 50, 75, 100):
            self.bright_menu.addAction(
                f"{pct}%", lambda p=pct: self.set_brightness(p / 100.0))

    def _rebuild_modes(self):
        self.modes_menu.clear()
        r = _req({"cmd": "list_modes"})
        modes = r["modes"] if r else []
        st = _daemon_status()
        current = st["mode"] if st else None
        for name, kind in modes:
            act = self.modes_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == current)
            act.triggered.connect(lambda _=False, n=name: self.set_mode(n))

    def _rebuild_audio(self):
        self.audio_menu.clear()
        r = _req({"cmd": "audio_sources"})
        srcs = r["sources"] if r else []
        st = _daemon_status()
        cur = (st.get("params", {}).get("device")
               if st and st.get("mode") == "vu" else None)
        a = self.audio_menu.addAction("Default sink")
        a.setCheckable(True)
        a.setChecked(cur in (None, "default"))
        a.triggered.connect(lambda _=False: self.set_vu_device("default"))
        self.audio_menu.addSeparator()
        if not srcs:
            self.audio_menu.addAction("(no monitors found)").setEnabled(False)
        for s in srcs:
            label = ("● " if s["running"] else "   ") + s["label"]
            act = self.audio_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(s["name"] == cur)
            act.triggered.connect(lambda _=False, n=s["name"]: self.set_vu_device(n))

    def _rebuild_vu_options(self):
        self.vu_menu.clear()
        p = self._vu_params()
        cur_style = p.get("style", "spectrum")
        cur_dir = p.get("direction", "right")
        self.vu_menu.addAction("Style").setEnabled(False)
        for st in ("bar", "spectrum"):
            a = self.vu_menu.addAction("  " + st)
            a.setCheckable(True)
            a.setChecked(st == cur_style)
            a.triggered.connect(lambda _=False, v=st: self.set_vu_option(style=v))
        self.vu_menu.addSeparator()
        self.vu_menu.addAction("Direction").setEnabled(False)
        for d in ("right", "left", "up", "down"):
            a = self.vu_menu.addAction("  " + d)
            a.setCheckable(True)
            a.setChecked(d == cur_dir)
            a.triggered.connect(lambda _=False, v=d: self.set_vu_option(direction=v))

    # -- window / tray events --
    def _show_window(self):
        self.refresh()
        self.win.show()
        self.win.raise_()
        self.win.activateWindow()

    def _on_activate(self, reason):
        if reason == QtWidgets.QSystemTrayIcon.Trigger:  # left-click
            self._show_window()

    def refresh(self):
        st = _daemon_status()
        r = _req({"cmd": "list_modes"})
        modes = r["modes"] if r else []
        if st:
            self.header.setText(
                f"● {st['mode']}  —  {int(st['brightness']*100)}%")
            self.tray.setToolTip(
                f"KS82-B RGB: {st['mode']} @ {int(st['brightness']*100)}%")
        else:
            self.header.setText("daemon: not running")
            self.tray.setToolTip("KS82-B RGB: daemon offline")
        self.win.refresh(st, modes)

    def exec(self):
        return self.app.exec_()


def run():
    app = TrayApp()   # creates the QApplication first
    # isSystemTrayAvailable() must be called *after* a QApplication exists,
    # otherwise it dereferences a null qApp and segfaults.
    if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        print("No system tray available on this session.", file=sys.stderr)
    return app.exec()
