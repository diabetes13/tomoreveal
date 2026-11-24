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
    ga.start_add_arrow(win); assert win.arrow_pick
    f = next((fx, 0.55) for fx in np.linspace(0.3, 0.7, 17) if win.st.ren.pick_point(fx, 0.55) is not None)
    p = win.st.ren.pick_point(*f)
    win.on_click(*f)
    for _ in range(10):
        app.processEvents()
    a = an.items(win.spec)[-1]
    assert np.linalg.norm(np.asarray(a["anchor"]["xyz"]) - p) <= 1.0 and not win.arrow_pick
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
