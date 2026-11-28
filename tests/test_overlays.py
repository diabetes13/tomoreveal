"""Plan 3.4 phase 2: overlay items (legend, slice number, scale bar, title, caption): showing, placing, sizing."""
import argparse
import os
import sys

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import overlays as ov


def overview(st, spec, i=0):
    frames, _ = tr.build_timeline(spec, st.ds)
    return [f for f in frames if f.get("type") == "overview" and f["phase"] == "main"][i]


def test_explicit_defaults_draw_the_same_picture(studio):
    spec = tr.default_spec(studio.ds); f = overview(studio, spec)
    a = studio.ren.draw(f, spec, (320, 180))
    spec["overlays"] = ov.overlay_settings(spec)
    assert np.array_equal(studio.ren.draw(f, spec, (320, 180)), a)


def test_legend_keeps_its_relative_place_at_any_size(studio):
    spec = tr.default_spec(studio.ds); f = overview(studio, spec)
    ov.set_item(spec, "legend", x=0.03, y=0.97, anchor="bottom-left")
    boxes = []
    for W, H in ((320, 180), (1280, 720)):
        studio.ren.draw(f, spec, (W, H))
        x0, y0, x1, y1 = studio.ren.last_rects["legend"]["box"]
        assert x1 <= W / 2 and y0 >= H / 2  # in the bottom-left quarter
        boxes.append((x0 / W, y1 / H))
    assert abs(boxes[0][0] - boxes[1][0]) < 0.01 and abs(boxes[0][1] - boxes[1][1]) < 0.01
    assert abs(boxes[1][0] - 0.03) < 0.01 and abs(boxes[1][1] - 0.97) < 0.01


def test_slice_number_hidden_for_one_part(studio):
    spec = tr.default_spec(studio.ds)
    hide = spec["shots"][0]["id"]; spec["shots"][0]["overlays"] = {"slice_label": "hide"}
    frames, _ = tr.build_timeline(spec, studio.ds)
    a = next(f for f in frames if f["part"] == hide and f["phase"] == "main")
    b = next(f for f in frames if f["part"] == spec["shots"][1]["id"] and f["phase"] == "main")
    studio.ren.draw(a, spec, (320, 180)); assert not studio.ren.last_rects["slice_label"]["shown"]
    studio.ren.draw(b, spec, (320, 180)); assert studio.ren.last_rects["slice_label"]["shown"]
    spec["style"]["legend"] = False; spec["shots"][2]["overlays"] = {"legend": "show"}  # off for the movie, on for one part
    studio.ren.draw(overview(studio, spec), spec, (320, 180)); assert studio.ren.last_rects["legend"]["shown"]


def test_scale_bar_length_in_orthographic_view(studio):
    spec = tr.default_spec(studio.ds); spec["style"]["ortho"] = True; spec["style"]["bg"] = "#000000"
    spec["style"]["tomogram"]["show"] = False; spec["style"]["legend"] = False; spec["style"]["slice_label"] = False
    ov.set_show(spec, "scale_bar", True); ov.set_item(spec, "scale_bar", length_nm=50, label=True)
    f = overview(studio, spec); W, H = 640, 360
    img = studio.ren.draw(f, spec, (W, H))
    expect = 50 * 10 / studio.ds.vox * H / (2 * f["pose"]["scale"])  # demo voxel: 10 Å
    x0, y0, x1, y1 = studio.ren.last_rects["scale_bar"]["box"]
    row = img[int(y1) - 1, int(x0):int(x1) + 1].astype(int).sum(1) > 600  # the bar is white
    assert abs(row.sum() - expect) <= 1.5, (row.sum(), expect)
    assert ov.length_text(50) == "50 nm" and ov.length_text(2000) == "2 µm"


@pytest.mark.parametrize("fov", [30.0, 60.0])
def test_scale_bar_length_where_a_camera_path_changes_the_field_of_view(studio, fov):
    """The bar used the Look's field of view, not the frame's: 2.15 times too long at 60 degrees."""
    from test_waypoints import fov_spec
    spec, part = fov_spec(studio); part["waypoints"][1]["fov"] = fov; part["waypoints"][0]["fov"] = fov
    spec["style"].update(legend=False, slice_label=False, bg="#000000"); spec["style"]["tomogram"]["show"] = False
    ov.set_show(spec, "scale_bar", True); ov.set_item(spec, "scale_bar", length_nm=50, label=True)
    f = [g for g in tr.storyboard.build_plan(spec, studio.ds).frames if g["part"] == part["id"] and g["phase"] == "main"][-1]
    assert abs(float(f["pose"].get("fov", 30.0)) - fov) < 1e-6
    W, H = 640, 360; img = studio.ren.draw(f, spec, (W, H))
    _, foc, right, _, _ = studio.ren.camera_frame()
    (ax, _), _ = studio.ren.project(foc); (bx, _), _ = studio.ren.project(foc + right * 50 * 10 / studio.ds.vox)
    expect = abs(bx - ax) * W  # a 50 nm line at the focal point, as VTK draws it
    x0, y0, x1, y1 = studio.ren.last_rects["scale_bar"]["box"]
    row = img[int(y1) - 1, int(x0):int(x1) + 1].astype(int).sum(1) > 600  # the bar is white
    assert abs(row.sum() - expect) <= 0.02 * expect + 1, (row.sum(), expect)


def test_auto_length_is_nice_and_fits(studio):
    spec = tr.default_spec(studio.ds); W, H = 1920, 1080
    frames, _ = tr.build_timeline(spec, studio.ds)
    for f in frames[::40]:
        L = f["bar_nm"]
        mant = L / 10 ** np.floor(np.log10(L))
        assert round(mant, 6) in (1, 2, 5)
    part_first = {}
    for f in frames:
        if f["phase"] == "main":
            part_first.setdefault(f["part"], f)
    for f in part_first.values():
        px = ov.px_per_voxel(f["pose"], H, 30.0, False) * f["bar_nm"] * 10 / studio.ds.vox
        assert 0.10 * W - 1 <= px <= 0.25 * W + 1
    # the number stays the same for a whole part while the camera moves
    pid = spec["shots"][2]["id"]
    assert len({f["bar_nm"] for f in frames if f["part"] == pid}) == 1


def test_scale_bar_on_the_slice_renders(studio):
    spec = tr.default_spec(studio.ds)
    ov.set_show(spec, "scale_bar", True); ov.set_item(spec, "scale_bar", mode="slice")
    studio.ren.draw(overview(studio, spec), spec, (320, 180))
    assert studio.ren.last_rects["scale_bar"]["shown"] and studio.ren.bar_actor.GetVisibility()
    ov.set_show(spec, "scale_bar", False)
    studio.ren.draw(overview(studio, spec), spec, (320, 180))
    assert not studio.ren.bar_actor.GetVisibility()


# ---------------------------------------------------------------------------------------------------------------
# the window
# ---------------------------------------------------------------------------------------------------------------
QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6 import QtTest  # noqa: E402


def wait(app, win, start=None):
    if start:
        start()
    if win.worker is not None and win.worker.isRunning():
        loop = QtCore.QEventLoop(); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(40):
        app.processEvents()


@pytest.fixture
def window(tmp_path):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    win.resize(1400, 900)
    errs = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errs.append(v)
    yield app, win, errs
    sys.excepthook = old; win.close()


def test_wrong_voxel_size_warns_and_can_be_typed(window, demo, tmp_path):
    app, win, errs = window
    from tomoreveal.volumes import load_volume, write_mrc
    a, _ = load_volume(demo["tomogram"]); p = str(tmp_path / "one_angstrom.mrc"); write_mrc(p, a, 1.0)
    wait(app, win, lambda: win.load(paths=[p]))
    if win.st.ren is None:
        pytest.skip("3D rendering is not available here")
    assert "probably wrong" in win.voxel_row.note.text() and win.voxel_row.isVisibleTo(win)
    win.voxel_row.spin.setValue(4.11)
    assert win.spec["data"]["voxel_size_nm"] == pytest.approx(4.11) and ov.voxel_size(win.spec, win.st.ds) == pytest.approx(41.1)
    assert "your value" in win.voxel_row.note.text()
    assert win.project.data()["spec"]["data"]["voxel_size_nm"] == pytest.approx(4.11)  # saved in the project
    assert not errs, errs


def activate(app, win):
    """Typing tests need the window active, also off-screen."""
    win.show(); win.activateWindow(); win.windowHandle().requestActivate()
    for _ in range(200):
        app.processEvents()
        if win.isActiveWindow():
            return
    pytest.skip("the window could not be made active here")


def test_voxel_size_can_be_typed_key_by_key(window, demo, tmp_path, monkeypatch):
    app, win, errs = window
    wait(app, win, lambda: win.load(paths=[demo["tomogram"], demo["labels"]]))
    if win.st.ren is None:
        pytest.skip("3D rendering is not available here")
    activate(app, win); win.tabs.setCurrentIndex(0)
    sp = win.voxel_row.spin; sp.setFocus(); app.processEvents()
    for text, want in (("0.8", 0.8), ("1.36", 1.36)):
        sp.selectAll(); QtTest.QTest.keyClicks(sp, text); QtTest.QTest.keyClick(sp, QtCore.Qt.Key_Return)
        assert win.spec["data"]["voxel_size_nm"] == pytest.approx(want)
    from tomoreveal import project as pj
    win.project.path = str(tmp_path / ("typed" + pj.EXT))
    sp.selectAll(); QtTest.QTest.keyClicks(sp, "2.5")  # no Enter: Ctrl+S takes the number as it is typed
    assert app.focusWidget() in (sp, sp.lineEdit())
    win.project.act_save.trigger()
    assert pj.read_project(win.project.path)["spec"]["data"]["voxel_size_nm"] == pytest.approx(2.5)
    asked = []
    monkeypatch.setattr(type(win.project), "ask_save", lambda self, what: asked.append(what) or "cancel")
    sp.selectAll(); QtTest.QTest.keyClicks(sp, "3")
    assert not win.project.confirm_discard("closing TomoReveal") and asked  # the typed 3 is a change
    assert win.spec["data"]["voxel_size_nm"] == pytest.approx(3.0)
    assert not errs, errs


def test_drag_the_legend_to_the_bottom_left(window, demo):
    app, win, errs = window
    wait(app, win, lambda: win.load(paths=[demo["tomogram"], demo["labels"]]))
    if win.st.ren is None:
        pytest.skip("3D rendering is not available here")
    win.prev_timer.stop(); win.do_preview()
    for _ in range(10):
        app.processEvents()
    box = win.prev.overlay.rects()["legend"][0]
    assert box[0] > 0.5 and box[1] < 0.5  # top right at first
    view = dict(win.spec["view"]); n_hist = len(win.editor.history.states)
    start = win.prev.to_widget((box[0] + box[2]) / 2, (box[1] + box[3]) / 2).toPoint()
    end = win.prev.to_widget(0.15, 0.85).toPoint()
    QtTest.QTest.mousePress(win.prev, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
    for t in np.linspace(0, 1, 6)[1:]:
        QtTest.QTest.mouseMove(win.prev, QtCore.QPoint(round(start.x() + (end.x() - start.x()) * t), round(start.y() + (end.y() - start.y()) * t)))
    QtTest.QTest.mouseRelease(win.prev, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, end)
    for _ in range(20):
        app.processEvents()
    lg = win.spec["overlays"]["legend"]
    assert lg["anchor"].endswith("left") and lg["anchor"].startswith(("bottom", "middle")) and lg["x"] < 0.3 and lg["y"] > 0.6
    assert win.spec["view"] == view  # the camera did not turn
    assert len(win.editor.history.states) == n_hist + 1
    win.prev_timer.stop(); win.do_preview()
    box = win.prev.overlay.rects()["legend"][0]
    assert box[2] < 0.5 and box[3] > 0.5
    win.editor.undo()
    assert win.spec.get("overlays", {}).get("legend", {}).get("x") is None
    assert not errs, errs


def test_toolbar_and_look_tab_follow_each_other(window, demo):
    app, win, errs = window
    wait(app, win, lambda: win.load(paths=[demo["tomogram"], demo["labels"]]))
    if win.st.ren is None:
        pytest.skip("3D rendering is not available here")
    win.toolbar.btn["scale_bar"].setChecked(True)
    assert win.spec["overlays"]["scale_bar"]["show"] and win.overlay_controls.cb["scale_bar"].isChecked()
    win.overlay_controls.cb["legend"].setChecked(False)
    assert win.spec["style"]["legend"] is False and not win.toolbar.btn["legend"].isChecked()
    assert not errs, errs
