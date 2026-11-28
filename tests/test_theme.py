"""Phase 6: light and dark window themes, remembered in QSettings."""
import argparse

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6 import QtGui, QtWidgets  # noqa: E402


@pytest.fixture
def window(tmp_path, demo):
    from tomoreveal.gui import build_gui
    # a temporary settings file (conftest makes IniFormat the default), so the user's own settings are never touched
    QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, str(tmp_path / "settings"))
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(40):
        app.processEvents()
    yield app, win
    win.set_theme("light"); win.close()


def lightness(c):
    return c.lightnessF()


def test_dark_and_light_and_remembered(window):
    app, win = window
    win.set_theme("dark")
    pal = app.palette()
    assert lightness(pal.color(QtGui.QPalette.Window)) < 0.3 and lightness(pal.color(QtGui.QPalette.WindowText)) > 0.7
    assert app.style().name().lower() == "fusion"
    assert QtCore.QSettings().value("theme") == "dark"
    assert win.theme_acts["dark"].isChecked() and win.theme_cb.currentData() == "dark"
    win.say("Something went wrong", "err")
    assert QtGui.QColor(win.msg_lbl.styleSheet().split(":")[1]).lightnessF() > 0.5  # red that reads on dark
    win.set_theme("light")
    pal = app.palette()
    assert lightness(pal.color(QtGui.QPalette.Window)) > 0.7 and lightness(pal.color(QtGui.QPalette.WindowText)) < 0.3
    assert QtCore.QSettings().value("theme") == "light"
    assert QtGui.QColor(win.msg_lbl.styleSheet().split(":")[1]).lightnessF() < 0.5   # and the same message on light


def test_custom_painted_widgets_in_both_themes(window):
    app, win = window
    for choice in ("dark", "light", "system"):
        win.set_theme(choice)
        for w in (win.timeline.canvas, win.clip_s["x"]):
            w.resize(300, 70); img = w.grab().toImage()
            assert not img.isNull()


def test_choice_is_used_when_the_window_opens(tmp_path):
    from tomoreveal.gui import build_gui
    QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, str(tmp_path / "settings"))
    QtCore.QSettings().setValue("theme", "dark")
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    try:
        assert lightness(app.palette().color(QtGui.QPalette.Window)) < 0.3 and win.theme_acts["dark"].isChecked()
    finally:
        win.set_theme("light"); win.close()


def test_settings_go_to_the_test_folder_not_the_registry(window, tmp_path):
    """F-18: QSettings("TomoReveal", "TomoReveal") always used the registry on Windows, so tests wrote the user's settings."""
    app, win = window
    win.set_theme("dark"); win.settings.sync()  # Qt writes the file on sync
    assert (tmp_path / "settings" / "TomoReveal" / "TomoReveal.ini").exists()
    assert QtCore.QSettings().value("theme") == "dark"


def test_light_is_light_also_when_the_system_is_dark(monkeypatch):
    """N-25: Fusion's standard palette follows the system's colour scheme in Qt 6, so Light was dark grey on a dark Windows."""
    from tomoreveal import gui_theme
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    real = QtWidgets.QStyleFactory.create("Fusion").standardPalette()  # off-screen there is no dark mode: Fusion's light colours
    roles = [r for r in QtGui.QPalette.ColorRole if r not in (QtGui.QPalette.NColorRoles, QtGui.QPalette.PlaceholderText)]
    groups = (QtGui.QPalette.Active, QtGui.QPalette.Inactive, QtGui.QPalette.Disabled)
    assert all(gui_theme.light_palette().color(g, r).rgba() == real.color(g, r).rgba() for g in groups for r in roles)  # the look is unchanged

    class DarkFusion:  # what Fusion gives while Windows is in dark mode
        def standardPalette(self):
            return gui_theme.dark_palette()
    monkeypatch.setattr(QtWidgets.QStyleFactory, "create", staticmethod(lambda name: DarkFusion()))
    pal = gui_theme.light_palette()
    assert lightness(pal.color(QtGui.QPalette.Window)) > 0.7 and lightness(pal.color(QtGui.QPalette.WindowText)) < 0.3


def test_the_colour_scheme_told_to_qt_follows_the_choice():
    """N-25 (7-D1): Light and Dark are told to Qt as well, so the title bar and Qt's own dialogs follow; System lets the desktop decide."""
    from tomoreveal.gui_theme import qt_color_scheme
    S = QtCore.Qt.ColorScheme
    assert (qt_color_scheme("light"), qt_color_scheme("dark"), qt_color_scheme("system")) == (S.Light, S.Dark, S.Unknown)
