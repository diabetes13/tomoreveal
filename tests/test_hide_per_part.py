"""3.6 phase 4 (R18): hide structures in some parts only, with eyes in the Structures panel on the right."""
import numpy as np

import tomoreveal as tr
from tomoreveal import storyboard as sb

from test_tidy_panels import pump, window  # noqa: F401 (fixture)


def _frames_of(plan, pid):
    lo, n = sb.main_range(plan, pid)
    return plan.frames[lo:lo + n]


def test_hidden_in_one_part_only_and_faded(studio):
    ds = studio.ds; spec = tr.default_spec(ds); k = ds.key_of(2)
    a, b, c = spec["shots"][:3]
    b["each"] = {k: "hide"}
    plan = sb.build_plan(spec, ds); fps = plan.fps
    assert all(f.get("vis", {}).get(k) == 1.0 for f in _frames_of(plan, b["id"])[int(fps * 0.3):-int(fps * 0.3)])
    assert all(not f.get("vis") for f in _frames_of(plan, a["id"])[:-int(fps)])
    assert all(not f.get("vis") for f in _frames_of(plan, c["id"])[int(fps):])
    v = [f.get("vis", {}).get(k, 0.0) for f in plan.frames]
    assert max(abs(np.diff(v))) < 0.15  # fades over about 0.5 s, never a jump
    ramp = sum(1 for x in v if 0.02 < x < 0.98)
    assert fps * 0.3 <= ramp <= fps * 1.4  # two fades (in and out) of about half a second together... each
    lo, _ = sb.main_range(plan, b["id"]); f = plan.frames[lo + int(fps)]
    img_hidden = studio.ren.draw(f, spec, (320, 180)); g = dict(f); g.pop("vis")
    img_shown = studio.ren.draw(g, spec, (320, 180))
    assert np.abs(img_hidden.astype(int) - img_shown.astype(int)).sum(2).astype(bool).sum() > 50
    assert sb.state_key(f) != sb.state_key(g)


def test_plans_without_hiding_are_unchanged(studio):
    ds = studio.ds; spec = tr.default_spec(ds)
    plan = sb.build_plan(spec, ds)
    assert not any("vis" in f for f in plan.frames)
    k = ds.key_of(2)  # a close-up's own 'each' keeps working as in 3.5, without "vis"
    spec["shots"] = [sb.new_part("focus", target={"class": ds.key_of(1)}, seconds=2)]; spec["shots"][0]["each"] = {k: "hide"}
    assert not any("vis" in f for f in sb.build_plan(spec, ds).frames)


def test_eyes_hide_in_the_selected_part_and_undo(window, tmp_path):
    app, win, errs = window
    assert not win.struct_dock.isHidden()
    k = next(iter(win._grid_rows)); eye = win._grid_rows[k]["cb"]
    pid = win.spec["shots"][1]["id"]; win.select_part(pid); pump(app)
    assert win.vis_scope.scope() == "part" and eye.isChecked()
    eye.click(); pump(app)  # hide in this part only
    assert win.spec["shots"][1]["each"] == {k: "hide"} and win.spec["classes"][k]["show"]
    assert not eye.isChecked() and eye.dot
    other = win.spec["shots"][0]["id"]; win.select_part(other); pump(app)
    assert eye.isChecked() and not eye.dot  # shown in the other part
    win.vis_scope.combo.setCurrentIndex(0); win.vis_scope.combo.activated.emit(0); pump(app)
    assert win.vis_scope.scope() == "movie" and eye.isChecked() and eye.dot  # dot: hidden in some part
    eye.click(); pump(app)  # whole movie
    assert not win.spec["classes"][k]["show"] and win.spec["shots"][1]["each"] == {k: "hide"}  # the part's choice stays
    win.editor.undo(); pump(app)
    assert win.spec["classes"][k]["show"] and win._grid_rows[k]["cb"].isChecked()
    win.editor.undo(); pump(app)
    assert not win.spec["shots"][1].get("each")
    win.editor.redo(); pump(app)
    assert win.spec["shots"][1]["each"] == {k: "hide"}
    path = str(tmp_path / "p.tomoreveal"); assert win.project.save_to(path)
    win.spec["shots"][1].pop("each")
    from PySide6 import QtCore
    loop = QtCore.QEventLoop(); win.project.open(path)
    if win.worker is not None and win.worker.isRunning():
        win.worker.finished.connect(loop.quit); loop.exec()
    pump(app)
    assert win.spec["shots"][1]["each"] == {k: "hide"}
    win.select_part(win.spec["shots"][1]["id"]); win.vis_scope.combo.setCurrentIndex(1); win.vis_scope.combo.activated.emit(1); pump(app)
    assert not win._grid_rows[k]["cb"].isChecked()
    assert not errs
