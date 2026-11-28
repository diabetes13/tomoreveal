"""Plan 3.5 phase 6: arrows pinned to structures, and several arrows and text boxes per scene."""
import argparse
import json
import sys

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import annotations as an
from tomoreveal import storyboard as sb


def vesicle_centre(ds):
    lo, hi = ds.target_box({"class": ds.key_of(2), "component": 1}, pad=False)
    return (np.asarray(lo) + hi) / 2


def orbit_spec(ds, *anns, seconds=3, motion="spin"):
    spec = tr.default_spec(ds); spec["style"].update(legend=False, slice_label=False)
    part = sb.new_part("overview", motion=motion, seconds=seconds)
    spec["shots"] = [part]; spec["annotations"] = list(anns)
    return spec, part


def main(spec, ds):
    return [f for f in sb.build_plan(spec, ds).frames if f["phase"] == "main"]


def test_tip_stays_on_the_vesicle_and_tail_keeps_its_angle(studio):
    ds = studio.ds; c = vesicle_centre(ds)
    a = an.new_arrow(c, ds.key_of(2), 1, "vesicle"); a["label"]["text"] = ""
    spec, part = orbit_spec(ds, a)
    w, h = 320, 180; frames = main(spec, ds)
    offs = []
    for f in frames[15::9]:  # after the fade-in
        studio.ren.draw(f, spec, (w, h))
        (fx, fy), ok = studio.ren.project(c); assert ok
        tip = studio.ren.last_rects[f"anntip:{a['id']}"]["box"]; tx, ty = (tip[0] + tip[2]) / 2, (tip[1] + tip[3]) / 2
        assert abs(tx - fx * w) <= 1 and abs(ty - fy * h) <= 1
        q = an.project(sb.Framer(ds.shape, w / h, 30.0), f["pose"], c, w / h)  # the plan's projection matches VTK's
        assert abs(q[0] - fx) * w <= 1 and abs(q[1] - fy) * h <= 1
        tail = studio.ren.last_rects[f"anntail:{a['id']}"]["box"]
        offs.append(((tail[0] + tail[2]) / 2 - tx, (tail[1] + tail[3]) / 2 - ty))
    assert np.ptp(np.array(offs), axis=0).max() <= 1.0  # the same tail on screen while the camera moves


def test_hidden_with_its_structure_and_before_the_reveal(studio):
    ds = studio.ds; c = vesicle_centre(ds)
    a = an.new_arrow(c, ds.key_of(2), 1, "v"); spec, part = orbit_spec(ds, a, motion="none")
    f = main(spec, ds)[10]
    studio.ren.draw(f, spec, (320, 180)); assert f"ann:{a['id']}" in studio.ren.last_rects
    spec["classes"][ds.key_of(2)]["show"] = False
    studio.ren.draw(f, spec, (320, 180)); assert f"ann:{a['id']}" not in studio.ren.last_rects
    spec["classes"][ds.key_of(2)]["show"] = True
    spec["shots"] = [sb.new_part("reveal", seconds=2)]  # slides back from the top: the arrow appears once the plane passes it
    fr = main(spec, ds); drawn = []
    for g in fr[::4]:
        studio.ren.draw(g, spec, (320, 180)); drawn.append((g["k"], f"ann:{a['id']}" in studio.ren.last_rects))
    assert all(on == (k + 0.5 <= c[2]) for k, on in drawn)
    assert any(on for _, on in drawn) and not all(on for _, on in drawn)


def test_off_screen_fades_and_behind(studio):
    ds = studio.ds
    a = an.new_arrow([5.0, 5.0, 2.0], None, None, "corner")
    spec, part = orbit_spec(ds, a)
    spec["view"]["zoom"] = 3.0  # zoomed in: the corner leaves the picture while the camera turns
    frames = main(spec, ds); fr = sb.Framer(ds.shape, 16 / 9, 30.0)
    alphas = [f.get("ann", {}).get(a["id"], [0])[0] for f in frames]
    ons = [(lambda p: p[2] and -0.02 <= p[0] <= 1.02 and -0.02 <= p[1] <= 1.02)(an.project(fr, f["pose"], a["anchor"]["xyz"], 16 / 9)) for f in frames]
    assert not all(ons) and any(ons)
    assert all(al == 0 for al, on in zip(alphas, ons) if not on)
    j = next(i for i in range(1, len(ons)) if ons[i - 1] and not ons[i])  # leaving: faded down over the frames before
    assert alphas[j - 1] < alphas[max(0, j - 12)] or alphas[j - 1] < 1
    # the tip below the opaque slice, seen from above: "hide" hides it, "normal" draws it
    b = an.new_arrow([128.0, 160.0, 1.0], None, None, "under"); spec2, _ = orbit_spec(ds, b, motion="none")
    spec2["view"].update(tilt=0)
    f = main(spec2, ds)[5]
    studio.ren.draw(f, spec2, (320, 180)); assert f"ann:{b['id']}" in studio.ren.last_rects
    b["behind"] = "hide"; studio.ren.draw(f, spec2, (320, 180)); assert f"ann:{b['id']}" not in studio.ren.last_rects


def test_three_arrows_two_texts_each_with_its_time(studio, tmp_path):
    ds = studio.ds; c = vesicle_centre(ds)
    items = [an.new_arrow(c, ds.key_of(2), 1, f"a{i}") for i in range(3)] + [an.new_text("one"), an.new_text("two", y=0.6)]
    for i, it in enumerate(items):
        it["show"].update(start=0.5 * i, end=0.5 * i + 1.0, fade=0.2)
    spec, part = orbit_spec(ds, *items, seconds=4, motion="none")
    frames = main(spec, ds)
    for i, it in enumerate(items):
        on = [j / 30 for j, f in enumerate(frames) if it["id"] in f.get("ann", {})]
        assert abs(on[0] - 0.5 * i) <= 1 / 30 + 1e-9 and abs(on[-1] - (0.5 * i + 1.0)) <= 1 / 30 + 1e-9
    back = json.loads(json.dumps(spec))  # saved in the project and back
    assert [x["id"] for x in back["annotations"]] == [x["id"] for x in items]
    spec["output"].update(size=[160, 90], aa=False); part["seconds"] = 1.5
    n, _ = tr.render_movie(studio.ren, spec, str(tmp_path / "a.mp4"), poster=False)
    assert n > 0


def test_labels_kept_apart_and_transparent_export(studio):
    ds = studio.ds
    t1, t2 = an.new_text("first box", x=0.3, y=0.3), an.new_text("second box", x=0.31, y=0.31)
    spec, part = orbit_spec(ds, t1, t2, motion="none")
    f = main(spec, ds)[10]
    studio.ren.draw(f, spec, (320, 180)); r = studio.ren.last_rects
    a, b = r[f"ann:{t1['id']}"]["box"], r[f"ann:{t2['id']}"]["box"]
    assert b[1] >= a[3] and b[3] <= 180  # pushed down, not overlapping, inside the frame
    c = vesicle_centre(ds); ar = an.new_arrow(c, ds.key_of(2), 1, "")
    spec["annotations"] = [ar]; spec["style"]["tomogram"]["show"] = False
    f = main(spec, ds)[10]
    img = studio.ren.draw(f, spec, (320, 180), transparent=True)
    tail = studio.ren.last_rects[f"anntail:{ar['id']}"]["box"]; x, y = int((tail[0] + tail[2]) / 2), int((tail[1] + tail[3]) / 2)
    assert img.shape[2] == 4 and img[y, x, 3] == 255 and img[2, 2, 3] == 0


@pytest.fixture
def window(tmp_path, demo, needs_3d):
    QtCore = pytest.importorskip("PySide6.QtCore")
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True)); win.resize(1400, 900)
    errs = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errs.append(v)
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(30):
        app.processEvents()
    win.req = win._main_req(); win.prev_timer.stop(); win.do_preview()
    for _ in range(10):
        app.processEvents()
    yield app, win, errs
    sys.excepthook = old; win.close()


def test_add_arrows_and_text_in_the_window(window, monkeypatch):
    from PySide6 import QtCore
    from tomoreveal import gui_annotations as ga
    app, win, errs = window
    from tomoreveal import gui_clicks as clk
    ga.start_add_arrow(win); assert isinstance(win.clicks.mode, clk.NewArrow)
    f = next((fx, 0.55) for fx in np.linspace(0.3, 0.7, 17) if win.st.ren.pick_point(fx, 0.55) is not None)
    p = win.st.ren.pick_point(*f)
    win.on_click(*f)
    for _ in range(10):
        app.processEvents()
    a = an.items(win.spec)[-1]
    assert np.linalg.norm(np.asarray(a["anchor"]["xyz"]) - p) <= 1.0 and win.clicks.mode is None
    if win.label_editor is not None:
        win.label_editor.setText("here"); win.label_editor.returnPressed.emit()
    assert a["label"]["text"] == "here"
    texts = [ga.add_text_at(win, (0.2 + 0.1 * i, 0.2 + 0.15 * i)) for i in range(3)]
    for _ in range(5):
        app.processEvents()
    for le in win.prev.findChildren(type(win.name_le)):
        le.hide()
    req = win._main_req(); req["at"]["u"] = 0.5; win.req = req  # the middle of the part (they fade in at its start)
    win.prev_timer.stop(); win.do_preview()
    for _ in range(5):
        app.processEvents()
    n_hist = len(win.editor.history.states)
    ga.align(win, [t["id"] for t in texts], "left")
    win.req = {"frame": win.frame}; win.prev_timer.stop(); win.do_preview()
    rects = win.prev.overlay.rects()
    lefts = [rects[f"ann:{t['id']}"][0][0] for t in texts]
    assert max(lefts) - min(lefts) < 0.005 and len(win.editor.history.states) == n_hist + 1
    # the timeline shows overlapping items in separate lanes; dragging a bar's end changes its time
    from tomoreveal.gui_timeline import LANE_H
    c = win.timeline.canvas; bars = c.ann_bars()
    assert len({b[4] for b in bars}) >= 2
    b = bars[0]; y = c.lanes_top() + b[4] * LANE_H + LANE_H // 2; x = int(round(b[3]))
    n_hist = len(win.editor.history.states)
    from PySide6 import QtTest
    QtTest.QTest.mousePress(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x, y))
    QtTest.QTest.mouseMove(c, QtCore.QPoint(x - 30, y))
    QtTest.QTest.mouseRelease(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x - 30, y))
    for _ in range(5):
        app.processEvents()
    got = an.find(win.spec, b[0])["show"]
    assert got["end"] == pytest.approx(round(round((b[6] - 30 / (c.ppf * 30)) / 0.1) * 0.1, 2))
    assert len(win.editor.history.states) == n_hist + 1
    # the menus build
    seen = []
    monkeypatch.setattr(ga, "exec_menu", lambda m, pos: seen.extend(x.text() for x in m.actions()))
    ga.item_menu(win, a["id"], QtCore.QPoint(0, 0))
    assert "Delete" in seen and "Duplicate" in seen
    assert not errs, errs


def test_clicks_beside_an_arrow_do_not_fail(window):
    """3.5.0 bug: with an arrow on the picture, every other click in the preview raised KeyError 'ann:...'."""
    from PySide6 import QtCore, QtTest
    app, win, errs = window
    ds = win.st.ds; a = an.new_arrow(vesicle_centre(ds), ds.key_of(2), 1, "vesicle"); t = an.new_text("note")
    win.spec["annotations"] = [a, t]; win.editor.commit()
    req = win._main_req(); req["at"]["u"] = 0.5; win.req = req; win.prev_timer.stop(); win.do_preview()
    for _ in range(10):
        app.processEvents()
    assert f"ann:{a['id']}" in win.st.ren.last_rects
    for fx, fy in ((0.05, 0.95), (0.5, 0.5), (0.9, 0.1), (0.3, 0.7)):
        pt = win.prev.to_widget(fx, fy).toPoint()
        QtTest.QTest.mousePress(win.prev, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, pt)
        QtTest.QTest.mouseRelease(win.prev, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, pt)
        win.prev.overlay.hover((fx, fy))
    for _ in range(5):
        app.processEvents()
    assert not errs, errs


def paused_slide(ds):
    """A slide with pauses before and after it, then a close-up (and the flight back)."""
    spec = tr.default_spec(ds)
    s = sb.new_part("slide", seconds=2, pause_before=1.5, pause_after=1.5)
    spec["shots"] = [s, sb.new_part("focus", target=sb.make_target([(ds.key_of(2), 1)]), seconds=1)]
    return spec, s


def test_an_arrow_without_an_end_time_shows_to_the_end_of_its_part(studio):
    """Its end was the length of the main phase, counted from the start of the pause before it: it vanished 1.5 s early
    and was never in the pause after."""
    ds = studio.ds; spec, s = paused_slide(ds)
    a = an.new_arrow([128.0, 160.0, 20.0], parts=[s["id"]]); a["show"]["appear"] = "pop"
    b = an.new_arrow([100.0, 100.0, 20.0]); b["show"].update(start=1.0, end=2.0)
    c = an.new_arrow([60.0, 60.0, 20.0]); c["show"]["end"] = 100.0
    spec["annotations"] = [a, b, c]
    frames = sb.build_plan(spec, ds).frames
    mine = [f for f in frames if f["part"] == s["id"] and f["phase"] in ("main", "pause")]
    assert len(mine) == 150 and all(a["id"] in f.get("ann", {}) for f in mine)
    assert all(1.0 <= f["tp"] <= 2.0 for f in frames if b["id"] in f.get("ann", {}))  # explicit times: as before
    back = max(i for i, f in enumerate(frames) if f["phase"] == "transition")
    assert frames[back + 1:] and not any(f.get("ann") for f in frames[back + 1:])  # nothing after the flight back


def arrow_in_a_closeup(ds, target, others, look=None, **classes):
    """An arrow on vesicle 1 seen in the middle of a close-up of `target`."""
    from tomoreveal import look as lk
    spec = tr.default_spec(ds)
    if look:
        lk.apply_look(spec, look)
    for key, kw in classes.items():
        spec["classes"][ds.key_of(int(key[1:]))].update(kw)
    a = an.new_arrow(vesicle_centre(ds), ds.key_of(2), 1, "v")
    part = sb.new_part("focus", target=sb.make_target(target), others=others, motion="none", seconds=1)
    spec["shots"] = [part]; spec["annotations"] = [a]
    f = [g for g in sb.build_plan(spec, ds).frames if g["part"] == part["id"] and g["phase"] == "main"][-1]
    assert f["e"] == 1.0
    return a, spec, f


@pytest.mark.parametrize("case", ["hidden in this close-up", "opacity 0", "all at opacity 0", "another object, others hidden"])
def test_an_arrow_goes_with_what_the_close_up_hides(studio, case):
    """It stayed, pointing at nothing: the close-up's per-structure settings, opacity 0 and the other objects of its own
    structure were not asked."""
    ds = studio.ds; ves, mem = ds.key_of(2), ds.key_of(1)
    if case == "hidden in this close-up":
        a, spec, f = arrow_in_a_closeup(ds, [(mem, None)], dict(colour="grey", opacity=1.0, each={ves: "hide"}))
    elif case == "opacity 0":
        a, spec, f = arrow_in_a_closeup(ds, [(mem, None)], dict(colour="grey", opacity=1.0), c2=dict(opacity=0.0))
    elif case == "all at opacity 0":
        a, spec, f = arrow_in_a_closeup(ds, [(mem, None)], dict(colour="grey", opacity=1.0)); spec["style"]["structures_opacity"] = 0.0
    else:
        a, spec, f = arrow_in_a_closeup(ds, [(ves, 2)], dict(colour="grey", opacity=0.0))
    assert an._visible_here(a, f, spec, ds, an.anchor_xyz(a)) == 0.0
    studio.ren.draw(f, spec, (320, 180)); assert f"ann:{a['id']}" not in studio.ren.last_rects


@pytest.mark.parametrize("case", ["glass in this close-up", "glass look", "x-ray look", "its own object in focus"])
def test_an_arrow_stays_where_the_close_up_only_changes_the_look(studio, case):
    """Glass and X-ray draw structures at 30 % or less: that is the look, not hiding (the arrow would be dimmed)."""
    ds = studio.ds; ves, mem = ds.key_of(2), ds.key_of(1)
    if case == "glass in this close-up":
        a, spec, f = arrow_in_a_closeup(ds, [(mem, None)], dict(colour="grey", opacity=1.0, each={ves: "glass"}))
    elif case == "its own object in focus":
        a, spec, f = arrow_in_a_closeup(ds, [(ves, 1)], dict(colour="grey", opacity=0.0))
    else:
        a, spec, f = arrow_in_a_closeup(ds, [(mem, None)], dict(colour="grey", opacity=1.0), look="glass" if "glass" in case else "xray",
                                        c2=dict(membrane=True) if "glass" in case else {})
    assert an._visible_here(a, f, spec, ds, an.anchor_xyz(a)) == 1.0


def test_frames_to_the_picture_edge_are_counted_quickly():
    """The off-screen fade measured each frame against every frame off the picture: a frames x frames table (1.7 GB and
    11 s for every preview of a long movie)."""
    import time
    rng = np.random.default_rng(5)
    for n in (1, 7, 2000):
        for p in (0.02, 0.5, 0.98):
            on = rng.random(n) > p
            off = np.nonzero(~on)[0]
            if not len(off):
                continue
            want = np.min(np.abs(np.arange(n)[:, None] - off[None, :]), axis=1)  # (as it was counted)
            assert np.array_equal(an.frames_from_off(on), want)
    on = np.ones(20000, bool); on[[5, 9000, 19999]] = False
    t = time.perf_counter(); d = an.frames_from_off(on); t = time.perf_counter() - t
    assert d[0] == 5 and d[9001] == 1 and d[19998] == 1 and t < 0.1, t


def test_a_new_arrow_or_text_goes_into_the_part_on_screen(window):
    """Not into the selected part: the playhead is in part 2, part 1 is selected. On a card nothing is added."""
    from tomoreveal import gui_annotations as ga
    from tomoreveal import storyboard as sb
    app, win, errs = window
    s1, s2, card = sb.new_part("slide", seconds=3), sb.new_part("slide", seconds=3), sb.new_part("card", seconds=3)
    win.spec["shots"] = [s1, s2, card]; win.spec["annotations"] = []; win.editor.commit(select=s1["id"])
    win.prev_timer.stop(); win.do_preview(); app.processEvents()
    win.ann_panel.only_part.setChecked(True)
    def go(pid, u=0.5):
        win.req = {"at": {"part": pid, "u": u}}; win.prev_timer.stop(); win.do_preview(); app.processEvents()
    go(s2["id"]); win.selected_part = s1["id"]
    f = next((fx, 0.55) for fx in np.linspace(0.3, 0.7, 17) if win.st.ren.pick_point(fx, 0.55) is not None)
    a = ga.add_arrow_at(win, f); t = ga.add_text_at(win, None)
    assert a["show"]["parts"] == [s2["id"]] and t["show"]["parts"] == [s2["id"]] and win.selected_part == s2["id"]
    if win.label_editor is not None:
        win.label_editor.setText("x"); win.label_editor.returnPressed.emit()
    go(s2["id"])
    assert {a["id"], t["id"]} <= set(win.timeline.canvas.plan.frames[win.frame].get("ann", {}))  # seen in this frame
    assert win.ann_panel.rows.count() == 2  # and in the list of the selected part
    go(card["id"]); n = len(win.spec["annotations"])
    assert ga.add_arrow_at(win, f) is None and ga.add_text_at(win, None) is None
    assert len(win.spec["annotations"]) == n and "card" in win.msg_lbl.text().lower()
    assert not errs, errs


def test_typing_in_the_list_of_arrows_is_not_interrupted(window):
    """G-45: every change rebuilt all rows inside the signal of one of them: digits, focus and the next click were lost."""
    from PySide6 import QtCore, QtTest, QtWidgets
    from tomoreveal import storyboard as sb
    app, win, errs = window
    s = sb.new_part("slide", seconds=20); win.spec["shots"] = [s]
    a = an.new_arrow([128.0, 160.0, 20.0], parts=[s["id"]]); win.spec["annotations"] = [a]; win.editor.commit(select=s["id"])
    win.tabs.setCurrentIndex(win.TAB_LOOK); win.show(); win.activateWindow(); win.windowHandle().requestActivate()
    for _ in range(200):
        app.processEvents()
        if win.isActiveWindow():
            break
    else:
        pytest.skip("the window could not be made active here")
    win.prev_timer.stop(); win.do_preview(); app.processEvents()
    row = win.ann_panel.rows.itemAt(0).widget()
    to = [sp for sp in row.findChildren(QtWidgets.QDoubleSpinBox) if sp.prefix() == "to "][0]
    label = row.findChildren(QtWidgets.QLineEdit)[0]; vis = row.findChildren(QtWidgets.QCheckBox)[0]
    win.ann_panel.group.body.parentWidget().setVisible(True)
    to.setFocus(); to.selectAll(); QtTest.QTest.keyClicks(to, "12.5"); QtTest.QTest.keyClick(to, QtCore.Qt.Key_Return)
    for _ in range(5):
        app.processEvents()
    assert an.find(win.spec, a["id"])["show"]["end"] == pytest.approx(12.5)
    assert win.ann_panel.rows.itemAt(0).widget() is row and to.hasFocus() and to.isVisibleTo(win)
    # type a label, then click "shown" with one click on the window: both happen
    label.setFocus(); label.selectAll(); QtTest.QTest.keyClicks(label, "here")
    win.ensurePolished(); vis.window().raise_()
    at = vis.mapTo(win, vis.rect().center())
    QtTest.QTest.mouseClick(win.windowHandle(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, at)
    for _ in range(10):
        app.processEvents()
    b = an.find(win.spec, a["id"])
    assert b["label"]["text"] == "here" and b.get("visible", True) is False
    assert win.ann_panel.rows.itemAt(0).widget() is row  # the same widgets, filled again
    assert not errs, errs


def test_esc_in_the_label_editor_cancels(window):
    """G-55: Esc did not close the label editor; the next click elsewhere kept the text, and keys reached the preview."""
    from PySide6 import QtCore, QtTest
    from tomoreveal import gui_annotations as ga
    from tomoreveal import gui_clicks as clk
    app, win, errs = window
    a = an.new_arrow([128.0, 160.0, 20.0]); a["label"]["text"] = "old"; win.spec["annotations"] = [a]; win.editor.commit()
    win.prev_timer.stop(); win.do_preview(); app.processEvents()
    win.show(); win.activateWindow(); win.windowHandle().requestActivate()
    for _ in range(200):
        app.processEvents()
        if win.isActiveWindow():
            break
    else:
        pytest.skip("the window could not be made active here")
    win.clicks.start(clk.NewArrow()); n = len(win.editor.history.states); view = dict(win.spec["view"])
    ga.edit_label(win, a["id"]); le = win.label_editor; app.processEvents()
    QtTest.QTest.keyClicks(le, "new"); QtTest.QTest.keyClick(le, QtCore.Qt.Key_Up)
    QtTest.QTest.keyClick(le, QtCore.Qt.Key_Escape)
    for _ in range(5):
        app.processEvents()
    from shiboken6 import isValid
    assert win.label_editor is None and not (isValid(le) and le.isVisible())
    win.name_le.setFocus(); app.processEvents()
    assert an.find(win.spec, a["id"])["label"]["text"] == "old" and len(win.editor.history.states) == n
    assert isinstance(win.clicks.mode, clk.NewArrow)  # Esc was the editor's
    assert win.spec["view"] == view  # Up did not move the view
    # Enter and clicking elsewhere keep what was typed
    ga.edit_label(win, a["id"]); le = win.label_editor; QtTest.QTest.keyClicks(le, "kept"); QtTest.QTest.keyClick(le, QtCore.Qt.Key_Return)
    assert an.find(win.spec, a["id"])["label"]["text"] == "kept"
    ga.edit_label(win, a["id"]); le = win.label_editor; QtTest.QTest.keyClicks(le, "again"); win.name_le.setFocus(); app.processEvents()
    assert an.find(win.spec, a["id"])["label"]["text"] == "again"
    assert not errs, errs
