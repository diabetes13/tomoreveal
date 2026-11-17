"""3.3 phase 5: "View from here" parts with their own camera position."""
import argparse
import sys

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import storyboard as sb


@pytest.fixture(scope="module")
def st(demo):
    s = tr.Studio(headless=True)
    s.add_files([demo["tomogram"], demo["labels"]], log=lambda *a: None, make_renderer=False)
    return s


def poses(plan, pid, phase="main"):
    return [f["pose"] for f in plan.frames if f["part"] == pid and f["phase"] == phase]


def test_own_view_is_independent_of_the_main_view(st):
    spec = tr.default_spec(st.ds)
    v = sb.new_part("view", view=dict(tilt=10.0, az=60.0, zoom=1.5)); spec["shots"].append(v)
    plan = sb.build_plan(spec, st.ds)
    own = poses(plan, v["id"]); main = poses(plan, spec["shots"][2]["id"])
    assert own and own[0]["tilt"] == 10.0 and own[0]["az"] == 60.0 and own[0]["dist"] != main[0]["dist"]
    assert sb.part_name(v) == "View from here"
    spec["view"].update(tilt=70, az=-30, zoom=0.8)
    again = poses(sb.build_plan(spec, st.ds), v["id"])
    assert [(p["tilt"], p["az"], round(p["dist"], 6)) for p in again] == [(p["tilt"], p["az"], round(p["dist"], 6)) for p in own]


def test_two_views_in_a_row_fly_from_one_to_the_other(st):
    spec = tr.default_spec(st.ds)
    a = sb.new_part("view", view=dict(tilt=20.0, az=-40.0, zoom=1.0)); b = sb.new_part("view", view=dict(tilt=70.0, az=50.0, zoom=2.0))
    spec["shots"] = [a, b]
    plan = sb.build_plan(spec, st.ds)
    fly = poses(plan, b["id"], "transition")
    assert len(fly) == round(30 * sb.VIEW_TRANSITION)
    for key in ("tilt", "az"):
        vals = [p[key] for p in fly]
        assert all(x <= y for x, y in zip(vals, vals[1:])) and vals[-1] > vals[0]
    d = [p["dist"] for p in fly]
    assert all(x >= y for x, y in zip(d, d[1:]))  # zooming in all the way
    assert poses(plan, b["id"])[0]["tilt"] == 70.0


@pytest.fixture
def window(tmp_path, demo):
    QtCore = pytest.importorskip("PySide6.QtCore")
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    errors = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errors.append(v)
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(30):
        app.processEvents()
    if win.st.ren is None:
        pytest.skip("3D rendering is not available here")
    win.do_preview()
    yield app, win, errors
    sys.excepthook = old; win.close()


def test_dragging_changes_the_selected_view_part(window):
    app, win, errors = window
    main = dict(win.spec["view"])
    win.selected_part = win.spec["shots"][2]["id"]
    win.editor.add("view"); win.do_preview()
    for _ in range(20):
        app.processEvents()
    part = win.parts_panel.part(win.selected_part)
    assert part["view"] == {k: float(v) for k, v in main.items()} and win.timeline.canvas.plan.frames[win.frame]["part"] == part["id"]
    assert win.prev.note.text() == "Changing the view of part 4" and not win.prev.note.isHidden()
    win.on_drag(-40, 60); win.on_release(); win.do_preview()
    assert part["view"]["tilt"] == pytest.approx(main["tilt"] + 15) and part["view"]["az"] == pytest.approx(main["az"] + 14)
    assert win.spec["view"] == main  # the main view is untouched
    s, _, _, _ = win.parts_panel.view_sliders["tilt"]
    assert s.value() == round(main["tilt"] + 15)  # the open line follows
    win.on_wheel(240); assert part["view"]["zoom"] > main["zoom"]
    win.selected_part = win.spec["shots"][2]["id"]; win.req = {"at": {"part": win.selected_part, "u": 0.5}}; win.do_preview()
    assert win.prev.note.isHidden()
    win.on_drag(30, 0); win.on_release()
    assert win.spec["view"]["az"] != main["az"]  # with another part selected, dragging changes the main view
    assert not errors, errors
