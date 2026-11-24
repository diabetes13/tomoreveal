"""3.3 phase 2: the timeline under the preview (blocks, playhead, keys)."""
import argparse
import sys

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6 import QtGui, QtTest  # noqa: E402


@pytest.fixture
def window(tmp_path, demo, needs_3d):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    win.resize(1400, 900)
    errors = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errors.append(v)
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    pump(app)
    assert win.st.ren is not None, "the 3D renderer did not start after loading"
    win.do_preview(); pump(app)
    yield app, win, errors
    sys.excepthook = old; win.close()


def pump(app, n=40):
    for _ in range(n):
        app.processEvents()


def test_one_block_per_part_without_gaps(window):
    app, win, errors = window
    c = win.timeline.canvas
    on = [p["id"] for p in win.spec["shots"] if p.get("on", True)]
    assert list(c.parts) == on
    rects = c.segment_rects()
    for (sg, r), (sg2, r2) in zip(rects, rects[1:]):
        assert abs(r.right() - r2.left()) < 0.01 or r.width() in (1.0, 3.0)  # no gaps, no overlaps
    for sg, r in rects:  # main blocks at least 3 px; transitions and pauses become thin lines when narrow
        assert abs(r.width() - max(3.0 if sg["phase"] == "main" else 1.0, sg["count"] * c.ppf)) < 1
    assert abs(rects[0][1].left() - 8) < 0.01 and rects[-1][1].right() <= c.width() - 7
    win.spec["shots"][0]["on"] = False; win.do_preview(); pump(app)
    assert list(c.parts) == on[1:]
    assert not errors, errors


def test_click_selects_a_part_and_moves_the_playhead(window):
    app, win, errors = window
    c = win.timeline.canvas; got = {}
    win.timeline.part_selected.connect(lambda pid: got.__setitem__("part", pid))
    win.timeline.frame_changed.connect(lambda f: got.__setitem__("frame", f))
    pid = win.spec["shots"][1]["id"]; start, count = c.parts[pid]["main"]
    x = c.x_of(start + count / 2); y = 22 + 18
    QtTest.QTest.mouseClick(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(int(x), y))
    pump(app)
    assert got["part"] == pid and start <= got["frame"] < start + count and win.selected_part == pid
    assert "part 2" in win.timeline.info.text()
    QtTest.QTest.mouseDClick(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(int(x), y)); pump(app)
    assert win.tabs.currentIndex() == win.TAB_MOVIE
    assert not errors, errors


def test_keys_move_the_playhead(window):
    app, win, errors = window
    c = win.timeline.canvas; c.setFocus(); f0 = c.frame
    QtTest.QTest.keyClick(c, QtCore.Qt.Key_Right); assert c.frame == f0 + 1
    QtTest.QTest.keyClick(c, QtCore.Qt.Key_Right, QtCore.Qt.ShiftModifier); assert c.frame == f0 + 31
    QtTest.QTest.keyClick(c, QtCore.Qt.Key_End); assert c.frame == c.total - 1
    QtTest.QTest.keyClick(c, QtCore.Qt.Key_Home); assert c.frame == 0
    QtTest.QTest.keyClick(c, QtCore.Qt.Key_Down)
    second = win.spec["shots"][1]["id"]
    assert c.frame == c.parts[second]["main"][0] and win.selected_part == second
    pump(app); win.do_preview(); pump(app)
    assert win.frame == c.frame  # the preview follows the playhead
    assert not errors, errors


def test_zoom_and_paint_in_both_themes(window):
    app, win, errors = window
    c = win.timeline.canvas; fit = c.ppf
    ev = QtGui.QWheelEvent(QtCore.QPointF(200, 30), QtCore.QPointF(c.mapToGlobal(QtCore.QPoint(200, 30))), QtCore.QPoint(),
                           QtCore.QPoint(0, 240), QtCore.Qt.NoButton, QtCore.Qt.ControlModifier, QtCore.Qt.NoScrollPhase, False)
    app.sendEvent(c, ev); pump(app)
    assert c.ppf > fit and not c.fit and win.timeline.scroll.isVisibleTo(win)
    win.timeline.fit_btn.click(); pump(app)
    assert c.fit and c.ppf == pytest.approx(fit)
    for theme in ("dark", "light"):
        win.set_theme(theme)
        assert not win.timeline.grab().toImage().isNull()
    assert not errors, errors
