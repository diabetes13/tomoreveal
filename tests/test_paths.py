"""Plan 3.4 phase 8: camera paths along a structure and through chosen points."""
import math

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import paths
from tomoreveal import storyboard as sb
from tomoreveal.volumes import Dataset


@pytest.fixture(scope="module")
def tube():
    """A curved tube (half circle of radius 70 at z = 20, tube radius 3) as a segmentation without a tomogram."""
    nz, ny, nx = 40, 200, 200
    z, y, x = np.mgrid[0:nz, 0:ny, 0:nx].astype(np.float32)
    r = np.sqrt((x - 100) ** 2 + (y - 100) ** 2)
    lab = ((np.sqrt((r - 70) ** 2 + (z - 20) ** 2) < 3) & (y >= 100)).astype(np.int16)
    ds = Dataset()
    ds.set_segmentation([dict(kind="labels", key="labels:tube", title="tube", arr=lab, classes={1: {"name": "tube"}})], vox=10.0)
    return ds


def path_spec(ds, **kw):
    spec = tr.default_spec(ds)
    part = sb.new_part("path", seconds=4, **kw)
    spec["shots"] = [part]
    return spec, part


def main_poses(spec, ds, pid):
    frames, _ = tr.build_timeline(spec, ds)
    return [f["pose"] for f in frames if f["part"] == pid and f["phase"] == "main"]


def test_centre_line_of_a_curved_tube(tube):
    line = paths.centre_line(tube, [("labels:tube#1", None)])
    r = np.hypot(line[:, 0] - 100, line[:, 1] - 100)
    off = np.hypot(r - 70, line[:, 2] - 20)
    assert len(line) > 150 and np.percentile(off, 95) < 2.0 and off[5:-5].max() < 2.5
    spec, part = path_spec(tube, source=dict(kind="follow", items=[{"structure": "labels:tube#1", "component": None}]))
    poses = main_poses(spec, tube, part["id"])
    assert len(poses) == 120
    idx = [int(np.argmin(np.linalg.norm(line - p["c"], axis=1))) for p in poses]
    assert all(a <= b for a, b in zip(idx, idx[1:])) and idx[-1] - idx[0] > 0.9 * len(line)  # along it, one way
    assert all(p["pos"][2] > p["c"][2] for p in poses)  # "above" the path


def test_follow_the_demo_membrane(studio):
    ds = studio.ds
    line = paths.centre_line(ds, [(ds.key_of(1), None)])
    assert np.ptp(line[:, 1]) > 0.7 * ds.shape[1]  # along the membrane's long direction (y)
    spec, part = path_spec(ds, source=dict(kind="follow", items=[{"structure": ds.key_of(1), "component": None}]), speed="ease")
    poses = main_poses(spec, ds, part["id"])
    img = studio.ren.draw(dict(tr.build_timeline(spec, ds)[0][-1]), spec, (320, 180))
    assert img.shape == (180, 320, 3)
    steps = np.linalg.norm(np.diff([p["c"] for p in poses], axis=0), axis=1)
    assert steps[:5].mean() < steps[55:65].mean()  # slow at the ends


def test_too_compact_to_follow(studio):
    ds = studio.ds
    spec, part = path_spec(ds, source=dict(kind="follow", items=[{"structure": ds.key_of(2), "component": 1}]))
    plan = sb.build_plan(spec, ds)
    assert plan.problems[part["id"]] == paths.TOO_SMALL


def test_waypoints_and_pauses(studio):
    ds = studio.ds
    wps = [dict(pos=[40.0, 40.0, 150.0], focal=[128.0, 160.0, 24.0]), dict(pos=[200.0, 120.0, 120.0], focal=[128.0, 160.0, 24.0], pause=1.0),
           dict(pos=[60.0, 280.0, 90.0], focal=[128.0, 200.0, 24.0])]
    spec, part = path_spec(ds, source=dict(kind="points", items=[]), waypoints=wps)
    poses = main_poses(spec, ds, part["id"])
    assert len(poses) == 120 + 30
    pos = np.array([p["pos"] for p in poses])
    for w in wps:
        assert np.linalg.norm(pos - w["pos"], axis=1).min() <= 1.0
    same = [i for i in range(len(pos)) if np.allclose(pos[i], wps[1]["pos"])]
    assert len(same) >= 30 and same == list(range(same[0], same[0] + len(same)))  # one second at 30 fps, standing still
    curve, at = paths.catmull_rom([np.array(w["pos"]) for w in wps]); cum = paths.arclength(curve)
    expect = cum[at[1]] / cum[-1] * 119
    assert abs(same[0] - expect) <= 2  # at the time given by even speed
    for p in poses[::20]:
        studio.ren.draw(dict(tr.build_timeline(spec, ds)[0][0], pose=p), spec, (320, 180))


def test_the_path_is_drawn_in_the_preview_only(studio):
    ds = studio.ds
    spec, part = path_spec(ds, source=dict(kind="follow", items=[{"structure": ds.key_of(1), "component": None}]))
    f = tr.build_timeline(spec, ds)[0][40]
    studio.ren.preview_part = part["id"]
    a = studio.ren.draw(f, spec, (320, 180), preview=True).astype(int)
    assert studio.ren.path_view and studio.ren.path_view["lines"]  # 3.5: drawn by the window on top of the preview
    b = studio.ren.draw(f, spec, (320, 180)).astype(int)
    assert studio.ren.path_view is None and np.array_equal(a, b)  # never in the picture itself, so never in a movie
    studio.ren.preview_part = None


def test_window_path_parts(tmp_path, demo):
    QtCore = pytest.importorskip("PySide6.QtCore")
    import argparse
    import sys
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    errs = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errs.append(v)

    def settle():
        for _ in range(3):
            if win.worker is not None and win.worker.isRunning():
                loop = QtCore.QEventLoop(); win.worker.finished.connect(loop.quit); loop.exec()
            if win._path_worker is not None and win._path_worker.isRunning():
                loop = QtCore.QEventLoop(); win._path_worker.finished.connect(loop.quit); loop.exec()
            for _ in range(30):
                app.processEvents()
    try:
        win.load(paths=[demo["tomogram"], demo["labels"]]); settle()
        if win.st.ren is None:
            pytest.skip("3D rendering is not available here")
        key = win.st.ds.key_of(1)
        win.editor.insert(sb.new_part("path", source=dict(kind="follow", items=[{"structure": key, "component": None}])))
        pid = win.selected_part; win.prev_timer.stop(); win.do_preview(); settle()  # the line is worked out in a worker
        assert pid not in win.timeline.canvas.plan.problems and win.st.ds.path_cache
        win.parts_panel.open(pid); win.prev_timer.stop(); win.do_preview(); settle()
        assert win.st.ren.path_view and win.st.ren.path_view["lines"]  # drawn in the preview while selected
        pts = sb.new_part("path", source=dict(kind="points", items=[])); win.editor.insert(pts); win.parts_panel.open(pts["id"])
        win.prev_timer.stop(); win.do_preview(); settle()
        from tomoreveal.gui_paths import current_view
        for _ in range(2):
            v = current_view(win); part = win.parts_panel.part(pts["id"])
            part["waypoints"] = list(part["waypoints"]) + [dict(pos=list(v[0] + np.array([30.0, 0, 0]) * len(part["waypoints"])), focal=list(v[1]))]
            win.editor.commit(select=pts["id"])
        win.prev_timer.stop(); win.do_preview(); settle()
        assert pts["id"] not in win.timeline.canvas.plan.problems and len(win.st.ren._markers) == 2
        from PySide6 import QtWidgets
        for v in (1.5, 2.0, 2.5):  # changing a camera setting more than once (3.4.0 bug: the second change failed)
            dist = next(w for w in win.findChildren(QtWidgets.QDoubleSpinBox) if w.toolTip().startswith("How far the camera"))
            dist.setValue(v); app.processEvents()
        assert win.parts_panel.part(pts["id"])["camera"]["distance"] == 2.5
        assert not errs, errs
    finally:
        sys.excepthook = old; win.close()
