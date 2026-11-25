"""3.5 (late): smoothing that keeps thin parts, smoother membranes in one part, a colour of one's own for solid
membranes, coloured text boxes, arrows kept on the picture (2D), smooth arrows, own and locked views of slides and
holds, turning around the X or Y axis, and the volume shown flipped."""
import numpy as np

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
        assert ds.meshes[mem] is not base_m and ds.meshes[mem] is ds.make_surface(mem, 3)  # the membrane: smoother here
        assert ds.meshes[other] is base_o  # not a membrane: as it was
        assert ren.actors[mem].mapper.GetInput() is ds.meshes[mem]
        ren.draw([f for f in fr if f.get("part") == a["id"]][3], spec, (320, 180))
        assert ds.meshes[mem] is base_m and ds.param(mem, "soften") == 0  # the Structures tab's own setting is unchanged
        ren.surface_async = True; ds._mesh_cache.pop(ds._cache_key(mem, 2), None)
        spec2, _, b2 = two_parts(ds, second_look={"soften": 2})
        ren.draw([f for f in sb.build_plan(spec2, ds).frames if f.get("part") == b2["id"]][-1], spec2, (320, 180))
        assert (mem, 2) in ren.surfaces_pending and ds.meshes[mem] is base_m  # the window makes it in a worker thread first
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
