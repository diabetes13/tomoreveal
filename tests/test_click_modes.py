"""Clicks in the preview for a task: one click mode at a time, and each ends where it should (6-D3)."""
import argparse
import itertools
import sys

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6 import QtTest  # noqa: E402

from tomoreveal import annotations as an  # noqa: E402
from tomoreveal import gui_clicks as clk  # noqa: E402
from tomoreveal import storyboard as sb  # noqa: E402


def pump(app, n=20):
    for _ in range(n):
        app.processEvents()


def wait_load(app, win):
    if win.worker is not None and win.worker.isRunning():
        loop = QtCore.QEventLoop(); win.worker.finished.connect(loop.quit); loop.exec()
    pump(app, 40)


@pytest.fixture
def window(tmp_path, demo, needs_3d):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True)); win.resize(1400, 900)
    errs = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errs.append(v)
    win.load(paths=[demo["tomogram"], demo["labels"]]); wait_load(app, win)
    if win.st.ren is None:
        pytest.skip("3D rendering is not available here")
    key = win.st.ds.key_of(2)
    a = sb.new_part("focus", target=sb.make_target([(key, None)]), caption="")
    b = sb.new_part("focus", target=sb.make_target([(key, None)]), caption="")
    pts = sb.new_part("path", seconds=4, source=dict(kind="points", items=[]),
                      waypoints=[dict(pos=[60.0, 60.0, 120.0], focal=[80.0, 90.0, 24.0]), dict(pos=[200.0, 260.0, 120.0], focal=[180.0, 230.0, 24.0])])
    fol = sb.new_part("path", seconds=4, source=dict(kind="follow", items=[{"structure": key, "component": None}]))
    arrow = an.new_arrow([128.0, 160.0, 20.0])
    win.spec["shots"] += [a, b, pts, fol]; win.spec["annotations"] = [arrow]; win.editor.commit()
    win.prev_timer.stop(); win.do_preview(); pump(app)
    ids = dict(a=a["id"], b=b["id"], pts=pts["id"], fol=fol["id"], arrow=arrow["id"], key=key)
    yield app, win, errs, ids
    sys.excepthook = old; win.close()


def modes(ids):
    """The eight click modes, each as the window starts it."""
    return {
        "new close-up": lambda: clk.NewCloseup(None),
        "object of a close-up": lambda: clk.CloseupObject(ids["a"], ids["key"]),
        "several objects": lambda: clk.CloseupObjects(ids["a"]),
        "path points": lambda: clk.PathPoints(ids["pts"]),
        "object of a path": lambda: clk.PathObject(ids["fol"]),
        "region": lambda: clk.Region(None, None),
        "new arrow": lambda: clk.NewArrow(),
        "more pointers": lambda: clk.MorePointers(ids["arrow"]),
    }


def is_off(win):
    return (win.clicks.mode is None and not win.pick_btn.isChecked() and not win.prev.box_mode
            and win.prev.cursor().shape() == QtCore.Qt.ArrowCursor)


def test_never_two_click_modes_at_once(window):
    app, win, errs, ids = window
    for (n1, m1), (n2, m2) in itertools.permutations(modes(ids).items(), 2):
        win.clicks.start(m1()); first = win.clicks.mode; win.clicks.start(m2())
        m = win.clicks.mode
        assert m is not first and type(m) is type(m2()), (n1, n2)
        assert win.pick_btn.isChecked() == isinstance(m, (clk.NewCloseup, clk.CloseupObject)), (n1, n2)
        assert win.prev.box_mode == isinstance(m, clk.Region) and win.prev.cursor().shape() == QtCore.Qt.CrossCursor, (n1, n2)
        win.clicks.end(); assert is_off(win), (n1, n2)
    # "object of close-up A", then "an object I click" (the same button, already on): only the new close-up
    win.clicks.start(clk.CloseupObject(ids["a"], ids["key"])); win.editor.pick_closeup()
    assert isinstance(win.clicks.mode, clk.NewCloseup) and win.pick_btn.isChecked()
    assert not errs, errs


@pytest.mark.parametrize("name", list(modes({k: None for k in ("a", "pts", "fol", "arrow", "key")})))
def test_every_click_mode_ends_with_esc_new_a_load_and_a_movie(window, demo, name):
    app, win, errs, ids = window
    make = modes(ids)[name]
    win.clicks.start(make()); win.tabs.setCurrentIndex(win.TAB_LOOK); win.tabs.setCurrentIndex(win.TAB_MOVIE)
    assert win.clicks.mode is not None  # not on a tab change
    QtTest.QTest.keyClick(win, QtCore.Qt.Key_Escape); assert is_off(win)
    win.clicks.start(make()); win.set_busy(True); win.set_busy(False); assert is_off(win)  # loading data
    win.clicks.start(make()); win.quality_cb.setCurrentIndex(0)
    for p in win.spec["shots"]:
        p["seconds"] = 0.3
    win.ask_left_out = lambda text: True  # (the parts made here that a movie leaves out)
    win.name_le.setText("clicks_check"); win.make_movie(); assert win.gen is not None and is_off(win)  # a movie
    win.cancel_render(); pump(app)
    win.clicks.start(make()); win.project.confirm_discard = lambda what: True; win.start_over(); assert is_off(win)  # New
    assert not errs, errs


def test_open_ends_a_click_mode(window, tmp_path):
    app, win, errs, ids = window
    path = str(tmp_path / "clicks.tomoreveal"); assert win.project.save_to(path)
    win.clicks.start(clk.CloseupObjects(ids["a"])); win.project.open(path, ask=False); wait_load(app, win)
    assert is_off(win) and not errs, errs


@pytest.mark.parametrize("name", ["object of a close-up", "several objects", "path points", "object of a path", "more pointers"])
def test_a_mode_for_a_part_ends_when_another_part_is_selected_or_opened(window, name):
    app, win, errs, ids = window
    make = modes(ids)[name]; own = make().part
    for how in ("select", "open"):
        win.select_part(own or ids["a"])  # (an arrow's mode: the part selected when it started)
        win.clicks.start(make())
        win.select_part(ids["b"]) if how == "select" else win.parts_panel.open(ids["b"])
        assert is_off(win), how
    if own is not None:  # its own part keeps it
        win.clicks.start(make()); win.select_part(own); win.parts_panel.open(own); assert win.clicks.mode is not None
    assert not errs, errs


def remove(win, ids, own):
    from tomoreveal.gui_annotations import delete
    win.editor.delete(own) if own else delete(win, ids["arrow"])


@pytest.mark.parametrize("how", ["delete", "undo", "redo"])
@pytest.mark.parametrize("name", ["object of a close-up", "several objects", "path points", "object of a path", "more pointers"])
def test_a_mode_ends_when_its_part_or_arrow_is_gone(window, name, how):
    app, win, errs, ids = window
    make = modes(ids)[name]; own = make().part
    if how == "redo":  # the redo of a delete
        remove(win, ids, own); win.editor.undo(); pump(app)
    win.clicks.start(make())
    if how == "delete":
        remove(win, ids, own)
    elif how == "undo":  # of the step that added the parts and the arrow
        win.editor.undo()
    else:
        win.editor.redo()
    pump(app); assert is_off(win)
    assert not errs, errs


def test_path_points_end_when_the_path_no_longer_goes_through_points(window):
    app, win, errs, ids = window
    win.clicks.start(clk.PathPoints(ids["pts"]))
    part = win.parts_panel.part(ids["pts"]); part["source"] = dict(kind="follow", items=[{"structure": ids["key"], "component": None}])
    win.editor.commit(select=ids["pts"]); pump(app)
    assert is_off(win) and not errs, errs


@pytest.mark.parametrize("name", ["new close-up", "region", "new arrow"])
def test_a_mode_for_something_new_stays_when_parts_are_selected_deleted_or_undone(window, name):
    app, win, errs, ids = window
    win.clicks.start(modes(ids)[name]())
    win.select_part(ids["b"]); win.parts_panel.open(ids["a"]); win.editor.delete(ids["b"]); win.editor.undo(); pump(app)
    assert type(win.clicks.mode) is type(modes(ids)[name]())
    assert not errs, errs


def test_picking_for_a_close_up_ends_when_another_part_is_selected(window):
    """G-50: 'Pick an object' stayed on for part A after part B was selected, and the next click changed A."""
    app, win, errs, ids = window
    a = win.parts_panel.part(ids["a"]); before = dict(a["target"])
    win.select_part(ids["a"]); win.clicks.start(clk.CloseupObject(ids["a"], ids["key"])); win.select_part(ids["b"])
    win.st.pick_at = lambda *a: dict(found=True, **{"class": ids["key"]}, component=1, name="vesicles", voxels=1)
    win.on_click(0.5, 0.5); pump(app)
    assert win.parts_panel.part(ids["a"])["target"] == before and win.clicks.mode is None
    assert not errs, errs


def test_a_cancelled_click_does_not_keep_its_place(window):
    """G-57: after a cancelled "an object I click", a new close-up from the button went to the old place."""
    app, win, errs, ids = window
    win.editor.pick_closeup(after=ids["fol"]); QtTest.QTest.keyClick(win, QtCore.Qt.Key_Escape)
    first = win.spec["shots"][0]["id"]
    win.req = {"at": {"part": first, "u": 0.5}}; win.prev_timer.stop(); win.do_preview(); pump(app)
    win.timeline.set_frame(win.frame)
    win.pick_btn.setChecked(True)
    win.st.pick_at = lambda *a: dict(found=True, **{"class": ids["key"]}, component=None, name="vesicles", voxels=1)
    n = len(win.spec["shots"]); win.on_click(0.5, 0.5); pump(app)
    shots = win.spec["shots"]
    assert len(shots) == n + 1 and shots[1]["type"] == "focus" and shots[1]["id"] not in ids.values()  # after part 1
    assert not errs, errs


def test_esc_ends_the_click_mode_first_and_then_the_path_editor(window):
    """G-56: with the path editor on and 'Add point by clicking' on, Esc ended the wrong one."""
    app, win, errs, ids = window
    win.select_part(ids["pts"]); win.req = win._main_req(); win.prev_timer.stop(); win.do_preview(); pump(app)
    assert win.editing_path() is not None
    win.clicks.start(clk.PathPoints(ids["pts"]))
    QtTest.QTest.keyClick(win, QtCore.Qt.Key_Escape)
    assert is_off(win) and win.editing_path() is not None
    QtTest.QTest.keyClick(win, QtCore.Qt.Key_Escape)
    assert win.editing_path() is None
    assert not errs, errs


@pytest.mark.parametrize("name", ["new close-up", "object of a close-up", "object of a path"])
def test_a_click_on_nothing_keeps_the_mode_on(window, name):
    """A miss says so and lets you try again; only a click that does the task ends a one-click mode."""
    app, win, errs, ids = window
    win.st.pick_at = lambda *a: dict(found=False)
    win.st.ren.pick_point = lambda *a: None
    win.clicks.start(modes(ids)[name]()); n = len(win.spec["shots"]), len(win.spec["annotations"])
    win.on_click(0.5, 0.5)
    assert "Nothing there" in win.msg_lbl.text()  # (read at once: the follow path made in the background clears it when done)
    pump(app)
    assert type(win.clicks.mode) is type(modes(ids)[name]()) and (len(win.spec["shots"]), len(win.spec["annotations"])) == n
    assert not errs, errs


@pytest.mark.parametrize("name", list(modes({k: None for k in ("a", "pts", "fol", "arrow", "key")})))
def test_a_click_mode_keeps_its_cursor_and_gets_clicks_on_labels(window, name):
    """N-09: over the legend the cross cursor was lost at the first move, and a click there went to the legend."""
    app, win, errs, ids = window
    win.req = win._main_req(); win.prev_timer.stop(); win.do_preview(); pump(app)
    box = win.prev.overlay.rects()["legend"][0]
    at = win.prev.to_widget((box[0] + box[2]) / 2, (box[1] + box[3]) / 2).toPoint()
    m = modes(ids)[name](); got = []
    m.click = lambda w, fx, fy: got.append((fx, fy)); win.clicks.start(m)
    QtTest.QTest.mouseMove(win.prev, at); QtTest.QTest.mouseMove(win.prev, at + QtCore.QPoint(2, 1)); pump(app, 5)
    assert win.prev.cursor().shape() == QtCore.Qt.CrossCursor
    lg = dict(win.spec.get("overlays", {}).get("legend", {}))
    QtTest.QTest.mouseClick(win.prev, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, at); pump(app, 5)
    assert dict(win.spec.get("overlays", {}).get("legend", {})) == lg
    assert got if not m.box else win.clicks.mode is m  # (a frame is drawn by dragging; a click alone draws none)
    assert not errs, errs


def test_path_points_stay_draggable_while_points_are_added(window):
    app, win, errs, ids = window
    win.select_part(ids["pts"]); win.req = win._main_req(); win.prev_timer.stop(); win.do_preview(); pump(app)
    r = win.prev.overlay.rects().get("marker:0")
    assert r is not None
    f = ((r[0][0] + r[0][2]) / 2, (r[0][1] + r[0][3]) / 2)
    win.clicks.start(clk.PathPoints(ids["pts"]))
    assert win.prev.overlay.press(f, QtCore.Qt.NoModifier) and win.prev.overlay.drag["mode"] == "marker"
    win.prev.overlay.drag = None
    win.clicks.start(clk.NewArrow())
    assert not win.prev.overlay.press(f, QtCore.Qt.NoModifier)  # other modes get the click
    assert not errs, errs
