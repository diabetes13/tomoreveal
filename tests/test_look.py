"""Plan 3.4 phase 5: lighting presets and direction, slice shadows, anti-aliasing, surface styles."""
import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import look as lk
from tomoreveal.volumes import load_volume


def overview(st, spec):
    frames, _ = tr.build_timeline(spec, st.ds)
    return dict([f for f in frames if f.get("type") == "overview" and f["phase"] == "main"][0])


def plain(spec):
    spec["style"].update(legend=False, slice_label=False)
    return spec


@pytest.fixture(autouse=True)
def reset_look(studio):
    yield
    spec = tr.default_spec(studio.ds)  # leave the shared renderer with the default look for the other tests
    studio.ren.draw(overview(studio, spec), spec, (320, 180))


def test_every_preset_renders(studio):
    spec = plain(tr.default_spec(studio.ds)); f = overview(studio, spec)
    base = studio.ren.draw(f, spec, (320, 180)).astype(int)
    seen = []
    for _, pre in lk.PRESETS:
        lk.ensure(spec)["lighting"]["preset"] = pre
        img = studio.ren.draw(f, spec, (320, 180)).astype(int)
        assert img.shape == (180, 320, 3) and img.max() > 50, pre
        seen.append(np.abs(img - base).mean())
    assert seen[0] == 0  # Default is exactly the lighting of earlier versions
    assert all(d > 0.3 for d in seen[1:])  # every other preset looks different


def test_light_from_the_left_or_the_right(studio):
    ds = studio.ds; spec = plain(tr.default_spec(ds))
    for k, c in ds.classes.items():
        spec["classes"][c["key"]]["show"] = c["label"] == 2  # only the vesicles
    spec["style"]["tomogram"]["show"] = False; spec["view"].update(tilt=0, az=0); spec["style"]["ortho"] = True
    L = lk.ensure(spec)["lighting"]; L.update(preset="simple", elevation=20, fill=0.0, ambient=0.05)
    f = overview(studio, spec); f["pose"] = dict(f["pose"], tilt=0.0, az=0.0)
    halves = []
    for az in (90, -90):  # +y is to the right on the screen in this top view
        L["azimuth"] = az
        img = studio.ren.draw(f, spec, (320, 180)).astype(int)
        on = img.sum(2) > 30; ys, xs = np.nonzero(on); cx = xs.mean()
        b = img.sum(2)
        halves.append((b[on & (np.arange(320)[None, :] < cx)].mean(), b[on & (np.arange(320)[None, :] >= cx)].mean()))
    (l1, r1), (l2, r2) = halves
    assert r1 > l1 + 20 and l2 > r2 + 20, halves


def test_slice_shadows(studio):
    ds = studio.ds; spec = plain(tr.default_spec(ds)); f = overview(studio, spec); f["k"] = 0
    lab, _ = load_volume(studio_labels(studio))
    L = lk.ensure(spec); L["lighting"].update(preset="simple", azimuth=0, elevation=90)
    studio.ren.draw(f, spec, (320, 180)); plainslice = studio.ren.img.point_data["I"].reshape(ds.shape[1:]).astype(int)
    L["shadows"]["slice"].update(on=True, strength=0.0)
    studio.ren.draw(f, spec, (320, 180))
    assert np.array_equal(studio.ren.img.point_data["I"].reshape(ds.shape[1:]), plainslice)  # strength 0: unchanged
    L["shadows"]["slice"].update(strength=1.0, softness=2.0)
    studio.ren.draw(f, spec, (320, 180)); shadowed = studio.ren.img.point_data["I"].reshape(ds.shape[1:]).astype(int)
    assert shadowed[150, 120] < 0.3 * max(1, plainslice[150, 120])  # under the big vesicle, light from straight above
    empty = np.argwhere(np.lib.stride_tricks.sliding_window_view((lab > 0).any(0), (31, 31)).any((2, 3)) == 0)
    y, x = empty[0] + 15
    assert shadowed[y, x] == plainslice[y, x]  # far from every structure: unchanged


def studio_labels(studio):
    return next(s["path"] for s in studio.seg_sources if s["kind"] == "labels")


def test_real_shadows_are_not_offered_with_software_rendering(studio):
    assert tr.shadows.real_shadows_available(True) == (False, "Not available with software rendering on this computer")


def test_unlit_actors_look_the_same_drawn_by_ambient_light(studio):
    """With shadow maps on, unlit actors (the slice, outlines, scale bar) are drawn lit by ambient light only, because
    VTK's shadow shader fails for unlit actors on NVIDIA cards. The colours must not change, and the actors are unlit again
    afterwards."""
    from tomoreveal.overlays import set_show
    from tomoreveal.shadows import restore_unlit, unlit_to_ambient
    spec = plain(tr.default_spec(studio.ds)); set_show(spec, "scale_bar", True); f = overview(studio, spec)
    for preset in ("simple", "flat"):
        spec.setdefault("look", lk.look_settings(spec))["lighting"]["preset"] = preset
        a = studio.ren.draw(f, spec, (320, 180), annotate=False).astype(int)
        saved = unlit_to_ambient(studio.ren.pl.renderer)
        assert saved and studio.ren.slice_actor.GetProperty().GetLighting()
        studio.ren.pl.render(); b = studio.ren.pl.screenshot(None).astype(int)
        restore_unlit(saved)
        assert not studio.ren.slice_actor.GetProperty().GetLighting()
        assert np.abs(a - b).max() <= 2, preset


def test_anti_aliasing_modes(studio):
    spec = plain(tr.default_spec(studio.ds)); f = overview(studio, spec)
    imgs = {m: studio.ren.draw(f, spec, (320, 180), aa=m).astype(int) for m in ("off", "fxaa", "ssaa", "msaa")}
    assert all(i.shape == (180, 320, 3) for i in imgs.values())
    edges = np.abs(np.diff(imgs["off"].sum(2), axis=1)) > 120  # sharp edges in the picture without anti-aliasing
    d = np.abs(imgs["ssaa"] - imgs["off"]).sum(2)[:, 1:]
    assert d[edges].mean() > 3 * max(0.5, d[~edges].mean())  # Best differs from Off mostly at edges


def test_surface_styles(studio):
    ds = studio.ds; spec = plain(tr.default_spec(ds)); f = overview(studio, spec)
    base = studio.ren.draw(f, spec, (320, 180)).astype(int)
    key = ds.key_of(2)
    for kind in ("cartoon", "dots", "glossy"):
        spec["classes"][key]["style"] = dict(kind=kind)
        img = studio.ren.draw(f, spec, (320, 180)).astype(int)
        assert np.abs(img - base).mean() > 0.2, kind
    # cartoon outlines go with the structure where the reveal plane cuts it away
    for c in ds.classes.values():
        spec["classes"][c["key"]]["style"] = dict(kind="cartoon", outline_width=3)
    g = dict(f, reveal=False, cut=None)
    spec_n = plain(tr.default_spec(ds))
    assert np.abs(studio.ren.draw(g, spec, (320, 180)).astype(int) - studio.ren.draw(g, spec_n, (320, 180)).astype(int)).max() == 0
    # dots and glossy follow the opacity: at 0 they are gone
    hidden = plain(tr.default_spec(ds)); hidden["classes"][key]["show"] = False
    want = studio.ren.draw(f, hidden, (320, 180)).astype(int)
    for kind in ("dots", "glossy"):
        sp = plain(tr.default_spec(ds)); sp["classes"][key].update(opacity=0.0, style=dict(kind=kind, gloss="glass"))
        assert np.abs(studio.ren.draw(f, sp, (320, 180)).astype(int) - want).mean() < 0.5, kind


def test_window_offers_the_settings(tmp_path, demo):
    QtCore = pytest.importorskip("PySide6.QtCore")
    import argparse
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    try:
        loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
        for _ in range(60):
            app.processEvents()
        if win.st.ren is None:
            pytest.skip("3D rendering is not available here")
        win._test_real_shadows()
        assert not win.look_controls.real_sh.isEnabled() and "software rendering" in win.look_controls.real_sh.toolTip()
        win.look_controls.preset.setCurrentIndex(win.look_controls.preset.findData("full"))
        win.look_controls.set_preset("full")
        assert win.spec["look"]["lighting"]["preset"] == "full" and win.spec["look"]["shadows"]["slice"]["on"]
        win.look_controls.ball.moved.emit(180.0, 30.0)
        assert win.spec["look"]["lighting"]["azimuth"] == 180.0 and win.look_controls.az.s.value() == 180
        win.prev_timer.stop(); win.do_preview()
    finally:
        win.close()
