"""3.5 (late): smoothing that keeps thin parts, smoother membranes in one part, a colour of one's own for solid
membranes, coloured text boxes, arrows kept on the picture (2D), smooth arrows, own and locked views of slides and
holds, turning around the X or Y axis, and the volume shown flipped."""
import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import annotations as an
from tomoreveal import look as lk
from tomoreveal import storyboard as sb
from tomoreveal.volumes import SOFTEN_SIGMA, _keep_thin, surface
from test_part_effects import two_parts
from test_structure_settings import surfaces_made
from test_tidy_panels import pump, window  # noqa: F401 (fixture)


def test_smoothing_keeps_thin_parts_and_small_objects():
    from scipy import ndimage
    shapes = []
    a = np.zeros((20, 30, 30), bool); a[10, 5:25, 5:25] = True; shapes.append(a)       # a sheet 1 voxel thick
    a = np.zeros((20, 30, 30), bool); a[10, 15, 3:27] = True; shapes.append(a)         # a rod 1 voxel thin
    a = np.zeros((20, 30, 30), bool); a[9:11, 14:16, 14:16] = True; shapes.append(a)   # a tiny object
    for m in shapes:
        for level in (1, 2, 3):
            s = surface(m, (0, 0, 0), 15, 1, soften=level, full_shape=m.shape)
            assert s is not None and s.n_points > 10, level
            g = ndimage.gaussian_filter(np.pad(m, 4).astype(np.float32), SOFTEN_SIGMA[level])
            kept = _keep_thin(np.pad(m, 4), g) > 0.5
            assert ndimage.label(kept)[1] == ndimage.label(m)[1]  # nothing lost, nothing split
    flat = np.zeros((20, 40, 40), bool); flat[5:15, 5:35, 5:35] = True; flat[15, 20, 20] = True  # a 1-voxel bump on a block
    g = ndimage.gaussian_filter(flat.astype(np.float32), SOFTEN_SIGMA[3])
    assert not (_keep_thin(flat, g) > 0.5)[15, 20, 20]  # bumps on a surface are still smoothed away


def test_a_part_can_have_smoother_membranes(studio):
    ds, ren = studio.ds, studio.ren
    mem = next(k for k, c in ds.classes.items() if c["name"] == "membrane")
    other = next(k for k, c in ds.classes.items() if c["name"] == "filaments")
    spec, a, b = two_parts(ds, second_look={"soften": 3})
    assert sb.part_look(b) == {"soften": 3}
    fr = sb.build_plan(spec, ds).frames
    base_m, base_o = ds.meshes[mem], ds.meshes[other]
    try:
        ren.draw([f for f in fr if f.get("part") == b["id"]][-1], spec, (320, 180))
        assert ren.mesh_of(mem) is not base_m and ren.mesh_of(mem) is ds.make_surface(mem, 3)  # the membrane: smoother here
        assert ren.mesh_of(other) is base_o  # not a membrane: as it was
        assert ren.actors[mem].mapper.GetInput() is ren.mesh_of(mem)
        assert ds.meshes[mem] is base_m and ds.param(mem, "soften") == 0  # the Structures tab's own setting is unchanged
        ren.draw([f for f in fr if f.get("part") == a["id"]][3], spec, (320, 180))
        assert ren.mesh_of(mem) is base_m
        ren.surface_async = True; ds._mesh_cache.pop(ds._cache_key(mem, 2), None)
        spec2, _, b2 = two_parts(ds, second_look={"soften": 2})
        ren.draw([f for f in sb.build_plan(spec2, ds).frames if f.get("part") == b2["id"]][-1], spec2, (320, 180))
        assert (mem, 2) in ren.surfaces_pending and ren.mesh_of(mem) is base_m  # the window makes it in a worker thread first
    finally:
        ren.surface_async = False; ren.surfaces_pending = set()
        ren.draw([f for f in fr if f.get("part") == a["id"]][3], spec, (320, 180))


def test_solid_membranes_in_a_colour_of_ones_own(studio):
    ds, ren = studio.ds, studio.ren
    mem = next(k for k, c in ds.classes.items() if c["name"] == "membrane")
    spec, a, b = two_parts(ds, second_look={"membranes": "cream"})
    lk.set_look(spec, solid_colour="#3366cc")
    fr = [f for f in sb.build_plan(spec, ds).frames if f.get("part") == b["id"]][-1]
    ren.draw(fr, spec, (320, 180))
    assert np.allclose(ren._legend_col[mem], tr.render.hex2rgb("#3366cc"))
    lk.apply_look(spec, "ceramic")
    assert lk.style35(spec)["solid_colour"] == "#3366cc"  # remembered when another look is chosen


def test_arrows_on_the_picture_and_coloured_text_boxes(studio):
    ds, ren = studio.ds, studio.ren
    spec = tr.default_spec(ds); spec["style"].update(legend=False, slice_label=False)
    spec["shots"] = [sb.new_part("overview", motion="spin", seconds=1)]
    a = an.new_arrow([10, 10, 5], None, None, ""); a["place"] = "2d"; a["tip2d"] = [0.4, 0.6]
    t = an.new_text("Box", x=0.05, y=0.05); t["style"].update(box="fill", fill="#ff0000")
    spec["annotations"] = [a, t]
    fr = [f for f in sb.build_plan(spec, ds).frames if f.get("ann")]
    W, H = 480, 270; tips = []
    for f in (fr[2], fr[len(fr) // 2]):
        img = ren.draw(f, spec, (W, H))
        x0, y0, x1, y1 = ren.last_rects[f"anntip:{a['id']}"]["box"]; tips.append(((x0 + x1) / 2, (y0 + y1) / 2))
    assert np.allclose(tips[0], tips[1]) and np.allclose(tips[0], (0.4 * W, 0.6 * H), atol=1)  # stays put while the camera turns
    bx = ren.last_rects[f"ann:{t['id']}"]["box"]
    px = img[int(bx[1]) + 3, int(bx[0]) + 3].astype(int)
    assert px[0] > 180 and px[1] < 80 and px[2] < 80  # the box is red


def test_arrows_are_drawn_smoothly(studio):
    ds, ren = studio.ds, studio.ren
    spec = tr.default_spec(ds); spec["style"].update(legend=False, slice_label=False); spec["shots"] = [sb.new_part("hold", seconds=1)]
    a = an.new_arrow([10, 10, 5], None, None, ""); a["place"] = "2d"; a["tip2d"] = [0.5, 0.5]; a["tail"]["angle"] = 33
    spec["annotations"] = [a]
    f = [g for g in sb.build_plan(spec, ds).frames if g.get("ann")][-1]
    img = ren.draw(dict(f, slice=0.0), spec, (480, 270)).astype(int)
    grey = img.sum(-1)
    levels = np.unique(grey[(grey > 30) & (grey < 700)])  # edge pixels between the white arrow and black background
    assert len(levels) > 20  # smooth edges have many in-between shades (no anti-aliasing gave hardly any)


def test_slides_and_holds_can_have_a_view_of_their_own(studio):
    ds = studio.ds
    spec = tr.default_spec(ds)
    s = sb.new_part("slide_up", seconds=1, structures="none", view=dict(tilt=0.0, az=0.0, zoom=3.0))
    s["from"] = s["to"] = 5
    h = sb.new_part("hold", seconds=1, view=dict(tilt=10.0, az=20.0, zoom=2.0))
    spec["shots"] = [sb.new_part("overview", motion="none", seconds=1), s, h]
    plan = sb.build_plan(spec, ds); fr = plan.frames
    ps = fr[sum(sb.main_range(plan, s["id"])) - 1]["pose"]; ph = fr[sum(sb.main_range(plan, h["id"])) - 1]["pose"]
    po = fr[sum(sb.main_range(plan, spec["shots"][0]["id"])) - 1]["pose"]
    assert ps["tilt"] == 0.0 and abs(ps["dist"] * 3.0 / po["dist"] - 1.0) < 0.35  # from the top, zoomed in
    assert ph["tilt"] == 10.0 and ph["az"] == 20.0
    assert fr[sum(sb.main_range(plan, s["id"])) - 1]["k"] == 5


def test_a_spin_can_turn_around_x_or_y(studio):
    ds = studio.ds; spec = tr.default_spec(ds)
    for axis in ("x", "y"):
        p = sb.new_part("overview", motion="spin", seconds=2, spin_axis=axis); spec["shots"] = [p]
        plan = sb.build_plan(spec, ds); lo, n = sb.main_range(plan, p["id"])
        poses = [plan.frames[lo + i]["pose"] for i in range(0, n, max(1, n // 8))]
        assert all(q.get("pos") is not None for q in poses)
        dirs = [np.asarray(q["pos"]) - np.asarray(q["c"]) for q in poses]
        ax = np.eye(3)["xyz".index(axis)]
        along = [float(d @ ax) for d in dirs]
        assert np.allclose(along, along[0], atol=1e-3 * np.linalg.norm(dirs[0]))  # turning around that axis: this stays
        assert len({tuple(np.round(d / np.linalg.norm(d), 2)) for d in dirs}) > 4  # it turns
        assert np.allclose(np.linalg.norm(dirs, axis=1), np.linalg.norm(dirs[0]))  # at one distance: nothing leaves the picture


def test_flip_moves_points_and_shows_the_volume_flipped(demo, needs_3d):
    st = tr.Studio(headless=True); st.load([demo["tomogram"], demo["labels"]], log=lambda *a: None, make_renderer=False)
    tomo = np.array(st.ds.tomo); nz, ny, nx = st.ds.shape
    spec = tr.default_spec(st.ds)
    a = an.new_arrow([1.0, 2.0, 3.0]); spec["annotations"] = [a]
    s = sb.new_part("slide_up"); s["from"] = 4; spec["shots"] = [s]
    spec["clip"] = {"x": [0, 10]}
    sb.flip_spec(spec, st.ds.shape, {"x", "z"})
    assert a["anchor"]["xyz"] == [nx - 2.0, 2.0, nz - 4.0] and s["from"] == nz - 5 and spec["clip"]["x"] == [nx - 11, nx - 1]
    info = st.set_flip(("x", "z"), log=lambda *a: None, make_renderer=False)
    assert info["flip"] == ["x", "z"] and np.array_equal(st.ds.tomo, tomo[::-1, :, ::-1])
    assert st.ds.meshes and all(m is not None for m in st.ds.meshes.values())
    st.set_flip((), log=lambda *a: None, make_renderer=False)
    assert np.array_equal(st.ds.tomo, tomo)


def test_locked_parts_keep_their_view_and_the_window_finds_flipped_objects(window):
    app, win, errs = window
    p = sb.new_part("overview", motion="none", seconds=2)
    win.editor.insert(p, "start"); pump(app); win.do_preview()
    pid = p["id"]; win.select_part(pid); win.parts_panel.lock(pid, True); pump(app)
    q = win.parts_panel.part(pid)
    assert q["locked"] and q["view"]  # it keeps the view it had
    plan = win.timeline.canvas.plan; win.frame = sb.main_range(plan, pid)[0] + 1
    before = (dict(q["view"]), dict(win.spec["view"]))
    win.on_drag(40, 20); win.on_release(); win.on_wheel(240); win.nav.apply(dict(tilt=5.0, az=40.0, zoom=2.0))
    assert (q["view"], win.spec["view"]) == before  # nothing moved it
    win.parts_panel.lock(pid, False); pump(app)
    assert not win.parts_panel.part(pid).get("locked")
    # flip: a close-up of one object still shows that object afterwards
    ds = win.st.ds; key = next(c["key"] for c in ds.classes.values() if c["name"] == "vesicles"); k = ds.id_of(key)
    f = sb.new_part("focus", target={"class": key, "component": 1}); win.editor.insert(f, "start"); pump(app)
    lo, hi = ds.target_box({"class": key, "component": 1}, pad=False); nz, ny, nx = ds.shape
    from PySide6 import QtCore
    loop = QtCore.QEventLoop(); win.flip_volume(("x",)); win.worker.finished.connect(loop.quit)
    if win.worker.isRunning():
        loop.exec()
    pump(app, 40)
    g = win.parts_panel.part(f["id"]); ds = win.st.ds
    lo2, hi2 = ds.target_box({"class": key, "component": g["target"]["component"]}, pad=False)
    assert np.allclose(lo2[0], nx - 1 - hi[0], atol=1) and np.allclose(lo2[1:], lo[1:], atol=1)
    assert win.spec["flip"] == ["x"] and win.data.flip_cb["x"].isChecked()
    loop = QtCore.QEventLoop(); win.flip_volume(()); win.worker.finished.connect(loop.quit)
    if win.worker.isRunning():
        loop.exec()
    pump(app, 40)
    assert "flip" not in win.spec and not errs, errs


# ---- a part's own smoothing with many structures (G-16) ----
def many_membranes(folder, n):
    """A label file with n small objects, all of them membranes."""
    import json
    from tomoreveal.volumes import write_mrc
    lab = np.zeros((16, 8 * (n // 10 + 1), 88), np.int16)
    for i in range(n):
        y, x = 8 * (i // 10) + 2, 8 * (i % 10) + 2
        lab[5:10, y:y + 5, x:x + 5] = i + 1
    p = str(folder / "many_membranes.mrc"); write_mrc(p, lab, 10.0)
    with open(str(folder / "many_membranes.json"), "w") as f:
        json.dump({str(i + 1): {"name": f"membrane {i + 1}"} for i in range(n)}, f)
    return p


@pytest.mark.parametrize("n", [30, 60])
def test_a_later_part_has_the_usual_surfaces(tmp_path, needs_3d, n):
    st = tr.Studio(headless=True); st.add_files([many_membranes(tmp_path, n)], log=lambda *a: None); ds, ren = st.ds, st.ren
    try:
        spec, a, b = two_parts(ds, second_look={"soften": 3}); fr = sb.build_plan(spec, ds).frames
        one, two = ([f for f in fr if f.get("part") == p["id"]][-1] for p in (a, b)); usual = dict(ds.meshes)
        for _ in range(2):
            ren.draw(two, spec, (160, 90))
            assert all(ren.actors[k].mapper.GetInput() is ds.make_surface(k, 3) for k in usual)
            ren.draw(one, spec, (160, 90))
            assert all(ren.actors[k].mapper.GetInput() is m for k, m in usual.items())
            assert all(ds.meshes[k] is m and ren.mesh_of(k) is m for k, m in usual.items())  # no part changes the data's own
    finally:
        st._drop_renderer()


@pytest.mark.parametrize("n", [30, 60])
def test_the_window_asks_for_a_parts_surfaces_once(tmp_path, needs_3d, n):
    from test_structure_settings import run_for, wait_for
    app, win = build_window(tmp_path)
    try:
        wait_for(win, lambda: win.load(paths=[many_membranes(tmp_path, n)])); pump(app, 30)
        win.spec, _, b = two_parts(win.st.ds, second_look={"soften": 3}); asked = []; real = win._make_part_surfaces
        win._make_part_surfaces = lambda: (asked.append(len(win.st.ren.surfaces_pending)), real())
        win.req = {"t": 1.0}; win.do_preview(); assert asked == [n]; win._surf_worker.wait(60000); run_for(app, 0.5)
        win.req = {"t": 1.0}; win.do_preview(); run_for(app, 0.5)
        assert not any(asked[1:]) and all(win.st.ren.mesh_of(k) is win.st.ds.make_surface(k, 3) for k in win.st.ds.classes)
    finally:
        win.close()


def build_window(folder):
    import argparse
    from tomoreveal.gui import build_gui
    return build_gui(argparse.Namespace(files=[], outdir=str(folder), headless=True))


# ---- a flip changes the data and the settings together (G-38) ----
def one_particle(win, number=1):
    ds = win.st.ds; key = next(c["key"] for c in ds.classes.values() if c["name"] == "particles")
    f = sb.new_part("focus", target={"class": key, "component": number}); win.editor.insert(f, "start")
    return f["id"]


def test_a_failed_flip_changes_nothing(window, monkeypatch):
    import json
    from tomoreveal.volumes import Dataset
    from test_structure_settings import run_for, wait_for
    app, win, errs = window; ds = win.st.ds; one_particle(win); win.spec["annotations"] = [an.new_arrow([10.0, 20.0, 30.0])]
    win.spec["shots"].append(sb.new_part("focus", region=dict(lo=[10, 20, 30], hi=[20, 30, 40]))); run_for(app, 1.0)
    spec, tomo, boxes = json.dumps(win.spec, default=str), ds.tomo, dict(ds.bbox)

    def broken(*a, **k):
        raise MemoryError("not enough memory")
    monkeypatch.setattr(Dataset, "set_segmentation", broken)
    wait_for(win, lambda: win.flip_volume(("x", "z"))); pump(app, 40)
    assert json.dumps(win.spec, default=str) == spec and win.st.flip == () and ds.tomo is tomo and ds.bbox == boxes
    assert "not enough memory" in win.msg_lbl.text() and not win.data.flip_cb["x"].isChecked()
    assert win.st.ren is not None and win.st.ren.actors
    monkeypatch.undo(); wait_for(win, lambda: win.flip_volume(("x",))); pump(app, 40)  # and the next flip works, once
    assert win.spec["flip"] == ["x"] and np.array_equal(win.st.ds.tomo, tomo[:, :, ::-1])
    nx = ds.shape[2]; r = win.spec["shots"][-1]["region"]
    assert (r["lo"][0], r["hi"][0]) == (nx - 1 - 20, nx - 1 - 10) and not errs, errs


def test_history_after_a_flip_starts_with_the_new_object_numbers(window):
    from test_structure_settings import run_for, wait_for
    app, win, errs = window; pid = one_particle(win); run_for(app, 1.0)
    changed = 0
    for axes in (("z",), ("y", "z"), ("x",)):
        was = win.parts_panel.part(pid)["target"]["component"]
        wait_for(win, lambda: win.flip_volume(axes)); run_for(app, 1.2)
        changed += win.parts_panel.part(pid)["target"]["component"] != was
        assert not win.editor.history.can_undo and len(win.editor.history.states) == 1
    assert changed and not errs, errs


def test_the_flip_stays_with_the_movie(window, demo):
    from test_structure_settings import run_for, wait_for
    app, win, errs = window; st = win.st; files = [demo["tomogram"], demo["labels"]]; tomo = st.ds.tomo

    def again(before_last=lambda: None):
        for key in [s["key"] for s in st.seg_sources]:
            wait_for(win, lambda: win.data.remove(key)); pump(app, 30)
        before_last(); wait_for(win, lambda: win.data.remove("file:" + st._norm(demo["tomogram"]))); pump(app, 30)
        assert win.spec is None
        wait_for(win, lambda: win.load(paths=files)); pump(app, 40)
    win.spec["shots"].append(sb.new_part("focus", region=dict(lo=[10, 20, 30], hi=[20, 30, 40])))
    wait_for(win, lambda: win.flip_volume(("x",))); pump(app, 40); lo = list(win.spec["shots"][-1]["region"]["lo"])
    again()
    assert win.spec["flip"] == ["x"] == win.st.info()["flip"] and np.array_equal(win.st.ds.tomo, tomo[:, :, ::-1])
    assert win.spec["shots"][-1]["region"]["lo"] == lo
    win.project.ask_save = lambda what: "discard"
    again(lambda: win.project.mark_saved())  # nothing to lose, so the movie is not kept; the new one records the data's flip
    assert win.spec.get("flip") == ["x"] == win.st.info()["flip"]
    win.project.new(); wait_for(win, lambda: win.load(paths=files)); pump(app, 40)
    assert "flip" not in win.spec and win.st.flip == () and np.array_equal(win.st.ds.tomo, tomo) and not errs, errs


# ---- the view you see is the view you change (G-43, N-10) and its undo (G-42) ----

def own_view_parts(app, win):
    a = sb.new_part("overview", motion="none", seconds=2, view=dict(tilt=30.0, az=10.0, zoom=1.0))
    b = sb.new_part("overview", motion="none", seconds=2, view=dict(tilt=50.0, az=-20.0, zoom=1.0))
    win.spec["shots"] += [a, b]; win.editor.commit(); win.selected_part = None; pump(app)
    return a["id"], b["id"], win._main_req()["at"]["part"]


def go(app, win, pid, u=0.5):
    win.req = {"at": {"part": pid, "u": u}}; win.prev_timer.stop(); win.do_preview(); pump(app)
    assert win.timeline.canvas.plan.frames[win.frame]["part"] == pid


def views(win, *pids):
    return [dict(win.parts_panel.part(p)["view"]) if p else dict(win.spec["view"]) for p in pids]


def test_dragging_changes_the_view_on_screen(window):
    """G-43: over an own-view part that was not selected, a drag changed the main view, which is not on screen."""
    app, win, errs = window
    a, b, main = own_view_parts(app, win)
    go(app, win, a); before = views(win, a, b, None)
    win.on_drag(40, 0); win.on_release(); pump(app)
    after = views(win, a, b, None)
    assert after[0]["az"] != before[0]["az"] and after[1:] == before[1:]
    assert win.prev.note.text().startswith("Changing the view of part")
    win.nav.wheel(120, (0.5, 0.5)); win.nav._step(); win.nav.glide(win.nav.target_view(), 0); pump(app)
    assert views(win, a)[0]["zoom"] != after[0]["zoom"] and views(win, b, None) == after[1:]
    # a glide on part 1, then the playhead moves to part 2 half-way: it ends on part 1
    v = views(win, a)[0]; win.nav.push(force=True); win.nav.glide(dict(win.nav.view(), az=v["az"] + 60.0), 0.5)
    win.nav._step()
    win.on_scrub(next(j for j, f in enumerate(win.timeline.canvas.plan.frames) if f.get("part") == b))
    pump(app)
    assert views(win, a)[0]["az"] == pytest.approx(((v["az"] + 60.0 + 180) % 360) - 180) and views(win, b, None) == after[1:]
    assert win.nav._anim is None
    assert not errs, errs


@pytest.mark.parametrize("where", ["path", "card", "hold after a close-up", "own view, Tilt slider"])
def test_changing_the_main_view_shows_it(window, where):
    """N-10: where the main view is not on screen, changing it (drag, or the Main view sliders) showed nothing."""
    app, win, errs = window
    a, b, main = own_view_parts(app, win)
    key = win.st.ds.key_of(2)
    if where == "path":
        p = sb.new_part("path", seconds=3, source=dict(kind="points", items=[]),
                        waypoints=[dict(pos=[60.0, 60.0, 120.0], focal=[80.0, 90.0, 24.0]), dict(pos=[200.0, 260.0, 120.0], focal=[180.0, 230.0, 24.0])])
    elif where == "card":
        p = sb.new_part("card", seconds=2)
    else:
        p = None
    if where == "hold after a close-up":
        f = sb.new_part("focus", target=sb.make_target([(key, None)]), caption=""); p = sb.new_part("hold", seconds=2)
        win.spec["shots"] += [f, p]
    elif p is not None:
        win.spec["shots"].append(p)
    win.editor.commit(); win.selected_part = None; pump(app)
    pid = a if p is None else p["id"]
    go(app, win, pid); m0 = views(win, None)[0]
    if where.startswith("own view"):
        win.set_view(tilt=m0["tilt"] + 10.0); win.prev_timer.stop(); win.do_preview(); pump(app)
    else:
        win.on_drag(40, 0); win.on_release(); win.prev_timer.stop(); win.do_preview(); pump(app)
    assert views(win, None)[0] != m0
    assert win.timeline.canvas.plan.frames[win.frame]["part"] == main  # the main view is shown
    assert not errs, errs


def test_undo_of_a_view_change_goes_back_to_its_own_view(window):
    """G-42: one history for all views: an undo wrote the main view into a part's own view, and the other way round."""
    app, win, errs = window
    a, b, main = own_view_parts(app, win)
    nav = win.nav
    go(app, win, main); m0, a0 = views(win, None, a)
    nav.push(force=True); nav.apply(dict(nav.view(), az=m0["az"] + 30.0), quick=False); m1 = views(win, None)[0]
    go(app, win, a)
    nav.push(force=True); nav.apply(dict(nav.view(), az=a0["az"] + 40.0), quick=False); a1 = views(win, a)[0]
    go(app, win, b, 0.2)
    nav.undo(); pump(app); win.prev_timer.stop(); win.do_preview(); pump(app)
    assert views(win, None, a) == [m1, a0] and win.timeline.canvas.plan.frames[win.frame]["part"] == a  # and shown
    nav.undo(); pump(app); win.prev_timer.stop(); win.do_preview(); pump(app)
    assert views(win, None, a) == [m0, a0] and win.timeline.canvas.plan.frames[win.frame]["part"] == main
    nav.redo(); nav.redo(); pump(app)
    assert views(win, None, a) == [m1, a1]
    # a part locked, or deleted, since: its steps are skipped and nothing else changes
    go(app, win, a); nav.push(force=True); nav.apply(dict(nav.view(), az=a1["az"] + 5.0), quick=False); a2 = views(win, a)[0]
    win.parts_panel.lock(a, True); pump(app); go(app, win, main); b0 = views(win, b)[0]
    nav.undo(); nav.undo(); pump(app)
    assert views(win, a, b)[0] == a2 and views(win, b)[0] == b0
    assert views(win, None)[0] == m0  # (the main view's own step was the next one back)
    win.parts_panel.lock(a, False); win.editor.delete(a); pump(app); n = len(nav.history)
    nav.redo(); pump(app)
    assert views(win, b)[0] == b0
    win.project.confirm_discard = lambda what: True; win.start_over()
    assert not nav.history and not nav.future
    assert not errs, errs
