"""3.3 phase 6: playing the movie in the preview."""
import argparse
import sys
import time

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6 import QtTest  # noqa: E402


@pytest.fixture
def window(tmp_path, demo, needs_3d):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    win.resize(1200, 800)
    errors = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errors.append(v)
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(30):
        app.processEvents()
    assert win.st.ren is not None, "the 3D renderer did not start after loading"
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


def test_nothing_draws_while_files_are_loading(window, demo, monkeypatch):
    """F-08: playback, picking and Explore kept using Studio and the renderer while the worker thread replaced them."""
    import threading
    import tomoreveal.gui_explore as gx
    app, win, errors = window
    draws, slices, picks = [], [], []
    real_draw, real_slice, real_load = win.st.ren.draw, gx.slice_image, win.st.load
    monkeypatch.setattr(win.st.ren, "draw", lambda *a, **k: draws.append(1) or real_draw(*a, **k))
    monkeypatch.setattr(gx, "slice_image", lambda *a, **k: slices.append(1) or real_slice(*a, **k))
    monkeypatch.setattr(win.st, "pick", lambda *a, **k: picks.append(1) or {"found": False})
    go = threading.Event()
    monkeypatch.setattr(win.st, "load", lambda *a, **k: go.wait(10) and real_load(*a, **k))
    win.play_btn.click(); run_for(app, 0.1)
    assert win.playing
    win.pick_btn.setChecked(True)
    win.load(paths=[demo["tomogram"], demo["labels"]])
    draws.clear(); slices.clear()
    run_for(app, 0.3)                    # longer than a play tick
    win.on_click(0.5, 0.5)               # a click on the preview in pick mode
    win.explore.step(1)                  # an arrow key on the Explore page
    try:
        assert not draws and not slices and not picks
        assert not win.playing and not win.pick_btn.isChecked()
        assert not win.timeline.isEnabled() and not win.play_btn.isEnabled() and not win.explore.page.isEnabled()
    finally:
        go.set()
        loop = QtCore.QEventLoop(); win.worker.finished.connect(loop.quit)
        if win.worker.isRunning():
            loop.exec()
        run_for(app, 0.2)
    assert win.timeline.isEnabled() and win.explore.page.isEnabled() and not errors, errors


def test_closing_during_playback(window, capsys):
    """F-41: the play timer kept drawing after the window closed and the renderer was gone (AttributeError)."""
    app, win, errors = window
    win.play_btn.click(); run_for(app, 0.2); capsys.readouterr()
    win.close(); run_for(app, 0.3)
    assert not errors and "Error" not in capsys.readouterr().err, errors


def test_closing_during_a_load(window, demo, monkeypatch):
    """F-41: closing during a load made a new renderer for the closed window when the load finished."""
    import threading
    app, win, errors = window
    made = []; go = threading.Event(); real_load = win.st.load
    monkeypatch.setattr(win.st, "ensure_renderer", lambda *a, **k: made.append(1))
    monkeypatch.setattr(win.st, "load", lambda *a, **k: go.wait(10) and real_load(*a, **k))
    win.load(paths=[demo["tomogram"], demo["labels"]])
    threading.Timer(0.3, go.set).start()
    win.close(); run_for(app, 0.3)
    assert not made and not win.worker.isRunning() and not errors, errors

