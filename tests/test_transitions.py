"""Plan 3.5 phase 2: transitions with a start and an end (easing at each end, overlaps, cut / dissolve / fades, arc)."""
import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import storyboard as sb


def campos(p, fr):
    d, _ = fr.axes(p["tilt"], p["az"])
    return np.asarray(p["c"]) + p["dist"] * np.asarray(d)


def framer(spec, ds):
    w, h = spec["output"]["size"]
    return sb.Framer(ds.shape, w / h, float(spec["style"].get("fov", 30)), False)


def two_overviews(ds, **tin):
    spec = tr.default_spec(ds)
    a = sb.new_part("overview", motion="none", seconds=1, view=dict(tilt=10.0, az=0.0, zoom=1.0))
    b = sb.new_part("overview", motion="orbit" if False else "spin", seconds=3, view=dict(tilt=60.0, az=40.0, zoom=1.6), transition=2.0)
    if tin:
        b["transition_in"] = tin
    spec["shots"] = [a, b]
    return spec, a, b


def test_ease_curves():
    u = np.linspace(0, 1, 101)
    assert np.allclose([sb.ease_curve(x) for x in u], [sb.smooth01(x) for x in u])  # gentle / gentle as before
    assert np.allclose([sb.ease_curve(x, "sudden", "sudden") for x in u], u)
    for a in sb.EASES:
        for b in sb.EASES:
            v = [sb.ease_curve(x, a, b) for x in u]
            assert v[0] == pytest.approx(0, abs=1e-6) and v[-1] == pytest.approx(1, abs=1e-6)
            assert all(x <= y + 1e-9 for x, y in zip(v, v[1:]))


def test_default_transition_is_unchanged(studio):
    spec, a, b = two_overviews(studio.ds)
    plain = sb.build_plan(spec, studio.ds).frames
    b["transition_in"] = dict(sb.TRANSITION_DEFAULTS)
    assert [sb.state_key(f) for f in sb.build_plan(spec, studio.ds).frames] == [sb.state_key(f) for f in plain]


def test_leave_suddenly_arrive_very_gently(studio):
    spec, a, b = two_overviews(studio.ds, ease_out_prev="sudden", ease_in_next="very_gentle")
    plan = sb.build_plan(spec, studio.ds); fr = framer(spec, studio.ds)
    t = [f for f in plan.frames if f["part"] == b["id"] and f["phase"] == "transition"]
    pos = np.array([campos(f["pose"], fr) for f in t]); v = np.linalg.norm(np.diff(pos, axis=0), axis=1)
    assert v[0] >= 0.95 * v.max() and v[-1] < 0.05 * v.max()


def test_overlap_with_the_next_part(studio):
    spec, a, b = two_overviews(studio.ds, overlap_next=1.0)
    plan = sb.build_plan(spec, studio.ds)
    t = [f for f in plan.frames if f["part"] == b["id"] and f["phase"] == "transition"]
    m = [f for f in plan.frames if f["part"] == b["id"] and f["phase"] == "main"]
    assert len(t) == 60 and len(m) == 90 - 30  # the main phase started 1 s early
    # the spin already turns at the end of the flight
    assert abs(((t[-1]["pose"]["az"] - 40.0 + 180) % 360) - 180) > 1.0
    fr = framer(spec, studio.ds)
    allf = [f for f in plan.frames if f["part"] == b["id"]]
    pos = np.array([campos(f["pose"], fr) for f in allf]); steps = np.linalg.norm(np.diff(pos, axis=0), axis=1)
    normal = np.linalg.norm(np.diff(np.array([campos(f["pose"], fr) for f in m]), axis=0), axis=1).max()
    spec2, a2, b2 = two_overviews(studio.ds)
    plain = [f for f in sb.build_plan(spec2, studio.ds).frames if f["part"] == b2["id"] and f["phase"] == "transition"]
    flight = np.linalg.norm(np.diff(np.array([campos(f["pose"], fr) for f in plain]), axis=0), axis=1).max()
    assert steps.max() <= (flight + normal) * 1.1  # about the flight and the spin together, never a jump
    assert np.abs(np.diff(steps)).max() <= 0.35 * normal  # and no jump in speed


def test_overlap_with_the_previous_part(studio):
    spec, a, b = two_overviews(studio.ds, overlap_prev=0.5)
    a["seconds"] = 2; a["motion"] = "spin"
    plan = sb.build_plan(spec, studio.ds)
    ma = [f for f in plan.frames if f["part"] == a["id"] and f["phase"] == "main"]
    assert len(ma) == 60 - 15  # the flight took over its last half second
    t = [f for f in plan.frames if f["part"] == b["id"] and f["phase"] == "transition"]
    assert len(t) == 60


def test_cut_dissolve_and_fades(studio):
    spec, a, b = two_overviews(studio.ds, kind="cut")
    plan = sb.build_plan(spec, studio.ds)
    assert not [f for f in plan.frames if f["part"] == b["id"] and f["phase"] == "transition"]
    spec, a, b = two_overviews(studio.ds, kind="dissolve")
    b["motion"] = "none"; spec["style"].update(legend=False, slice_label=False)
    plan = sb.build_plan(spec, studio.ds)
    t = [f for f in plan.frames if f["part"] == b["id"] and f["phase"] == "transition"]
    assert len(t) == 60 and all("blend" in f for f in t)
    mid = t[len(t) // 2]; q = mid["blend"]["q"]
    img = studio.ren.draw(mid, spec, (320, 180), annotate=False).astype(float)
    ia = studio.ren.draw(dict(mid["blend"]["src"], blend=None), spec, (320, 180), annotate=False).astype(float)
    ib = studio.ren.draw(dict(mid, blend=None), spec, (320, 180), annotate=False).astype(float)
    assert np.abs(img - (ia * (1 - q) + ib * q)).max() <= 1.0
    b["transition_in"] = dict(kind="black")
    t = [f for f in sb.build_plan(spec, studio.ds).frames if f["part"] == b["id"] and f["phase"] == "transition"]
    dark = studio.ren.draw(min(t, key=lambda f: abs(f["blend"]["q"] - 0.5)), spec, (320, 180), annotate=False)
    assert dark.max() <= 20


def test_arc_flies_higher(studio):
    spec, a, b = two_overviews(studio.ds)
    fr = framer(spec, studio.ds)
    straight = [f for f in sb.build_plan(spec, studio.ds).frames if f["part"] == b["id"] and f["phase"] == "transition"]
    b["transition_in"] = dict(path="arc")
    arc = [f for f in sb.build_plan(spec, studio.ds).frames if f["part"] == b["id"] and f["phase"] == "transition"]
    i = len(arc) // 2
    assert np.linalg.norm(np.asarray(arc[i]["pose"]["c"]) - straight[i]["pose"]["c"]) > 5
    assert np.allclose(arc[-1]["pose"]["c"], straight[-1]["pose"]["c"])


def test_slow_down_at_the_end(studio):
    spec = tr.default_spec(studio.ds)
    p = sb.new_part("overview", motion="spin", seconds=2)
    assert p["ease_end"] == "gentle" and "ease_end" not in sb.as_legacy(p)
    spec["shots"] = [p]
    az = [f["pose"]["az"] for f in sb.build_plan(spec, studio.ds).frames if f["phase"] == "main"]
    step = [abs(((y - x + 180) % 360) - 180) for x, y in zip(az, az[1:])]
    assert step[-1] < 0.2 * max(step)


def test_drag_the_end_of_a_transition_on_the_timeline(tmp_path, demo, needs_3d):
    QtCore = pytest.importorskip("PySide6.QtCore")
    import argparse
    import sys
    from PySide6 import QtTest
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True)); win.resize(1400, 900)
    errs = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errs.append(v)
    try:
        loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
        for _ in range(30):
            app.processEvents()
        win.spec["shots"].append(sb.new_part("focus", target={"class": win.st.ds.key_of(2)}, caption="v"))
        win.prev_timer.stop(); win.do_preview()
        for _ in range(10):
            app.processEvents()
        c = win.timeline.canvas; pid = win.spec["shots"][-1]["id"]
        c.set_zoom_level(0.4)
        sg, r = next((sg, r) for sg, r in c.segment_rects() if sg.get("transition") == f"{pid}:in")
        c.scroll_to_frame(max(0, sg["start"] - 20))
        r = next(rr for s2, rr in c.segment_rects() if s2 is sg or s2.get("transition") == f"{pid}:in")
        y = int(r.center().y()); x = int(round(r.right()))
        n_hist = len(win.editor.history.states)
        QtTest.QTest.mousePress(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x, y))
        for dx in (10, 20, 30):
            QtTest.QTest.mouseMove(c, QtCore.QPoint(x + dx, y))
        QtTest.QTest.mouseRelease(c, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(x + 30, y))
        for _ in range(10):
            app.processEvents()
        part = win.parts_panel.part(pid)
        expect = round(round(30 / (c.ppf * 30) / 0.1) * 0.1, 2)
        assert sb.transition_of(part)["overlap_next"] == pytest.approx(expect) and expect > 0
        assert len(win.editor.history.states) == n_hist + 1
        win.parts_panel.open(pid)  # the transition settings build
        assert "Transition from part" in win.parts_panel.rows[pid]["transition_head"].text()
        assert not errs, errs
    finally:
        sys.excepthook = old; win.close()
