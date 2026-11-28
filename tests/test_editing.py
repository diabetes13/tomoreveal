"""3.3 phase 3: editing the movie on the timeline (add, delete, resize, reorder, split, undo, keys)."""
import argparse
import os
import sys

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6 import QtTest, QtWidgets  # noqa: E402

import tomoreveal as tr  # noqa: E402
from tomoreveal import storyboard as sb  # noqa: E402


@pytest.fixture
def window(tmp_path, demo, needs_3d):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    win.resize(1400, 900)
    errors = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errors.append(v)
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    pump(app)
    assert win.st.ren is not None, "the 3D renderer did not start after loading"
    refresh(app, win)
    yield app, win, errors
    sys.excepthook = old; win.close()


def pump(app, n=30):
    for _ in range(n):
        app.processEvents()


def refresh(app, win):
    win.do_preview(); pump(app)


def ids(win):
    return [p["id"] for p in win.spec["shots"]]


def put_playhead_in(app, win, pid, u=0.5):
    c = win.timeline.canvas; start, count = c.parts[pid]["main"]
    f = start + int(count * u); c._move_playhead(f); win.req = {"frame": f}; refresh(app, win)


def test_add_after_the_part_under_the_playhead(window):
    app, win, errors = window
    a, b, c = ids(win)
    put_playhead_in(app, win, b)
    win.editor.add("hold"); refresh(app, win)
    new = ids(win)
    assert new[:2] == [a, b] and new[3] == c and win.spec["shots"][2]["type"] == "hold"
    assert win.selected_part == new[2] and win.timeline.canvas.selected == new[2]
    assert win.timeline.frame == win.timeline.canvas.parts[new[2]]["main"][0]
    win.editor.add_closeup(win.st.ds.key_of(2), "vesicles"); refresh(app, win)  # after the hold, which is now selected and under the playhead
    assert win.spec["shots"][3]["type"] == "focus" and ids(win)[4] == c
    assert not errors, errors


def test_delete_any_part_and_undo(window):
    app, win, errors = window
    first = ids(win)
    for _ in range(3):
        win.editor.delete(ids(win)[0]); refresh(app, win)
        sb.build_plan(win.spec_for(), win.st.ds)  # still a valid movie
    assert ids(win) == [] and not win.go_btn.isEnabled() and "no parts" in win.msg_lbl.text()
    win.editor.undo(); refresh(app, win)
    assert ids(win) == first[2:] and win.go_btn.isEnabled()
    win.editor.undo(); win.editor.undo(); refresh(app, win)
    assert ids(win) == first
    win.editor.redo(); refresh(app, win)
    assert ids(win) == first[1:]
    assert not errors, errors


def test_resize_by_dragging_the_right_edge(window):
    app, win, errors = window
    c = win.timeline.canvas; pid = ids(win)[1]; start, count = c.parts[pid]["main"]
    before = float(win.spec["shots"][1]["seconds"]); steps = len(win.editor.history.states)
    x = int(round(c.x_of(start + count))); y = 22 + 18
    assert c.edge_at(QtCore.QPointF(x, y)) == pid
    QtTest.QTest.mousePress(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x, y))
    for dx in (20, 40, 60):
        QtTest.QTest.mouseMove(c, QtCore.QPoint(x + dx, y)); pump(app, 2)
    QtTest.QTest.mouseRelease(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x + 60, y)); pump(app)
    expect = round(round((before + 60 / (c.ppf * 30)) / 0.1) * 0.1, 2)
    assert win.spec["shots"][1]["seconds"] == pytest.approx(expect, abs=0.101)
    assert len(win.editor.history.states) == steps + 1  # exactly one undo step
    win.editor.undo(); assert win.spec["shots"][1]["seconds"] == before
    assert not errors, errors


def test_a_click_and_then_a_slow_drag_of_the_edge_is_one_undo_step(window):
    """N-15: a click restarts the 600 ms history timer, and the drag writes each new length into the settings; when the
    timer fired during the drag, a half-way length became an undo step (two Undos needed, the first to a length never chosen)."""
    app, win, errors = window

    def pause(ms):
        loop = QtCore.QEventLoop(); QtCore.QTimer.singleShot(ms, loop.quit); loop.exec()
    pause(1000)  # nothing left over from the load
    c = win.timeline.canvas; pid = ids(win)[1]; start, count = c.parts[pid]["main"]; y = 22 + 18
    QtTest.QTest.mouseClick(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(int(c.x_of(start + count / 2)), y)); pump(app)
    before = float(win.spec["shots"][1]["seconds"]); steps = len(win.editor.history.states)
    x = int(round(c.x_of(start + count))); assert c.edge_at(QtCore.QPointF(x, y)) == pid
    QtTest.QTest.mousePress(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x, y))
    for dx in (20, 40, 60):  # about a second, as a hand drags
        QtTest.QTest.mouseMove(c, QtCore.QPoint(x + dx, y)); pause(300)
    QtTest.QTest.mouseRelease(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x + 60, y)); pump(app)
    assert len(win.editor.history.states) == steps + 1
    win.editor.undo(); assert win.spec["shots"][1]["seconds"] == before
    assert not errors, errors


def test_reorder_by_dragging_a_block(window):
    app, win, errors = window
    c = win.timeline.canvas; a, b, d = ids(win)
    start, count = c.parts[d]["main"]; x = int(c.x_of(start + count / 2)); y = 22 + 18
    target = int(c.x_of(c.parts[a]["start"]) + 2)
    QtTest.QTest.mousePress(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x, y))
    for xx in (x - 10, (x + target) // 2, target):
        QtTest.QTest.mouseMove(c, QtCore.QPoint(xx, y)); pump(app, 2)
    QtTest.QTest.mouseRelease(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(target, y)); pump(app)
    assert ids(win) == [d, a, b]
    assert not errors, errors


def test_split_then_hold_pauses_in_the_middle(window):
    app, win, errors = window
    up = ids(win)[0]; put_playhead_in(app, win, up)
    k_mid = win.timeline.canvas.plan.frames[win.timeline.frame]["k"]
    assert win.editor.split_btn.isEnabled()
    win.editor.split_btn.click(); refresh(app, win)
    assert len(ids(win)) == 4 and ids(win)[0] == up and win.selected_part == up
    assert sb.part_at_frame(win.timeline.canvas.plan, win.timeline.frame) == up  # playhead at the end of the first half
    win.editor.add("hold"); refresh(app, win)  # as the user does it: Split, then + Add part -> Hold
    hold = ids(win)[1]
    assert {f["k"] for f in win.timeline.canvas.plan.frames if f["part"] == hold} == {k_mid}
    assert not errors, errors


def test_shortcuts_only_act_on_the_timeline(window):
    app, win, errors = window
    win.editor.add_closeup(win.st.ds.key_of(2), "vesicles"); refresh(app, win)
    n = len(ids(win))
    caption = win.parts_panel.rows[win.selected_part]["caption"]  # the new close-up is open
    caption.setFocus(); QtTest.QTest.keyClick(caption, QtCore.Qt.Key_Delete); QtTest.QTest.keyClick(caption, QtCore.Qt.Key_Z, QtCore.Qt.ControlModifier)
    pump(app)
    assert len(ids(win)) == n
    c = win.timeline.canvas; c.setFocus()
    QtTest.QTest.keyClick(c, QtCore.Qt.Key_D, QtCore.Qt.ControlModifier); pump(app)
    assert len(ids(win)) == n + 1
    QtTest.QTest.keyClick(c, QtCore.Qt.Key_Delete); pump(app)
    assert len(ids(win)) == n
    QtTest.QTest.keyClick(c, QtCore.Qt.Key_Z, QtCore.Qt.ControlModifier); pump(app)
    assert len(ids(win)) == n + 1
    assert not errors, errors


def test_movie_after_edits_has_the_planned_frames(window, tmp_path):
    app, win, errors = window
    a, b, c = ids(win)
    put_playhead_in(app, win, b); win.editor.add("hold"); refresh(app, win)   # add
    win.editor.moved(c, 0); refresh(app, win)                                 # reorder: overview first
    win.editor.delete(a); refresh(app, win)                                   # delete the slide up
    put_playhead_in(app, win, b); win.editor.split(); refresh(app, win)       # split the reveal
    spec = win.spec_for(); spec["output"].update(size=[320, 180], fps=10, aa=False)
    for p in spec["shots"]:
        p["seconds"] = min(float(p["seconds"]), 1.0)
    n_plan = len(sb.build_plan(spec, win.st.ds).frames)
    out = str(tmp_path / "edited.mp4")
    n, secs = tr.render_movie(win.st.ren, spec, out)
    assert os.path.getsize(out) > 2000 and n == n_plan
    import imageio.v2 as imageio
    assert imageio.get_reader(out).count_frames() == n_plan
    assert not errors, errors


def test_undo_right_after_a_setting_change_undoes_only_that_change(window):
    """F-09: a change still waiting for the 600 ms history timer was skipped, and Undo took back the edit before it too."""
    app, win, errors = window
    first = ids(win)
    win.editor.delete(first[0]); refresh(app, win)                     # an edit, recorded at once
    before = win.spec["style"]["tomogram"]["opacity"]
    win.spec["style"]["tomogram"]["opacity"] = 0.25; win.editor.changed()  # a slider: recorded once things settle
    win.editor.undo()                                                     # within 600 ms
    assert ids(win) == first[1:] and win.spec["style"]["tomogram"]["opacity"] == before
    win.editor.redo()
    assert win.spec["style"]["tomogram"]["opacity"] == 0.25
    assert not errors, errors


@pytest.mark.parametrize("remove", ["undo", "delete"])
def test_closeup_still_added_when_its_place_is_gone(window, remove):
    """F-26: "+ Click a structure" remembered the part to insert after; if that part went away first, the click failed."""
    app, win, errors = window
    a = ids(win)[1]; put_playhead_in(app, win, a)
    win.editor.add("hold"); refresh(app, win); hold = ids(win)[2]
    win.editor.pick_closeup(after=hold)
    win.editor.undo() if remove == "undo" else win.editor.delete(hold)
    refresh(app, win)
    assert hold not in ids(win) and win.pick_btn.isChecked()
    win.st.pick = lambda *a, **k: dict(found=True, **{"class": win.st.ds.key_of(2)}, component=None, name="vesicles", voxels=1)
    win.on_click(0.5, 0.5)
    assert [p["type"] for p in win.spec["shots"]].count("focus") == 1
    assert not errors, errors

