"""3.6 phase 8: calm ends of camera paths (R9) and laying out a path from above (R19)."""
import json

import numpy as np
import pytest
from PySide6 import QtCore, QtTest

import tomoreveal as tr
from tomoreveal import camera, paths
from tomoreveal import storyboard as sb

from test_arrows import window  # noqa: F401 (fixture)

FOC = [128.0, 160.0, 24.0]
WPS = [dict(pos=[20.0, 20.0, 150.0], focal=FOC), dict(pos=[200.0, 60.0, 150.0], focal=FOC), dict(pos=[220.0, 300.0, 150.0], focal=FOC)]


def _turns(ds, spec):
    plan = sb.build_plan(spec, ds); fr = sb.Framer(ds.shape, 16 / 9, 30.0)
    D = []
    for f in plan.frames:
        pos, foc, _, _, _ = camera.pose_camera(fr, f["pose"]); d = foc - pos; D.append(d / np.linalg.norm(d))
    D = np.array(D)
    turn = np.degrees(np.arccos(np.clip((D[1:] * D[:-1]).sum(1), -1, 1)))
    return plan, turn


def _three(ds, legacy=False):
    spec = tr.default_spec(ds)
    a = sb.new_part("overview", motion="none", seconds=1)
    p = sb.new_part("path", seconds=6, source=dict(kind="points", items=[]), waypoints=[dict(w) for w in WPS])
    b = sb.new_part("overview", motion="none", seconds=1)
    if legacy:
        p.pop("auto_flight")
    spec["shots"] = [a, p, b]
    return spec, p


def test_flights_into_and_out_of_a_path_turn_gently(studio):
    ds = studio.ds; spec, p = _three(ds)
    plan, turn = _turns(ds, spec)
    trans = [i for i, f in enumerate(plan.frames[1:]) if f["phase"] == "transition"]
    assert len(trans) > 40 and turn[trans].max() < 2.0  # was 10.3° per frame on the way in (0.5 s)
    spec, p = _three(ds, legacy=True)  # older path parts: as before (half a second)
    plan, turn = _turns(ds, spec)
    into = [f for f in plan.frames if f["phase"] == "transition" and f["part"] == p["id"]]
    assert len(into) == 15


@pytest.mark.parametrize("pts", [
    [[10, 10, 30], [200, 250, 30]],
    [[10, 10, 30], [10, 10, 30], [200, 250, 30]],
    [[10, 10, 30], [10.0001, 10, 30], [100, 100, 30], [100, 100, 30.00001]] + [[100 + 10 * k, 100 + 15 * k, 30] for k in range(6)],
])
def test_no_nan_and_the_ends_on_the_points(studio, pts):
    ds = studio.ds; spec = tr.default_spec(ds)
    p = sb.new_part("path", seconds=4, source=dict(kind="points", items=[]), waypoints=[dict(point=list(map(float, q)), pause=0.0) for q in pts])
    spec["shots"] = [p]
    main = [f for f in sb.build_plan(spec, ds).frames if f["part"] == p["id"] and f["phase"] == "main"]
    C = np.array([f["pose"]["c"] for f in main], float)
    assert np.isfinite(C).all() and np.allclose(C[0], pts[0], atol=1e-3) and np.allclose(C[-1], pts[-1], atol=1e-3)


def test_hold_at_the_ends(studio):
    ds = studio.ds; spec, p = _three(ds); p["pause_before"] = 1.0; p["pause_after"] = 0.5
    plan = sb.build_plan(spec, ds); fps = plan.fps
    held = [f for f in plan.frames if f["part"] == p["id"] and f["phase"] == "pause"]
    assert len(held) == round(1.5 * fps)
    main = [f for f in plan.frames if f["part"] == p["id"] and f["phase"] == "main"]
    assert np.allclose(held[0]["pose"]["pos"], main[0]["pose"]["pos"]) and np.allclose(held[-1]["pose"]["pos"], main[-1]["pose"]["pos"])


def test_points_no_closer_than_30_px():
    from tomoreveal.gui_topview import spaced
    from tomoreveal import drawing
    t = np.linspace(0, 1, 400); px = np.column_stack([100 + 500 * t, 200 + 80 * np.sin(6 * t)])
    k = spaced(drawing.simplify(px, 1.5))
    gaps = np.hypot(*np.diff(k, axis=0).T)
    assert gaps.min() >= 15 and np.all(gaps[:-1] >= 30) and np.allclose(k[0], px[0]) and np.allclose(k[-1], px[-1])


def test_older_paths_are_offered_the_smoother_method():
    from tomoreveal.gui_paths import make_paths_smoother, old_path_parts
    a = sb.as_legacy(sb.new_part("path", source=dict(kind="points", items=[]), waypoints=[]))
    b = sb.as_legacy(sb.new_part("path")); c = sb.new_part("path")
    spec = {"shots": [a, b, c, sb.new_part("overview")]}
    assert old_path_parts(spec) == [a, b]
    assert make_paths_smoother(spec) == 2 and not old_path_parts(spec)
    assert a["interp"] == 2 and b["steadiness"] == 50.0 and a["auto_flight"] and b["auto_flight"]


# ---- the window ------------------------------------------------------------------------------------------------------
def _path_part(app, win):
    p = sb.new_part("path", seconds=4, source=dict(kind="points", items=[]), waypoints=[])
    win.editor.insert(p); win.parts_panel.open(p["id"])
    for _ in range(5):
        app.processEvents()
    return p["id"]


def _draw(app, win):
    win.prev_timer.stop(); win.do_preview()
    for _ in range(3):
        app.processEvents()


def test_four_clicks_from_above_and_the_view_comes_back(window):
    from tomoreveal import gui_topview as tv
    app, win, errs = window
    pid = _path_part(app, win); _draw(app, win)
    view0 = json.dumps(win.spec["view"], sort_keys=True)
    win.parts_panel.set(pid, "top_height", 30.0)
    tv.start(win, pid); _draw(app, win)
    assert win.locked_part(say=False) is not None  # the camera cannot be turned meanwhile
    clicks = [(0.3, 0.3), (0.6, 0.35), (0.65, 0.7), (0.35, 0.65)]; want = []
    for f in clicks:
        want.append(win.st.ren.to_plane(f[0], f[1], 30.0)); win.on_click(*f); _draw(app, win)
    ws = win.parts_panel.part(pid)["waypoints"]
    assert len(ws) == 4
    for w, q in zip(ws, want):
        assert np.linalg.norm(np.asarray(w["point"][:2]) - q[:2]) <= 1.0 and w["point"][2] == 30.0
    # the height strip: drag point 2 up
    st = win._top_strip; assert st.isVisible()
    q = st._xy(1, 4, 30.0).toPoint()
    QtTest.QTest.mousePress(st, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, q)
    QtTest.QTest.mouseMove(st, QtCore.QPoint(q.x(), q.y() - 20))
    QtTest.QTest.mouseRelease(st, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(q.x(), q.y() - 20))
    assert win.parts_panel.part(pid)["waypoints"][1]["point"][2] > 31
    QtTest.QTest.keyClick(win, QtCore.Qt.Key_Escape); _draw(app, win)
    assert win.top_edit is None and json.dumps(win.spec["view"], sort_keys=True) == view0 and not st.isVisible()
    win.parts_panel.part(pid)["waypoints"] = []; win.editor.commit(); win.req = {"at": {"part": win.spec["shots"][0]["id"], "u": 0}}
    _draw(app, win)
    assert not errs, errs


def test_draw_a_path_from_above(window):
    from tomoreveal import gui_topview as tv
    app, win, errs = window
    pid = _path_part(app, win); win.parts_panel.set(pid, "top_height", 20.0)
    tv.start(win, pid); _draw(app, win)
    win.top_edit["draw"] = True
    t = np.linspace(0, 1, 160); fr = [(0.2 + 0.6 * x, 0.5 + 0.2 * np.sin(5 * x)) for x in t]
    pts = [win.prev.to_widget(*q).toPoint() for q in fr]
    QtTest.QTest.mousePress(win.prev, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, pts[0])
    for q in pts[1:]:
        QtTest.QTest.mouseMove(win.prev, q)
    QtTest.QTest.mouseRelease(win.prev, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, pts[-1])
    for _ in range(5):
        app.processEvents()
    ws = win.parts_panel.part(pid)["waypoints"]
    stroke_px = np.array([(p.x(), p.y()) for p in pts], float)
    assert 3 <= len(ws) <= paths_len_px(stroke_px) / 30 + 2  # at most one point per 30 px
    line = np.array([win.st.ren.to_plane(f[0], f[1], 20.0)[:2] for f in fr])
    curve, _ = paths.catmull_rom(np.array([w["point"] for w in ws]), 20)
    d = [np.min(np.hypot(*(line - c[:2]).T)) for c in curve]
    assert max(d) <= 3.0 and all(w["point"][2] == 20.0 for w in ws)  # the path stays within 3 voxels of the line
    assert not win.top_edit["draw"]
    tv.stop(win); assert not errs, errs


def paths_len_px(px):
    return float(np.hypot(*np.diff(px, axis=0).T).sum())
