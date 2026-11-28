"""3.3 phase 2: the timeline under the preview (blocks, playhead, keys)."""
import argparse
import sys

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6 import QtGui, QtTest, QtWidgets  # noqa: E402


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


def test_arrow_bars_and_here_use_the_clock_of_the_movie(window):
    """The bars and "here" counted from the main phase, the movie from the pause before it."""
    from tomoreveal import annotations as an
    from tomoreveal import storyboard as sb
    app, win, errors = window
    s = sb.new_part("slide", seconds=2, pause_before=1.5, pause_after=1.5)
    a = an.new_arrow([128.0, 160.0, 20.0]); a["show"]["appear"] = "pop"
    b = an.new_arrow([100.0, 100.0, 20.0]); b["show"].update(start=0.5, end=2.5, appear="pop")
    win.spec["shots"] = [s]; win.spec["annotations"] = [a, b]
    win.prev_timer.stop(); win.do_preview(); pump(app)
    c = win.timeline.canvas; plan = c.plan
    for bar in c.ann_bars():
        shows = [j for j, f in enumerate(plan.frames) if bar[0] in f.get("ann", {})]
        j0, j1 = c.frame_at(bar[2] + 0.01), c.frame_at(bar[3] - 0.01)
        assert j0 == shows[0] and j1 - shows[-1] == (0 if bar[0] == a["id"] else 1), bar[0]  # (a fade-out reaches 0 at the end time)
    j = max(i for i, f in enumerate(plan.frames) if f["phase"] == "pause")  # the end of the pause after the slide
    win.timeline.set_frame(j); win.on_scrub(j); win.prev_timer.stop(); win.do_preview(); pump(app, 5)
    assert win.frame == j
    win.ann_panel.at_playhead(b["id"], "end")
    assert an.find(win.spec, b["id"])["show"]["end"] == pytest.approx(round(plan.frames[j]["tp"], 2)) and not errors


def test_dragging_the_end_of_a_bar_keeps_a_start_before_the_part(window):
    """A start before the part (left by a split) was set to the edge of the part when the end was dragged; dragging the
    start or the whole bar starts from where the bar is drawn."""
    from tomoreveal import annotations as an
    from tomoreveal import storyboard as sb
    from tomoreveal.gui_timeline import LANE_H
    app, win, errors = window
    s = sb.new_part("slide", seconds=6)
    a = an.new_arrow([128.0, 160.0, 20.0], parts=[s["id"]]); a["show"].update(start=-2.37, end=3.0)
    win.spec["shots"] = [s]; win.spec["annotations"] = [a]
    win.prev_timer.stop(); win.do_preview(); pump(app); win.ann_panel.rebuild(); pump(app)
    assert an.find(win.spec, a["id"])["show"]["start"] == -2.37  # (filling its row does not write it back)
    c = win.timeline.canvas
    def drag(at, dx):
        b = c.ann_bars()[0]; y = c.lanes_top() + b[4] * LANE_H + LANE_H // 2; x = int(round(b[2] if at == "start" else b[3] if at == "end" else (b[2] + b[3]) / 2))
        assert c.ann_at(QtCore.QPointF(x, y))[1] == at
        QtTest.QTest.mousePress(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x, y))
        QtTest.QTest.mouseMove(c, QtCore.QPoint(x + dx, y)); pump(app, 2)
        QtTest.QTest.mouseRelease(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x + dx, y)); pump(app)
        win.prev_timer.stop(); win.do_preview(); pump(app)
        return an.find(win.spec, a["id"])["show"]
    one = round(c.ppf * 30)  # pixels per second
    sh = drag("end", one); assert sh["start"] == -2.37 and sh["end"] == pytest.approx(4.0, abs=0.11)
    sh = drag("start", one); assert sh["start"] == pytest.approx(1.0, abs=0.11)  # (from 0, where it is drawn)
    sh["start"] = -2.37; sh["end"] = 3.0; win.do_preview(); pump(app)
    sh = drag("move", one); assert (sh["start"], sh["end"]) == (pytest.approx(1.0, abs=0.11), pytest.approx(4.0, abs=0.11))  # (the bar drawn from 0 to 3 s)
    assert not errors, errors


def test_delete_after_clicking_an_arrow_bar_deletes_the_arrow(window):
    """Delete means what was clicked last: an arrow's bar, or else the selected part. A redraw of the preview keeps it."""
    from tomoreveal import annotations as an
    from tomoreveal.gui_timeline import LANE_H
    app, win, errors = window
    pid = win.spec["shots"][1]["id"]; n = len(win.spec["shots"])
    a = an.new_arrow([128.0, 160.0, 20.0], parts=[pid]); win.spec["annotations"] = [a]; win.editor.commit()
    win.prev_timer.stop(); win.do_preview(); pump(app)
    c = win.timeline.canvas; b = c.ann_bars()[0]
    QtTest.QTest.mouseClick(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                            QtCore.QPoint(int((b[2] + b[3]) / 2), c.lanes_top() + b[4] * LANE_H + LANE_H // 2)); pump(app)
    win.prev_timer.stop(); win.do_preview(); pump(app)  # (the preview selects the part again at every redraw)
    QtTest.QTest.keyClick(c, QtCore.Qt.Key_Delete); pump(app)
    assert an.find(win.spec, a["id"]) is None and len(win.spec["shots"]) == n
    win.editor.undo(); pump(app)
    assert an.find(win.spec, a["id"]) is not None and len(win.spec["shots"]) == n
    win.prev_timer.stop(); win.do_preview(); pump(app)
    start, count = c.parts[pid]["main"]
    QtTest.QTest.mouseClick(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(int(c.x_of(start + count / 2)), 22 + 18)); pump(app)
    QtTest.QTest.keyClick(c, QtCore.Qt.Key_Delete); pump(app)
    assert len(win.spec["shots"]) == n - 1 and pid not in [p["id"] for p in win.spec["shots"]]
    assert not errors, errors


def test_the_zoom_row_is_by_the_text_and_a_long_name_does_not_widen_the_timeline(window):
    """G-21: the zoom row sits on the line of text, not in the button row; that text shortens with "..." instead."""
    app, win, errors = window
    tl = win.timeline
    assert tl.buttons.indexOf(tl.zoom_bar) == -1 and tl.info_row.indexOf(tl.zoom_bar) >= 0
    assert tl.buttons.indexOf(win.play_btn) == tl.buttons.count() - 1  # Play stays at the right end of the buttons
    w0 = tl.minimumSizeHint().width()
    from tomoreveal import storyboard as sb
    long = "x" * 100
    key = win.st.ds.key_of(2); win.spec["classes"].setdefault(key, {})["name"] = long  # a structure with a long name
    part = sb.new_part("focus", target=sb.make_target([(key, None)]), motion="none"); win.editor.insert(part); win.do_preview(); pump(app)
    start, count = tl.canvas.parts[part["id"]]["main"]; tl.set_frame(start + count // 2); pump(app, 5)
    shown = long in tl.info.text()
    assert shown, tl.info.text()
    assert tl.minimumSizeHint().width() == w0, (tl.minimumSizeHint().width(), w0)
    tl.info.resize(200, tl.info.height()); pump(app, 5)
    assert tl.info.text().endswith(long) and tl.info.toolTip() == tl.info.text()  # the whole text, also in the tooltip
    assert QtWidgets.QLabel.text(tl.info).endswith("…")  # what is drawn
    assert not errors, errors
