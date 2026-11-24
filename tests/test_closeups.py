"""Plan 3.4 phase 6: close-ups of several objects, the other structures' colour and opacity, vignette, background blur."""
import argparse
import sys

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import look as lk
from tomoreveal import storyboard as sb


def main_frame(st, spec, pid, i=None):
    frames, _ = tr.build_timeline(spec, st.ds)
    fr = [f for f in frames if f["part"] == pid and f["phase"] == "main"]
    return fr[len(fr) // 2 if i is None else i]


def test_two_whole_structures_in_focus(studio):
    ds = studio.ds; spec = tr.default_spec(ds)
    mem, ves, fil = ds.key_of(1), ds.key_of(2), ds.key_of(3)
    part = sb.new_part("focus", target=sb.make_target([(mem, None), (ves, None)]), others=dict(colour="grey", opacity=0.3), motion="none")
    sb.insert_part(spec, part)
    f = main_frame(studio, spec, part["id"])
    studio.ren.draw(f, spec, (320, 180))
    a = studio.ren.actors
    for k in (1, 2):
        assert np.allclose(a[k].GetProperty().GetColor(), tr.common.hex2rgb(spec["classes"][ds.key_of(k)]["color"]))
        assert a[k].GetProperty().GetOpacity() == pytest.approx(1.0)
    assert np.allclose(a[3].GetProperty().GetColor(), (0.27, 0.27, 0.27)) and a[3].GetProperty().GetOpacity() == pytest.approx(0.3)
    lo, hi = ds.targets_box(part["target"])
    for key in (mem, ves):
        blo, bhi = ds.target_box({"class": key})
        assert np.all(lo <= blo + 1e-6) and np.all(hi >= bhi - 1e-6)
    assert np.allclose(f["pose"]["c"], (lo + hi) / 2)
    assert sb.closeup_caption([(mem, None), (ves, None)], lambda k: spec["classes"][k]["name"]) == "membrane and vesicles"
    assert sb.part_name(part, spec, ds) == "Close-up: membrane and vesicles"
    assert studio.ren.last_rects["legend"]["shown"]


def test_old_hide_means_opacity_zero():
    assert sb.others_of("hide") == ("keep", 0.0) and sb.others_of("grey") == ("grey", 1.0) and sb.others_of("keep") == ("keep", 1.0)
    assert sb.others_of(dict(colour="darken", opacity=0.3)) == ("darken", 0.3)
    assert sb.target_items({"class": "a", "component": 3}) == [("a", 3)]


def test_vignette_darkens_edges_but_not_the_text(studio):
    ds = studio.ds; spec = tr.default_spec(ds)
    f = [x for x in tr.build_timeline(spec, ds)[0] if x["type"] == "slide" and x["phase"] == "main"][5]
    base = studio.ren.draw(f, spec, (320, 180)).astype(int); rect = studio.ren.last_rects["slice_label"]["box"]
    lk.ensure(spec)["vignette"].update(when="always", strength=1.0, size=0.3, softness=0.2, shape="round")
    img = studio.ren.draw(f, spec, (320, 180)).astype(int)
    w = lk.vignette_weight(320, 180, 160, 90, 0.3, 0.2)
    assert w[0, 0] == pytest.approx(1.0) and w[90, 160] == 0.0
    corner = (slice(150, 180), slice(250, 320))
    assert img[corner].sum() <= base[corner].sum() * (1 - w[corner].mean()) + 0.02 * base[corner].sum()
    assert np.array_equal(img[85:95, 155:165], base[85:95, 155:165])  # the clear centre is unchanged
    x0, y0, x1, y1 = [int(round(v)) for v in rect]
    assert img[y0:y1, x0:x1].max() == base[y0:y1, x0:x1].max() == 255  # the slice number is drawn after the vignette


def test_background_blur_keeps_the_focused_object_sharp(studio):
    ds = studio.ds; spec = tr.default_spec(ds); spec["style"].update(legend=False, slice_label=False)
    part = sb.new_part("focus", target=sb.make_target([(ds.key_of(2), 1)]), others=dict(colour="keep", opacity=1.0), motion="none", caption="")
    sb.insert_part(spec, part)
    f = main_frame(studio, spec, part["id"])
    sharp = studio.ren.draw(f, spec, (480, 270)).astype(float).sum(2)
    part["vignette"] = dict(on=True, strength=0.0, blur=dict(on=True, strength=1.0))
    blurred = studio.ren.draw(f, spec, (480, 270)).astype(float).sum(2)
    part["vignette"] = None; studio.ren.draw(f, spec, (480, 270))  # DOF off again for the other tests
    energy = lambda im: np.abs(np.diff(im, axis=1))[:-1, :] + np.abs(np.diff(im, axis=0))[:, :-1]
    es, eb = energy(sharp), energy(blurred)
    centre = (slice(80, 190), slice(160, 320))  # the focused vesicle fills the middle of the frame
    outside = np.ones_like(es, bool); outside[centre] = False; outside[:20] = outside[-20:] = False
    assert eb[centre].mean() / es[centre].mean() > eb[outside].mean() / es[outside].mean() + 0.1


QtCore = pytest.importorskip("PySide6.QtCore")


@pytest.fixture
def window(tmp_path, demo):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    errs = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errs.append(v)
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(40):
        app.processEvents()
    if win.st.ren is None:
        pytest.skip("3D rendering is not available here")
    yield app, win, errs
    sys.excepthook = old; win.close()


def test_pick_several_objects_then_one_close_up_each(window):
    app, win, errs = window
    part = sb.new_part("focus", target=sb.make_target([]), caption=""); win.editor.insert(part)
    pid = part["id"]; win.parts_panel.open(pid)
    win.start_multi_pick(pid); win.prev_timer.stop(); win.do_preview()
    spec = win.spec_for(); seen = {}
    for fx in np.linspace(0.2, 0.8, 13):
        for fy in np.linspace(0.25, 0.8, 8):
            p = win.st.pick(spec, {"frame": win.frame, "x": fx, "y": fy})
            if p.get("found") and p.get("component") is not None:
                seen.setdefault((p["class"], p["component"]), (fx, fy))
    picks = list(seen.items())[:3]
    assert len(picks) == 3
    for _, (fx, fy) in picks:
        win.on_click(fx, fy); win.prev_timer.stop(); win.do_preview()
    items = sb.target_items(win.parts_panel.part(pid)["target"])
    assert items == [k for k, _ in picks]
    win.on_click(*picks[0][1])
    assert sb.target_items(win.parts_panel.part(pid)["target"]) == [k for k, _ in picks[1:]]
    win.on_click(*picks[0][1])  # back to three
    win.end_multi_pick()
    n = len(win.spec["shots"])
    win.editor.split_closeup(pid)
    shots = win.spec["shots"]; i = len(shots) - 3
    assert len(shots) == n + 2 and all(len(sb.target_items(p["target"])) == 1 for p in shots[i:])
    assert [sb.target_items(p["target"])[0] for p in shots[i:]] == [picks[1][0], picks[2][0], picks[0][0]]
    win.editor.undo()
    assert len(win.spec["shots"]) == n and len(sb.target_items(win.spec["shots"][-1]["target"])) == 3
    assert not errs, errs


def _objects(win, n):
    spec = win.spec_for(); seen = {}
    for fx in np.linspace(0.2, 0.8, 13):
        for fy in np.linspace(0.25, 0.8, 8):
            p = win.st.pick(spec, {"frame": win.frame, "x": fx, "y": fy})
            if p.get("found") and p.get("component") is not None:
                seen.setdefault((p["class"], p["component"]), (fx, fy))
    return list(seen.items())[:n]


def test_a_later_close_up_does_not_add_to_the_first(window):
    """3.5 bug fix: with "+ Click objects" still on for close-up A, clicks for a new close-up went into A."""
    app, win, errs = window
    a = sb.new_part("focus", target=sb.make_target([]), caption=""); win.editor.insert(a)
    win.parts_panel.open(a["id"]); win.start_multi_pick(a["id"]); win.prev_timer.stop(); win.do_preview()
    picks = _objects(win, 3); assert len(picks) == 3
    win.on_click(*picks[0][1])
    assert sb.target_items(win.parts_panel.part(a["id"])["target"]) == [picks[0][0]]
    # without pressing Esc: add a new close-up by clicking an object
    n = len(win.spec["shots"])
    win.editor.pick_closeup(); win.prev_timer.stop(); win.do_preview()
    assert win.multi_pick is None
    win.on_click(*picks[1][1])
    assert len(win.spec["shots"]) == n + 1
    assert sb.target_items(win.parts_panel.part(a["id"])["target"]) == [picks[0][0]]  # A unchanged
    b = win.spec["shots"][-1] if win.spec["shots"][-1]["id"] != a["id"] else win.spec["shots"][-2]
    assert sb.target_items(b["target"])[0][0] == picks[1][0][0]
    # selecting another part also ends the clicking for A
    win.parts_panel.open(a["id"]); win.start_multi_pick(a["id"])
    win.select_part(b["id"]); assert win.multi_pick is None
    # later, objects can still be added to A
    win.select_part(a["id"]); win.start_multi_pick(a["id"]); win.prev_timer.stop(); win.do_preview()
    win.on_click(*picks[2][1]); win.end_multi_pick()
    assert sb.target_items(win.parts_panel.part(a["id"])["target"]) == [picks[0][0], picks[2][0]]
    assert not errs, errs


def test_hide_the_others_and_start_from_the_top(studio):
    ds = studio.ds; spec = tr.default_spec(ds)
    part = sb.new_part("focus", target=sb.make_target([(ds.key_of(2), None)]), motion="none")
    assert part["tilt"] == 0.0 and "tilt" not in sb.as_legacy(part)
    part["others"] = sb.set_others_choice(part["others"], colour="hide")
    assert sb.others_choice(part["others"]) == ("hide", 1.0) and sb.others_of(part["others"]) == ("keep", 0.0)
    sb.insert_part(spec, part)
    f = main_frame(studio, spec, part["id"])
    assert f["pose"]["tilt"] == pytest.approx(0.0)  # looking straight down
    studio.ren.draw(f, spec, (320, 180))
    a = studio.ren.actors
    assert a[2].GetVisibility() and not a[1].GetVisibility() and not a[3].GetVisibility()
    back = sb.set_others_choice(part["others"], colour="grey")
    assert back == dict(colour="grey", opacity=1.0)  # the opacity comes back when another colour is chosen
    assert sb.others_choice("hide") == ("hide", 1.0)
