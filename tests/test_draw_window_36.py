"""3.6 phase 6 in the window: moving an arrow's tip (R16), the drawing toolbar (R3, R17) and separate layer movies (R12)."""
import json

import numpy as np
from PySide6 import QtCore, QtTest

from tomoreveal import annotations as an
from tomoreveal import drawing

from test_arrows import vesicle_centre, window  # noqa: F401 (fixture)


def pump(app, n=8):
    for _ in range(n):
        app.processEvents()


def show_middle(app, win):
    req = win._main_req(); req["at"]["u"] = 0.5; win.req = req; win.prev_timer.stop(); win.do_preview(); pump(app)


def drag(app, win, f0, f1, steps=6, mods=QtCore.Qt.NoModifier, middle=None):
    p0, p1 = win.prev.to_widget(*f0).toPoint(), win.prev.to_widget(*f1).toPoint()
    QtTest.QTest.mousePress(win.prev, QtCore.Qt.LeftButton, mods, p0)
    for i, t in enumerate(np.linspace(0, 1, steps + 1)[1:]):
        q = QtCore.QPoint(int(round(p0.x() + (p1.x() - p0.x()) * t)), int(round(p0.y() + (p1.y() - p0.y()) * t)))
        QtTest.QTest.mouseMove(win.prev, q); pump(app, 2)
        if middle is not None and i == steps // 2:
            middle()
    QtTest.QTest.mouseRelease(win.prev, QtCore.Qt.LeftButton, mods, p1); pump(app)


def other_spot(win, ds, not_this):
    for fx in np.linspace(0.25, 0.75, 11):
        for fy in np.linspace(0.25, 0.75, 11):
            h = win.st.pick(win.spec_for(), {"frame": win.frame, "x": fx, "y": fy})
            if h.get("found") and (h["class"], h.get("component")) != not_this and win.st.ren.pick_point(fx, fy) is not None:
                return (float(fx), float(fy)), h
    raise AssertionError("no other structure in view")


def empty_spot(win):
    for fx in np.linspace(0.02, 0.98, 25):
        for fy in (0.03, 0.97, 0.5):
            if win.st.ren.pick_point(fx, fy) is None:
                return (float(fx), float(fy))
    raise AssertionError("no empty spot")


def tip_of(win, a):
    r = win.st.ren.last_rects[f"anntip:{a['id']}"]["box"]; W, H = win.st.ren.size
    return ((r[0] + r[2]) / 2 / W, (r[1] + r[3]) / 2 / H)


def test_drag_the_tip_onto_another_structure(window):
    app, win, errs = window
    ds = win.st.ds; a = an.new_arrow(vesicle_centre(ds), ds.key_of(2), 1, "v")
    win.spec["annotations"] = [a]; win.editor.commit(); show_middle(app, win)
    dst, hit = other_spot(win, ds, (ds.key_of(2), 1))
    view0 = json.dumps(win.spec["view"], sort_keys=True); n0 = len(win.editor.history.states)
    seen = {}

    def middle():  # the history timer fires in the middle of a slow drag: still one undo step (3.6 fix)
        win.editor.hist_timer.timeout.emit(); seen["under"] = win.ann_mouse.drag.get("under")
    drag(app, win, tip_of(win, a), dst, middle=middle)
    b = an.find(win.spec, a["id"]); win.req = {"frame": win.frame}; win.do_preview()
    want = win.st.ren.pick_point(*win.prev.frac(win.prev.to_widget(*dst).toPoint()))
    assert b.get("place") == "3d" and np.linalg.norm(np.asarray(b["anchor"]["xyz"]) - want) <= 1.0
    assert b["anchor"]["structure"] == hit["class"] and seen["under"] is not None
    assert len(win.editor.history.states) == n0 + 1 and json.dumps(win.spec["view"], sort_keys=True) == view0  # camera still
    win.editor.undo(); pump(app)
    assert np.allclose(an.find(win.spec, a["id"])["anchor"]["xyz"], vesicle_centre(ds))
    # onto empty space: the tip goes there and stays on the picture, instead of not moving at all
    show_middle(app, win); e = empty_spot(win)
    drag(app, win, tip_of(win, a), e)
    b = an.find(win.spec, a["id"])
    assert an.is_2d(b) and abs(b["tip2d"][0] - e[0]) < 0.02 and abs(b["tip2d"][1] - e[1]) < 0.02
    assert not errs, errs


def test_move_tip_from_the_menu_and_the_pin_button(window):
    from tomoreveal import gui_annotations as ga
    app, win, errs = window
    ds = win.st.ds; a = an.new_arrow(vesicle_centre(ds), ds.key_of(2), 1, "v")
    win.spec["annotations"] = [a]; win.editor.commit(); show_middle(app, win)
    dst, hit = other_spot(win, ds, (ds.key_of(2), 1))
    ga.start_move_tip(win, a["id"]); assert win.arrow_pick == ("tip", a["id"])
    win.on_click(*dst); pump(app)
    b = an.find(win.spec, a["id"]); assert not win.arrow_pick and b["anchor"]["structure"] == hit["class"]
    show_middle(app, win)
    win.ann_mouse.selected = [a["id"]]; win.drawbar.sync(); assert win.drawbar.pin.isChecked()
    win.drawbar.pin.click(); pump(app)  # stays on the picture now
    assert an.is_2d(an.find(win.spec, a["id"])) and not win.drawbar.pin.isChecked()
    win.drawbar.pin.click(); pump(app)
    assert not an.is_2d(an.find(win.spec, a["id"]))
    assert not errs, errs


def test_draw_arrows_and_shapes(window):
    app, win, errs = window
    ds = win.st.ds; win.spec["annotations"] = []; win.editor.commit(); show_middle(app, win)
    dst, hit = other_spot(win, ds, (None, None))
    # an arrow: tail to tip, pinned to the structure under the tip
    n0 = len(win.editor.history.states)
    win.drawbar.set_tool("arrow"); drag(app, win, (dst[0] - 0.15, dst[1] - 0.12), dst)
    a = an.items(win.spec)[-1]
    assert a["kind"] == "arrow" and not an.is_2d(a) and a["anchor"]["structure"] == hit["class"]
    assert len(win.editor.history.states) == n0 + 1 and win.drawbar.tool() == "select"
    W, H = win.st.ren.size
    assert abs(a["tail"]["angle"] - (180 - np.degrees(np.arctan2(0.12 * H, 0.15 * W)))) < 2  # up and to the left, as drawn
    # a freehand arrow: 120 mouse positions become a few points; one undo step
    win.drawbar.pin.setChecked(False); win.drawbar._label()
    win.drawbar.set_tool("freehand"); n0 = len(win.editor.history.states)
    t = np.linspace(0, np.pi, 120); pts = [(0.2 + 0.25 * x / np.pi, 0.75 - 0.15 * np.sin(x)) for x in t]
    p = [win.prev.to_widget(*q).toPoint() for q in pts]
    QtTest.QTest.mousePress(win.prev, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, p[0])
    for q in p[1:]:
        QtTest.QTest.mouseMove(win.prev, q)
    QtTest.QTest.mouseRelease(win.prev, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, p[-1]); pump(app)
    fh = an.items(win.spec)[-1]
    assert fh["kind"] == "freehand" and an.is_2d(fh) and 3 <= len(fh["points"]) < 40
    assert len(win.editor.history.states) == n0 + 1
    # a curved arrow, then its bend handle
    win.drawbar.set_tool("curve"); drag(app, win, (0.6, 0.8), (0.85, 0.8))
    cv = an.items(win.spec)[-1]; assert cv["kind"] == "curve" and len(cv["points"]) == 3
    show_middle(app, win)
    r = win.st.ren.last_rects[f"anncurve:{cv['id']}"]["box"]; W, H = win.st.ren.size
    m0 = ((r[0] + r[2]) / 2 / W, (r[1] + r[3]) / 2 / H)
    drag(app, win, m0, (m0[0], m0[1] + 0.1))
    P = np.asarray(an.find(win.spec, cv["id"])["points"])
    assert np.allclose(drawing.bezier_middle(P[0], P[1], P[2]), (m0[0], m0[1] + 0.1), atol=0.01)  # the middle followed the handle
    # an ellipse and a rectangle
    for tool in ("ellipse", "rect"):
        win.drawbar.set_tool(tool); drag(app, win, (0.1, 0.1), (0.25, 0.3))
        sh = an.items(win.spec)[-1]; assert sh["kind"] == tool and np.allclose(sh["points"], [(0.1, 0.1), (0.25, 0.3)], atol=0.01)
    show_middle(app, win)
    for b in an.items(win.spec):
        assert f"ann:{b['id']}" in win.st.ren.last_rects, b["kind"]
    win.editor.undo(); pump(app); assert an.items(win.spec)[-1]["kind"] == "ellipse"
    win.drawbar.set_tool("line"); QtTest.QTest.keyClick(win, QtCore.Qt.Key_Escape); assert win.drawbar.tool() == "select"
    win.ann_panel.only_part.setChecked(False); win.ann_panel.rebuild(); pump(app)
    assert len(win.ann_panel._row_w) == len(an.items(win.spec))
    seen = []
    from tomoreveal import gui_annotations as ga
    ga.exec_menu, old = (lambda m, pos: seen.extend(x.text() for x in m.actions())), ga.exec_menu
    try:
        ga.item_menu(win, fh["id"], QtCore.QPoint(0, 0))
    finally:
        ga.exec_menu = old
    assert "Arrowheads" in seen and "Separate layer movie" in seen and "Move tip..." in seen
    assert not errs, errs


def test_separate_layers_option_and_merge(window, tmp_path, monkeypatch):
    from tomoreveal import gui_export
    app, win, errs = window
    j = win.export.layers_cb.findData("separate"); win.export.layers_cb.setCurrentIndex(j); win.export.layers_cb.activated.emit(j)
    pump(app)
    assert win.spec["output"]["layers"] == "separate" and "transparent .mov" in win.export.note.text()
    info = tmp_path / "m_layers.json"; info.write_text(json.dumps(dict(base="m.mp4", layers=[dict(name="arrows", file="m_arrows.mov"),
                                                                                         dict(name="labels", file="m_labels.mov")])))
    monkeypatch.setattr(gui_export, "ask_layers", lambda w, ls: ["arrows"])
    job = win.export.merge_dialog(str(info))
    job["thread"].join(5); job["timer"].timeout.emit(); pump(app)
    assert "missing" in win._msg[0].lower()  # the files are not there: said in plain words
    assert not errs, errs
