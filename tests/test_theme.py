"""Phase 6: light and dark window themes, remembered in QSettings."""
import argparse

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6 import QtGui, QtWidgets  # noqa: E402


@pytest.fixture
def window(tmp_path, demo):
    from tomoreveal.gui import build_gui
    # a temporary settings scope, so the user's own settings are never touched
    QtCore.QSettings.setPath(QtCore.QSettings.NativeFormat, QtCore.QSettings.UserScope, str(tmp_path / "settings"))
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
    assert QtCore.QSettings("TomoReveal", "TomoReveal").value("theme") == "dark"
    assert win.theme_acts["dark"].isChecked() and win.theme_cb.currentData() == "dark"
    win.say("Something went wrong", "err")
    assert QtGui.QColor(win.msg_lbl.styleSheet().split(":")[1]).lightnessF() > 0.5  # red that reads on dark
    win.set_theme("light")
    pal = app.palette()
    assert lightness(pal.color(QtGui.QPalette.Window)) > 0.7 and lightness(pal.color(QtGui.QPalette.WindowText)) < 0.3
    assert QtCore.QSettings("TomoReveal", "TomoReveal").value("theme") == "light"
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
    QtCore.QSettings.setPath(QtCore.QSettings.NativeFormat, QtCore.QSettings.UserScope, str(tmp_path / "settings"))
    QtCore.QSettings("TomoReveal", "TomoReveal").setValue("theme", "dark")
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    try:
        assert lightness(app.palette().color(QtGui.QPalette.Window)) < 0.3 and win.theme_acts["dark"].isChecked()
    finally:
        win.set_theme("light"); win.close()
