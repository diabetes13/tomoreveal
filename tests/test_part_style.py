"""3.5: a whole look per part (Ceramic in the overview, see-through membranes in a close-up), smoother membrane
surfaces, soft shadows kept for the sets of structures a close-up switches between, the Presets tab before Movie,
evenly spaced frames when playback cannot keep up."""
import numpy as np

import tomoreveal as tr
from tomoreveal import look as lk
from tomoreveal import storyboard as sb
from tomoreveal.volumes import surface
from test_part_effects import two_parts
from test_tidy_panels import pump, window  # noqa: F401 (fixture)


def test_a_part_can_have_a_whole_look_of_its_own(studio):
    ds, ren = studio.ds, studio.ren
    spec, a, b = two_parts(ds, second_look={"style": "ceramic_glass"})
    lk.apply_look(spec, "ceramic")
    assert sb.part_look(b) == {"style": "ceramic_glass"} and sb.part_look({"look": {"style": "nonsense"}}) == {}
    plan = sb.build_plan(spec, ds); fr = plan.frames
    sa, sb_ = sb.main_range(plan, a["id"]), sb.main_range(plan, b["id"])
    mem = next(kk for kk, c in ds.classes.items() if c["name"] == "membrane")
    ren.draw(fr[sa[0] + 2], spec, (320, 180))
    assert ren.actors[mem].GetProperty().GetOpacity() > 0.99 and np.allclose(ren._legend_col[mem], tr.render.hex2rgb(lk.CREAM))
    ren.draw(fr[sb_[0] + sb_[1] - 1], spec, (320, 180))
    assert ren.actors[mem].GetProperty().GetOpacity() < 0.5  # see-through in this part only
    assert ren._l35["background"] == lk.style35(spec)["background"]  # the background stays the movie's
    spec2, _, b2 = two_parts(ds, second_look={"style": "xray", "outline": "off"})
    f = [g for g in sb.build_plan(spec2, ds).frames if g.get("part") == b2["id"]][-1]
    ren.draw(f, spec2, (320, 180))
    assert ren._l35["material"] == "xray" and ren._l35["outline"] == "off"  # single effects still change on top


def test_softened_surfaces_are_smoother_and_keep_thin_membranes():
    from scipy import ndimage
    rng = np.random.default_rng(0)
    z, y, x = np.mgrid[0:40, 0:60, 0:60]
    bump = ndimage.zoom(rng.integers(-1, 2, (15, 15)).astype(float), 4, order=0)[None]  # 1-voxel steps all over it
    rough_mask = np.abs(z - 20 - 3 * np.sin(x / 9.0) - bump) <= 1.5
    rough = surface(rough_mask, (0, 0, 0), 15, 1)
    soft = surface(rough_mask, (0, 0, 0), 15, 1, soften=3, full_shape=rough_mask.shape)

    def bumpiness(m):
        n, p = np.asarray(m.point_normals), np.asarray(m.points)
        inner = (p[:, 0] > 5) & (p[:, 0] < 54) & (p[:, 1] > 5) & (p[:, 1] < 54)
        return float(np.mean(np.abs(n[inner, 1])))  # the steps tilt the normals sideways
    assert bumpiness(soft) < 0.8 * bumpiness(rough)
    lo, hi = np.asarray(soft.bounds).reshape(3, 2).T
    lo0, hi0 = np.asarray(rough.bounds).reshape(3, 2).T
    assert np.all(np.abs(lo - lo0) < 1.0) and np.all(np.abs(hi - hi0) < 1.0)  # in the same place, also at the grid's edge
    thin = np.zeros((20, 30, 30), bool); thin[9:11, 5:25, 5:25] = True  # 2 voxels thick: still there with "much"
    m = surface(thin, (0, 0, 0), 15, 1, soften=3, full_shape=thin.shape)
    assert m is not None and m.n_points > 100


def test_soft_shadows_are_kept_for_the_sets_a_closeup_switches_between(studio):
    ds = studio.ds; ao = lk.BakedShadows(); ks = sorted(ds.meshes)
    ao.prepare(ds, ks, "normal"); full = ao.fields
    ao.prepare(ds, ks[:-1], "normal"); part = ao.fields
    assert part is not full
    ao.prepare(ds, ks, "normal"); assert ao.fields is full  # back to the first set: nothing worked out again
    assert len(ao._sets) <= lk.BakedShadows.KEEP_SETS


def test_presets_come_before_movie_and_membranes_can_be_smoother(window):
    app, win, errs = window
    names = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert names == ["Data", "Structures", "Explore", "Presets", "Movie", "Look"]
    assert names[win.TAB_PRESETS] == "Presets" and names[win.TAB_MOVIE] == "Movie" and names[win.TAB_LOOK] == "Look"
    ds = win.st.ds; mem = next(c["key"] for c in ds.classes.values() if c["name"] == "membrane")
    k = ds.id_of(mem); before = ds.meshes[k]
    win.look35.smooth_mem.setChecked(True); win.apply_params(); pump(app)
    assert win.spec["classes"][mem]["soften"] == 2 and ds.param(k, "soften") == 2 and ds.meshes[k] is not before
    win.look35.sync(); assert win.look35.smooth_mem.isChecked()
    win.look35.smooth_mem.setChecked(False); win.apply_params(); pump(app)
    assert ds.param(k, "soften") == 0 and not errs


def test_slow_playback_shows_evenly_spaced_frames(window):
    import time
    app, win, errs = window
    win.tabs.setCurrentIndex(win.TAB_MOVIE); pump(app)
    ren = win.st.ren; real = ren.draw
    def slow(*a, **kw):
        time.sleep(0.1); return real(*a, **kw)
    ren.draw = slow
    try:
        win.timeline.set_frame(0); win.start_play(); shown = []
        t0 = time.monotonic()
        while win.playing and time.monotonic() - t0 < 2.5:
            app.processEvents()
            if win.playing and win.playing["last"] is not None and (not shown or shown[-1] != win.playing["last"]):
                shown.append(win.playing["last"])
        win.stop_play()
    finally:
        ren.draw = real
    steps = np.diff(shown)[-4:]
    assert len(shown) > 8 and len(set(steps.tolist())) == 1 and steps[0] > 1, shown  # settles on one even step
    assert not errs
