"""3.3 phase 6: playing the movie in the preview."""
import argparse
import sys
import time

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6 import QtTest  # noqa: E402


@pytest.fixture
def window(tmp_path, demo):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    win.resize(1200, 800)
    errors = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errors.append(v)
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(30):
        app.processEvents()
    if win.st.ren is None:
        pytest.skip("3D rendering is not available here")
    win.do_preview()
    yield app, win, errors
    win.stop_play(); sys.excepthook = old; win.close()


def run_for(app, seconds):
    loop = QtCore.QEventLoop(); QtCore.QTimer.singleShot(int(seconds * 1000), loop.quit); loop.exec()


def test_plays_in_real_time_from_the_playhead(window):
    app, win, errors = window
    win.timeline.set_frame(100); f0 = win.timeline.frame
    t = time.monotonic(); win.play_btn.click()
    assert win.playing and win.play_btn.text() == "Stop"
    run_for(app, 1.0); wall = time.monotonic() - t
    moved = win.timeline.frame - f0
    assert moved == pytest.approx(wall * 30, abs=8)          # keeps real time
    assert 0 < win.playing["drawn"] <= moved + 1            # skips frames when drawing is slower
    assert win.frame == win.timeline.frame
    QtTest.QTest.mouseClick(win.timeline.canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(60, 5))
    assert not win.playing and win.play_btn.text() == "Play"  # a click on the timeline stops it
    assert not errors, errors


def test_stops_at_the_end_and_space_toggles(window):
    app, win, errors = window
    c = win.timeline.canvas; last = c.total - 1
    win.timeline.set_frame(last - 10); c.setFocus()
    QtTest.QTest.keyClick(c, QtCore.Qt.Key_Space)
    assert win.playing
    run_for(app, 0.8)
    assert not win.playing and win.timeline.frame == last
    QtTest.QTest.keyClick(c, QtCore.Qt.Key_Space)   # at the end, Space starts again from the beginning
    assert win.playing and win.playing["f0"] == 0
    QtTest.QTest.keyClick(c, QtCore.Qt.Key_Space)
    assert not win.playing
    assert not errors, errors


def test_never_while_a_movie_is_made(window):
    app, win, errors = window
    win.play_btn.click(); assert win.playing
    win.quality_cb.setCurrentIndex(0)
    for p in win.spec["shots"]:
        p["seconds"] = 0.3
    win.name_le.setText("play_check"); win.make_movie()
    assert not win.playing and win.gen is not None and not win.play_btn.isEnabled()
    win.start_play(); assert not win.playing
    while win.gen is not None:
        app.processEvents()
    assert win.play_btn.isEnabled()
    win.tabs.setCurrentIndex(win.TAB_MOVIE); win.play_btn.click(); assert win.playing
    win.tabs.setCurrentIndex(win.TAB_EXPLORE)
    assert not win.playing  # switching to Explore stops it
    assert not errors, errors
