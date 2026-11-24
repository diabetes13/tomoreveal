"""Plan 3.5 phase 1B: moving around in the preview (right drag moves, wheel zooms to the mouse, double-click centres,
right click opens a menu, view undo)."""
import argparse
import sys

import numpy as np
import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6 import QtTest  # noqa: E402

from tomoreveal import gui_navigate  # noqa: E402


@pytest.fixture
def window(tmp_path, demo, needs_3d):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    win.resize(1400, 900); win.show()
    errors = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errors.append(v)
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    pump(app); show_main(app, win)
    yield app, win, errors
    sys.excepthook = old; win.close()


def pump(app, n=30):
    for _ in range(n):
        app.processEvents()


def show_main(app, win):
    win.req = win._main_req(); win.prev_timer.stop(); win.do_preview(); pump(app)


def finish(app, win):
    """Let a glide end at once, then draw the frame fully."""
    nav = win.nav
    if nav._anim is not None:
        b = nav._anim["b"]; nav._anim = None; nav.anim.stop(); nav.apply(b, quick=False)
    win.prev_timer.stop(); win.do_preview(); pump(app)


def screen_of(win, P):
    (fx, fy), ok = win.st.ren.project(P); assert ok
    return win.prev.to_widget(fx, fy)


def test_right_drag_moves_the_point_under_the_mouse(window):
    app, win, errors = window
    f = (0.5, 0.55); P = win.st.ren.point_under(*f)[0]
    a = screen_of(win, P); v0 = dict(win.spec["view"])
    start = a.toPoint(); end = start + QtCore.QPoint(100, 0)
    QtTest.QTest.mousePress(win.prev, QtCore.Qt.RightButton, QtCore.Qt.NoModifier, start)
    for t in np.linspace(0, 1, 6)[1:]:
        QtTest.QTest.mouseMove(win.prev, start + QtCore.QPoint(round(100 * t), 0))
    QtTest.QTest.mouseRelease(win.prev, QtCore.Qt.RightButton, QtCore.Qt.NoModifier, end); pump(app)
    finish(app, win)
    b = screen_of(win, P)
    assert abs(b.x() - a.x() - 100) <= 2 and abs(b.y() - a.y()) <= 2, (a, b)
    assert win.spec["view"]["tilt"] == v0["tilt"] and win.spec["view"]["az"] == v0["az"]  # moved, not rotated
    assert "pan" in win.spec["view"]
    assert not errors, errors


def test_right_click_opens_the_menu_only_without_moving(window, monkeypatch):
    app, win, errors = window
    opened = []; monkeypatch.setattr(win.nav, "menu", lambda f, g: opened.append(f))
    c = win.prev.to_widget(0.5, 0.5).toPoint()
    for dx, menu in ((2, True), (10, False)):
        opened.clear(); pan0 = list(win.spec["view"].get("pan") or [0, 0, 0])
        QtTest.QTest.mousePress(win.prev, QtCore.Qt.RightButton, QtCore.Qt.NoModifier, c)
        QtTest.QTest.mouseMove(win.prev, c + QtCore.QPoint(dx, 0))
        QtTest.QTest.mouseRelease(win.prev, QtCore.Qt.RightButton, QtCore.Qt.NoModifier, c + QtCore.QPoint(dx, 0)); pump(app)
        assert bool(opened) == menu
        moved = list(win.spec["view"].get("pan") or [0, 0, 0]) != pan0
        assert moved != menu
    assert not errors, errors


def test_wheel_zooms_towards_the_mouse(window):
    app, win, errors = window
    f = (0.3, 0.6); P = win.st.ren.point_under(*f)[0]; a = screen_of(win, P); z0 = win.spec["view"]["zoom"]
    win.nav.wheel(240, f); finish(app, win)
    assert win.spec["view"]["zoom"] > z0
    b = screen_of(win, P)
    assert abs(b.x() - a.x()) <= 3 and abs(b.y() - a.y()) <= 3, (a, b)
    assert not errors, errors


def test_double_click_centres_and_undo_view_change(window):
    app, win, errors = window
    before = dict(win.spec["view"]); f = None
    for fx in np.linspace(0.3, 0.7, 9):
        if win.st.ren.pick_point(fx, 0.5) is not None:
            f = (fx, 0.5); break
    assert f is not None
    P = win.st.ren.pick_point(*f)
    win.nav.double(f); finish(app, win)
    _, foc, _, _, _ = win.st.ren.camera_frame()
    assert np.linalg.norm(foc - P) <= 1.0
    win.nav.undo(); finish(app, win)
    assert win.spec["view"] == before
    win.nav.redo(); finish(app, win)
    assert win.spec["view"] != before
    assert not errors, errors


def test_keys_and_fit(window):
    app, win, errors = window
    win.prev.setFocus(); v0 = dict(win.spec["view"])
    QtTest.QTest.keyClick(win.prev, QtCore.Qt.Key_Right, QtCore.Qt.ShiftModifier); finish(app, win)
    assert win.spec["view"]["az"] == pytest.approx(((v0["az"] - 5 + 180) % 360) - 180)
    QtTest.QTest.keyClick(win.prev, QtCore.Qt.Key_7); finish(app, win)
    assert win.spec["view"]["tilt"] == 0.0
    QtTest.QTest.keyClick(win.prev, QtCore.Qt.Key_Plus); finish(app, win)
    assert win.spec["view"]["zoom"] == pytest.approx(v0["zoom"] * 1.25, rel=1e-3)
    QtTest.QTest.keyClick(win.prev, QtCore.Qt.Key_Left); finish(app, win)
    assert "pan" in win.spec["view"]
    QtTest.QTest.keyClick(win.prev, QtCore.Qt.Key_F); finish(app, win)
    assert win.spec["view"]["zoom"] == 1.0 and "pan" not in win.spec["view"]
    assert gui_navigate.HINT in gui_navigate.HELP.replace("\n", " ") or gui_navigate.HINT
    assert not errors, errors


def test_menu_entries(window, monkeypatch):
    app, win, errors = window
    seen = []

    def fake_exec(menu, *a):
        for act in menu.actions():
            seen.append(act.text())
            if act.menu():
                seen.extend(x.text() for x in act.menu().actions())
    monkeypatch.setattr(gui_navigate, "exec_menu", fake_exec)
    f = None
    for fx in np.linspace(0.3, 0.7, 9):
        p = win.st.pick(win.spec_for(), {"frame": win.frame, "x": fx, "y": 0.5})
        if p.get("found"):
            f = (fx, 0.5); break
    assert f is not None
    win.nav.menu(f, QtCore.QPoint(0, 0))
    for t in ("Centre here", "Fit everything", "Undo view change", "Use this view for the selected part", "above"):
        assert t in seen, seen
    assert any(t.startswith("Zoom to") for t in seen) and any(t.startswith("Show only") for t in seen)
    assert not errors, errors
