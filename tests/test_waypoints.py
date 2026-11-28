"""Plan 3.5 phase 5: a clear, easy path through points (interpolation, zoom and field of view per point with "keep",
2D drawing, editing)."""
import argparse
import math
import sys

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import storyboard as sb
from tomoreveal import paths

FOC = [128.0, 160.0, 24.0]


def poses_of(ds, wps, seconds=6, **kw):
    spec = tr.default_spec(ds)
    part = sb.new_part("path", seconds=seconds, source=dict(kind="points", items=[]), waypoints=wps, **kw)
    spec["shots"] = [part]
    return [f["pose"] for f in sb.build_plan(spec, ds).frames if f["part"] == part["id"] and f["phase"] == "main"]


def test_even_speed_and_no_overshoot(studio):
    wps = [dict(pos=[20.0, 20.0, 150.0], focal=FOC), dict(pos=[40.0, 30.0, 150.0], focal=FOC), dict(pos=[200.0, 60.0, 150.0], focal=FOC),
           dict(pos=[220.0, 300.0, 150.0], focal=FOC)]
    P = np.array([p["pos"] for p in poses_of(studio.ds, wps)])
    lo, hi = np.min([w["pos"] for w in wps], 0), np.max([w["pos"] for w in wps], 0); pad = 0.05 * (hi - lo) + 1e-6
    assert np.all(P >= lo - pad) and np.all(P <= hi + pad)
    v = np.linalg.norm(np.diff(P, axis=0), axis=1)[25:-25]  # away from the gentle start and end
    assert v.max() / v.min() - 1 < 0.05


def test_no_flip_at_a_sharp_corner(studio):
    wps = [dict(pos=[40.0, 40.0, 150.0], focal=[60.0, 60.0, 24.0]), dict(pos=[200.0, 40.0, 150.0], focal=[200.0, 60.0, 24.0]),
           dict(pos=[200.0, 280.0, 150.0], focal=[180.0, 280.0, 24.0])]
    poses = poses_of(studio.ds, wps)
    d = np.array([np.asarray(p["c"]) - p["pos"] for p in poses]); d /= np.linalg.norm(d, axis=1, keepdims=True)
    turn = np.degrees(np.arccos(np.clip(np.sum(d[1:] * d[:-1], axis=1), -1, 1)))
    assert turn.max() < 10
    ups = np.array([p["up"] for p in poses])
    assert np.degrees(np.arccos(np.clip(np.sum(ups[1:] * ups[:-1], axis=1), -1, 1))).max() < 10


def dists(poses):
    return np.array([np.linalg.norm(np.asarray(p["pos"]) - p["c"]) for p in poses])


def test_zoom_halves_the_distance_smoothly(studio):
    P0 = [60.0, 60.0, 150.0]  # the camera stands still: only the zoom changes the distance
    wps = [dict(pos=P0, focal=FOC, zoom=1.0), dict(pos=P0, focal=FOC, zoom=2.0)]
    d = dists(poses_of(studio.ds, wps)); base = np.linalg.norm(np.array(P0) - FOC)
    assert d[0] == pytest.approx(base, rel=1e-3) and d[-1] == pytest.approx(base / 2, rel=1e-3)
    assert np.all(np.diff(d) <= 1e-9)  # smoothly and monotonically closer
    v = -np.diff(d); assert v[0] < 0.2 * v.max() and v[-1] < 0.2 * v.max()


def test_keep_zoom_along_the_next_points(studio):
    P0 = [60.0, 60.0, 150.0]; base = np.linalg.norm(np.array(P0) - FOC)
    wps = [dict(pos=P0, focal=FOC), dict(pos=P0, focal=FOC, zoom=2.0), dict(pos=P0, focal=FOC), dict(pos=P0, focal=FOC, zoom=1.0)]
    part = sb.new_part("path", seconds=6, source=dict(kind="points", items=[]), waypoints=wps)
    zs, own = paths.point_values(part, "zoom", 1.0)
    assert zs == [1.0, 2.0, 2.0, 1.0] and own == [False, True, False, True]
    z = base / dists(poses_of(studio.ds, wps))
    at2 = np.nonzero(np.abs(z / 2.0 - 1) < 1e-3)[0]  # from point 2 to point 3 the zoom stays at 2x
    i2, i3 = at2[0], at2[-1]
    assert len(at2) == i3 - i2 + 1 and len(at2) > 0.25 * len(z)
    assert np.all(np.diff(z[i3:]) <= 1e-9) and z[-1] == pytest.approx(1.0, rel=1e-3)  # then smoothly back to 1x
    wps[1]["zoom_only_here"] = True  # "only here": the next point goes back to the kept zoom
    assert paths.point_values(dict(part, waypoints=wps), "zoom", 1.0)[0] == [1.0, 2.0, 1.0, 1.0]
    same = dict(part, waypoints=wps, same_zoom=True, path_zoom=1.5)
    assert paths.point_values(same, "zoom", 1.0)[0] == [1.5] * 4
    zs2 = base / dists(poses_of(studio.ds, wps, same_zoom=True, path_zoom=1.5))
    assert np.abs(zs2 / 1.5 - 1).max() < 1e-3  # one zoom along the whole path


def test_field_of_view_and_keeping_the_size(studio):
    wps = [dict(pos=[60.0, 60.0, 150.0], focal=FOC), dict(pos=[200.0, 260.0, 150.0], focal=FOC)]
    a = poses_of(studio.ds, wps)
    z = math.tan(math.radians(15) / 2) / math.tan(math.radians(30) / 2)  # what "Keep the size the same" stores
    wps2 = [dict(w, fov=15.0, zoom=z) for w in wps]
    b = poses_of(studio.ds, wps2)
    assert b[0]["fov"] == pytest.approx(15.0)
    size_a = 1 / (dists(a)[0] * math.tan(math.radians(30) / 2)); size_b = 1 / (dists(b)[0] * math.tan(math.radians(15) / 2))
    assert size_b / size_a == pytest.approx(1.0, abs=0.02)


def test_path_line_is_2_px_wide():
    QtCore = pytest.importorskip("PySide6.QtCore")
    from PySide6 import QtGui, QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from tomoreveal.gui_paths import paint_path

    class Prev:
        def __init__(self, w, h):
            self.w, self.h = w, h

        def image_px(self):
            return (self.w, self.h)

        def to_widget(self, fx, fy):
            return QtCore.QPointF(fx * self.w, fy * self.h)
    for w, h in ((400, 200), (1200, 700)):
        img = QtGui.QImage(w, h, QtGui.QImage.Format_RGB32); img.fill(QtGui.QColor(0, 0, 0))
        view = dict(lines=[dict(kind="path", pts=[(0.1, 0.5, True, True), (0.9, 0.5, True, True)])], marks=[])
        p = QtGui.QPainter(img); paint_path(p, Prev(w, h), view); p.end()
        col = [QtGui.QColor(img.pixel(w // 3, y)).red() for y in range(h)]
        assert sum(1 for c in col if c > 150) in (2, 3), (w, h)


@pytest.fixture
def window(tmp_path, demo, needs_3d):
    QtCore = pytest.importorskip("PySide6.QtCore")
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True)); win.resize(1400, 900)
    errs = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errs.append(v)
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(30):
        app.processEvents()
    yield app, win, errs
    sys.excepthook = old; win.close()


def test_edit_points_in_the_window(window):
    app, win, errs = window
    part = sb.new_part("path", seconds=4, source=dict(kind="points", items=[]),
                       waypoints=[dict(pos=[60.0, 60.0, 120.0], focal=[80.0, 90.0, 24.0]), dict(pos=[200.0, 260.0, 120.0], focal=[180.0, 230.0, 24.0])])
    win.editor.insert(part, "start"); pid = part["id"]
    win.req = {"at": {"part": pid, "u": 0.0}}; win.prev_timer.stop(); win.do_preview()
    for _ in range(10):
        app.processEvents()
    win.req = win._main_req(); win.prev_timer.stop(); win.do_preview()  # look at it from the overview
    for _ in range(10):
        app.processEvents()
    assert win.editing_path() is not None and "Editing the camera path" in win.prev.note.text()
    ren = win.st.ren; rects = ren.last_rects
    assert "marker:0" in rects and win.st.ren.path_view["marks"]
    # drag point 2's look-at crosshair by 50 px to the right
    W, H = ren.size; box = rects["look:1"]["box"]; f0 = ((box[0] + box[2]) / 2 / W, (box[1] + box[3]) / 2 / H)
    img_w = win.prev.image_px()[0]; f1 = (f0[0] + 50 / img_w, f0[1])
    before = np.asarray(win.parts_panel.part(pid)["waypoints"][1]["focal"], float)
    expect = ren.to_plane(f1[0], f1[1], before[2]) - ren.to_plane(f0[0], f0[1], before[2])
    n_hist = len(win.editor.history.states)
    ov = win.prev.overlay
    assert ov.press(f0, 0); ov.move(f1, QtCore_mods()); ov.release()
    for _ in range(10):
        app.processEvents()
    after = np.asarray(win.parts_panel.part(pid)["waypoints"][1]["focal"], float)
    assert np.allclose(after - before, expect, atol=0.5) and len(win.editor.history.states) == n_hist + 1
    assert win.path_point == (pid, 1) and win.point_panel.isVisible()
    # the panel: zoom 2x at point 2, kept for the next points
    win.point_panel.zoom.setValue(win.point_panel.z_to_slider(2.0)); win.point_panel._commit()
    assert win.parts_panel.part(pid)["waypoints"][1]["zoom"] == pytest.approx(2.0, rel=0.01)
    # a click on the slice adds a point after the selected one
    win.req = win._main_req(); win.prev_timer.stop(); win.do_preview()
    for _ in range(5):
        app.processEvents()
    for fx in np.linspace(0.3, 0.7, 9):
        if ren.pick_point(fx, 0.6) is not None:
            win.on_click(fx, 0.6); break
    assert len(win.parts_panel.part(pid)["waypoints"]) == 3
    win.parts_panel.open(pid)  # the list of points builds
    assert not errs, errs


def test_the_point_panel_stays_on_its_point(window):
    """G-46: the selected point was a number nobody changed: delete point 1 and the panel edited another point."""
    app, win, errs = window
    wps = [dict(pos=[60.0 + 30 * k, 60.0 + 40 * k, 120.0], focal=[80.0 + 20 * k, 90.0 + 25 * k, 24.0]) for k in range(5)]
    part = sb.new_part("path", seconds=4, source=dict(kind="points", items=[]), waypoints=wps)
    win.editor.insert(part, "start"); pid = part["id"]
    win.req = {"at": {"part": pid, "u": 0.0}}; win.prev_timer.stop(); win.do_preview()
    for _ in range(10):
        app.processEvents()
    def redraw():
        win.prev_timer.stop(); win.do_preview()
        for _ in range(5):
            app.processEvents()
    def ws():
        return win.parts_panel.part(pid)["waypoints"]
    win.select_path_point(pid, 2); me = ws()[2]
    win.parts_panel.open(pid)
    def delete_point(i):  # as the preview's right-click menu does
        del ws()[i]; win.editor.commit(select=pid)
    delete_point(0); redraw()
    assert win.path_point == (pid, 1) and ws()[1] is me and win.point_panel.title.text().startswith("Point 2")
    ws()[1], ws()[2] = ws()[2], ws()[1]; win.editor.commit(select=pid); redraw()  # moved down
    assert win.path_point == (pid, 2) and win.point_panel.isVisible() and win.point_panel.title.text().startswith("Point 3")
    ws()[1], ws()[2] = ws()[2], ws()[1]; win.editor.commit(select=pid); redraw()  # and up again
    assert win.path_point == (pid, 1)
    win.point_panel.zoom.setValue(win.point_panel.z_to_slider(2.0)); win.point_panel._commit()
    assert me.get("zoom") == pytest.approx(2.0, rel=0.01) and all(w.get("zoom") is None for w in ws() if w is not me)
    # a click adds a point after the selected one: marker, ring and panel go to the new point
    win.req = win._main_req(); redraw(); ren = win.st.ren
    fx = next(x for x in np.linspace(0.3, 0.7, 9) if ren.pick_point(x, 0.6) is not None)
    win.on_click(fx, 0.6); redraw()
    assert len(ws()) == 5 and win.path_point == (pid, 2) and ws()[2] is not me and win.point_panel.title.text().startswith("Point 3")
    win.select_path_point(pid, 1); win.point_panel._delete(); redraw()  # deleting the selected one closes the panel
    assert win.path_point is None and not win.point_panel.isVisible() and me not in ws()
    win.select_path_point(pid, 0); win.editor.undo(); redraw()  # undo makes new copies: closed
    assert win.path_point is None and not win.point_panel.isVisible()
    assert not errs, errs


def QtCore_mods():
    from PySide6 import QtCore
    return QtCore.Qt.NoModifier


# ---- field of view per point in the movie (3.5.0 review): it must reach every frame, motion blur and the flight out ----

def fov_spec(studio, fps=10, then=None):
    spec = tr.default_spec(studio.ds); spec["output"] = dict(spec["output"], size=[320, 180], fps=fps)
    P0 = [60.0, 60.0, 150.0]  # the camera stands still: only the field of view changes
    part = sb.new_part("path", seconds=2, source=dict(kind="points", items=[]),
                       waypoints=[dict(pos=P0, focal=FOC, fov=30.0), dict(pos=P0, focal=FOC, fov=60.0)])
    spec["shots"] = [part] + ([then] if then else [])
    return spec, part


def test_a_field_of_view_change_is_not_a_frozen_movie(studio, tmp_path):
    spec, part = fov_spec(studio)
    job = tr.movie_frames(studio.ren, spec, str(tmp_path / "m.mp4"), poster=False)
    imgs, frames = [], job.frames
    for _, _, img in job:
        imgs.append(img.copy())
    main = [i for i, f in enumerate(frames) if f["part"] == part["id"] and f["phase"] == "main"]
    changed = [i for i in main[1:] if abs(frames[i]["pose"].get("fov", 30.0) - frames[i - 1]["pose"].get("fov", 30.0)) > 0.1]
    assert len(changed) > 5
    repeated = [i for i in changed if np.array_equal(imgs[i], imgs[i - 1])]
    assert not repeated, f"{len(repeated)} of {len(changed)} frames repeat the picture before them"


def test_motion_blur_keeps_the_field_of_view(studio):
    from tomoreveal.movie import motion_blurred
    spec, part = fov_spec(studio)
    main = [f for f in sb.build_plan(spec, studio.ds).frames if f["part"] == part["id"] and f["phase"] == "main"]
    s, nxt = main[-2], main[-1]
    assert s["pose"]["fov"] > 55
    size = (320, 180)
    blur = motion_blurred(studio.ren, s, nxt, spec, size, False, False).astype(int)
    sharp = studio.ren.draw(s, spec, size).astype(int)
    at_30 = studio.ren.draw(dict(s, pose={k: v for k, v in s["pose"].items() if k != "fov"}), spec, size).astype(int)
    assert np.abs(blur - sharp).mean() < 0.2 * np.abs(blur - at_30).mean()


def test_the_flight_out_of_a_path_changes_the_field_of_view_gently(studio):
    spec, part = fov_spec(studio, fps=30, then=sb.new_part("overview", seconds=1))
    frames = sb.build_plan(spec, studio.ds).frames
    end = [f for f in frames if f["part"] == part["id"]][-1]["pose"]["fov"]
    fl = [f["pose"].get("fov", 30.0) for f in frames if f["part"] == spec["shots"][1]["id"] and f["phase"] == "transition"]
    assert end == pytest.approx(60.0) and fl[0] > 58 and fl[-1] == pytest.approx(30.0, abs=0.5)
    assert np.abs(np.diff([end] + fl)).max() < 2 * 30.0 / len(fl)  # eased over the whole flight, no jump back to 30 degrees
