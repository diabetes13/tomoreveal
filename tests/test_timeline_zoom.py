"""Plan 3.5 phase 1: timeline zoom that is easy to find (zoom bar, wheel, touchpad, keys, overview strip)."""
import argparse
import sys

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6 import QtGui, QtTest  # noqa: E402

from tomoreveal import storyboard as sb  # noqa: E402


@pytest.fixture
def window(tmp_path, demo, needs_3d):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    win.resize(1400, 900)
    errors = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errors.append(v)
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    pump(app)
    win.do_preview(); pump(app)
    yield app, win, errors
    sys.excepthook = old; win.close()


def pump(app, n=40):
    for _ in range(n):
        app.processEvents()


def wheel(app, c, x, dy=0, dx=0, mods=QtCore.Qt.NoModifier, pixel=QtCore.QPoint()):
    pos = QtCore.QPointF(x, 30)
    ev = QtGui.QWheelEvent(pos, QtCore.QPointF(c.mapToGlobal(pos.toPoint())), pixel, QtCore.QPoint(dx, dy), QtCore.Qt.NoButton, mods,
                           QtCore.Qt.NoScrollPhase, False)
    app.sendEvent(c, ev); pump(app, 5)


def test_fit_all_with_thirty_parts(window):
    app, win, errors = window
    for i in range(30):
        win.spec["shots"].append(sb.new_part("hold", seconds=0.3 + (i % 3)))
    win.do_preview(); pump(app)
    c = win.timeline.canvas; win.timeline.fit_btn.click(); pump(app)
    assert c.fit and abs(c.x_of(c.total) - (c.width() - 8)) < 1  # the whole movie fits the width
    groups = c.groups()
    assert sum(len(ids) for ids, _, _ in groups) == len(c.parts)
    for ids, x0, x1 in groups:
        assert x1 - x0 >= 3 - 1e-6
    # very zoomed out (a narrow window): neighbouring tiny parts merge into "several parts" marks
    c.resize(200, c.height()); c.fit_all()
    assert any(len(ids) > 1 for ids, _, _ in c.groups()) and all(x1 - x0 >= 3 - 1e-6 for _, x0, x1 in c.groups())
    assert not win.timeline.grab().toImage().isNull()
    assert not errors, errors


def test_minus_three_then_plus_three(window):
    app, win, errors = window
    c = win.timeline.canvas; tl = win.timeline
    tl.zoom_in_btn.click(); tl.zoom_in_btn.click(); tl.zoom_in_btn.click(); tl.zoom_in_btn.click()
    z = c.ppf
    for _ in range(3):
        tl.zoom_out_btn.click()
    for _ in range(3):
        tl.zoom_in_btn.click()
    assert c.ppf == pytest.approx(z, rel=1e-6)
    assert tl.strip.isVisibleTo(win) and tl.zoom_slider.value() > 0
    tl.zoom_slider.setValue(0); pump(app)
    assert c.fit and not tl.strip.isVisibleTo(win)
    assert not errors, errors


def test_ctrl_wheel_zooms_around_the_mouse_and_plain_wheel_scrolls(window):
    app, win, errors = window
    c = win.timeline.canvas; x = 300.0
    t0 = (x + c.offset - 8) / c.ppf
    wheel(app, c, x, dy=240, mods=QtCore.Qt.ControlModifier)
    assert not c.fit
    t1 = (x + c.offset - 8) / c.ppf
    assert abs((t1 - t0) * c.ppf) < 1  # the time under the mouse stays under it within 1 px
    z, off = c.ppf, c.offset
    wheel(app, c, x, dy=-120)
    assert c.ppf == z and c.offset != off  # scrolls, does not zoom
    off = c.offset; wheel(app, c, x, dx=-120, mods=QtCore.Qt.ShiftModifier)
    assert c.ppf == z and c.offset > off
    off = c.offset; wheel(app, c, x, pixel=QtCore.QPoint(-40, 0), dx=-40)  # touchpad swipe
    assert c.offset == pytest.approx(off + 40)
    # keys
    c.setFocus(); QtTest.QTest.keyClick(c, QtCore.Qt.Key_0, QtCore.Qt.ControlModifier); assert c.fit
    QtTest.QTest.keyClick(c, QtCore.Qt.Key_Plus, QtCore.Qt.ControlModifier); assert not c.fit
    QtTest.QTest.keyClick(c, QtCore.Qt.Key_Z, QtCore.Qt.ShiftModifier); assert c.fit
    assert not errors, errors


def test_overview_strip(window):
    app, win, errors = window
    c = win.timeline.canvas; tl = win.timeline; s = tl.strip
    c.set_zoom_level(0.5); pump(app)
    a, b = c.visible_frames(); r = s.view_rect()
    assert r.width() / (s.width() - 16) == pytest.approx((b - a) / c.total, abs=0.01)
    cx = int(r.center().x())
    QtTest.QTest.mousePress(s, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(cx, 6))
    QtTest.QTest.mouseMove(s, QtCore.QPoint(cx + 50, 6))
    QtTest.QTest.mouseRelease(s, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(cx + 50, 6)); pump(app, 5)
    a2, _ = c.visible_frames()
    assert (a2 - a) / c.total == pytest.approx(50 / (s.width() - 16), abs=0.01)  # scrolled by the same fraction
    assert not errors, errors


def test_zoom_to_part_and_middle_drag(window):
    app, win, errors = window
    c = win.timeline.canvas; pid = win.spec["shots"][1]["id"]
    c.selected = pid; assert c.zoom_to_part()
    d = c.parts[pid]; w = c.x_of(d["end"]) - c.x_of(d["start"])
    assert 0.75 * (c.width() - 16) <= w <= 0.85 * (c.width() - 16) + 1 and c.x_of(d["start"]) >= 0
    off = c.offset
    QtTest.QTest.mousePress(c, QtCore.Qt.MiddleButton, QtCore.Qt.NoModifier, QtCore.QPoint(400, 40))
    QtTest.QTest.mouseMove(c, QtCore.QPoint(350, 40))
    QtTest.QTest.mouseRelease(c, QtCore.Qt.MiddleButton, QtCore.Qt.NoModifier, QtCore.QPoint(350, 40))
    assert c.offset == pytest.approx(off + 50, abs=1)
    assert not errors, errors


def test_zoom_is_saved_with_the_project(window, tmp_path):
    app, win, errors = window
    c = win.timeline.canvas
    c.set_zoom_level(0.6); c.scroll_to_frame(40); win.timeline.compact_btn.setChecked(True)
    v = c.view_state(); assert not v["fit"] and v["compact"]
    path = str(tmp_path / "z.tomoreveal"); assert win.project.save_to(path)
    win.timeline.fit_btn.click(); win.timeline.compact_btn.setChecked(False)
    loop = QtCore.QEventLoop(); win.project.open(path)
    if win.worker is not None and win.worker.isRunning():
        win.worker.finished.connect(loop.quit); loop.exec()
    pump(app); win.do_preview(); pump(app)
    got = c.view_state()
    assert not got["fit"] and got["compact"] and got["seconds"] == pytest.approx(v["seconds"], rel=0.02)
    assert got["start"] == pytest.approx(v["start"], abs=0.05)
    assert not errors, errors
