"""Plan 3.4 phase 4: per-structure opacity, speck removal and smoothing; the global structures opacity."""
import argparse
import sys

import numpy as np
import pytest
from scipy import ndimage

import tomoreveal as tr


def overview(st, spec):
    frames, _ = tr.build_timeline(spec, st.ds)
    return [f for f in frames if f.get("type") == "overview" and f["phase"] == "main"][0]


def plain(spec):
    spec["style"].update(legend=False, slice_label=False)
    return spec


@pytest.mark.parametrize("aa", ["off", "fxaa", "ssaa", "msaa"])  # msaa: older projects, drawn as Best
def test_opacity_changes_only_that_structure(studio, aa):
    ds = studio.ds; spec = plain(tr.default_spec(ds)); f = overview(studio, spec)
    key = ds.key_of(2)  # vesicles
    a = studio.ren.draw(f, spec, (320, 180), aa=aa).astype(int)
    spec["classes"][key]["show"] = False
    region = np.abs(studio.ren.draw(f, spec, (320, 180), aa=aa).astype(int) - a).sum(2) > 10
    spec["classes"][key]["show"] = True; spec["classes"][key]["opacity"] = 0.4
    b = studio.ren.draw(f, spec, (320, 180), aa=aa).astype(int)
    diff = np.abs(b - a).sum(2) > 10
    assert diff.sum() > 50 and studio.ren.peeling
    assert (diff & ~ndimage.binary_dilation(region, iterations=2)).sum() <= 3
    spec["classes"][key]["opacity"] = 1.0
    studio.ren.draw(f, spec, (320, 180), aa=aa); assert not studio.ren.peeling


def test_off_is_the_same_picture_in_a_new_window_and_after_a_change_of_mode(demo, needs_3d):
    """VTK's window starts with 8 samples: "Off" smoothed until the mode was first changed."""
    from conftest import load_demo_studio
    st = load_demo_studio(demo)
    try:
        spec = plain(tr.default_spec(st.ds)); f = overview(st, spec)
        first = st.ren.draw(f, spec, (320, 180), aa="off").astype(int)
        st.ren.draw(f, spec, (320, 180), aa="fxaa")
        again = st.ren.draw(f, spec, (320, 180), aa="off").astype(int)
        assert np.abs(first - again).max() <= 2 and st.ren.pl.ren_win.GetMultiSamples() == 0
    finally:
        st.ren.close()


def test_all_structures_opacity_and_the_slice_stays_visible(studio):
    ds = studio.ds; spec = plain(tr.default_spec(ds)); f = dict(overview(studio, spec))
    a = studio.ren.draw(f, spec, (320, 180)).astype(int)
    spec["style"]["structures_opacity"] = 0.3; f["slice"] = 0.4  # see-through structures and slice, no anti-aliasing
    b = studio.ren.draw(f, spec, (320, 180), aa=False).astype(int)
    grey = lambda im: ((np.abs(im[..., 0] - im[..., 1]) < 8) & (np.abs(im[..., 1] - im[..., 2]) < 8) & (im.sum(2) > 60)).mean()
    assert grey(b) > 0.5 * grey(a)  # the slice is still there (the 3.2 depth-peeling pitfall)
    sat = lambda im: (np.abs(im[..., 0] - im[..., 2]) > 40).mean()
    assert sat(b) < sat(a)          # and the structures are paler


def test_speck_removal_keeps_large_objects(studio):
    ds = studio.ds; k = ds.id_of(ds.key_of(4))  # particles
    sl = ds.bbox[k]; full = ds.mask(k, sl)
    lbl, n = ndimage.label(full, structure=np.ones((3, 3, 3), bool)); sizes = np.bincount(lbl.ravel())[1:]
    assert (sizes < 50).any() and (sizes >= 50).any()
    before = ds.meshes[k].n_points
    studio.set_structure_params(ds.key_of(4), min_object_voxels=50)
    kept = ds.kept_mask(k, sl)
    lbl2, n2 = ndimage.label(kept, structure=np.ones((3, 3, 3), bool))
    assert n2 == int((sizes >= 50).sum()) and np.bincount(lbl2.ravel())[1:].min() >= 50
    assert ds.meshes[k].n_points < before
    m = ds.id_of(ds.key_of(1))  # the membranes are large: nothing is removed
    studio.set_structure_params(ds.key_of(1), min_object_voxels=50)
    assert ds.kept_mask(m, ds.bbox[m]).sum() == ds.mask(m, ds.bbox[m]).sum()
    studio.set_structure_params(ds.key_of(4), min_object_voxels=0); studio.set_structure_params(ds.key_of(1), min_object_voxels=0)
    assert ds.meshes[k].n_points == before  # back to the cached surface


def test_smoothing_rebuilds_the_surface(studio):
    ds = studio.ds; k = ds.id_of(ds.key_of(2)); a = ds.meshes[k]
    studio.set_structure_params(ds.key_of(2), smoothing=0)
    raw = ds.meshes[k]
    assert raw is not a and not np.allclose(raw.points[:50], a.points[:50])
    studio.set_structure_params(ds.key_of(2), smoothing=15)
    assert ds.meshes[k] is a


QtCore = pytest.importorskip("PySide6.QtCore")


def test_settings_are_saved_in_the_project(demo, tmp_path):
    from tomoreveal.gui import build_gui
    errs = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errs.append(v)

    def wait(app, win, start):
        start()
        if win.worker is not None and win.worker.isRunning():
            loop = QtCore.QEventLoop(); win.worker.finished.connect(loop.quit); loop.exec()
        for _ in range(40):
            app.processEvents()
    try:
        app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
        wait(app, win, lambda: win.load(paths=[demo["tomogram"], demo["labels"]]))
        if win.st.ren is None:
            pytest.skip("3D rendering is not available here")
        key = win.st.ds.key_of(4)
        win.spec["classes"][key].update(opacity=0.5, min_object_voxels=50, smoothing=5); win.spec["style"]["structures_opacity"] = 0.8
        win.apply_params(); surfaces_made(app, win)
        k = win.st.ds.id_of(key)
        assert win.st.ds.param(k, "min_object_voxels") == 50 and win.st.ds.param(k, "smoothing") == 5
        path = str(tmp_path / "p.tomoreveal"); assert win.project.save_to(path); win.close()
        app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
        wait(app, win, lambda: win.project.open(path))
        k = win.st.ds.id_of(key)
        assert win.spec["classes"][key]["opacity"] == 0.5 and win.spec["style"]["structures_opacity"] == 0.8
        assert win.st.ds.param(k, "min_object_voxels") == 50 and win.st.ds.param(k, "smoothing") == 5
        win.editor.history.push(win.spec)
        win.spec["classes"][key]["min_object_voxels"] = 0; win.apply_params(); surfaces_made(app, win); win.editor.record()
        assert win.st.ds.param(k, "min_object_voxels") == 0
        win.editor.undo(); surfaces_made(app, win)  # undo rebuilds the surface as it was
        assert win.st.ds.param(k, "min_object_voxels") == 50
        win.close()
        assert not errs, errs
    finally:
        sys.excepthook = old


def surfaces_made(app, win):
    """New surfaces are made in a worker thread: wait for it, then for the swap."""
    from PySide6 import QtCore
    for _ in range(3):
        for _ in range(20):
            app.processEvents()
        if win.param_timer.isActive():
            win.param_timer.stop(); win.apply_params()
        wk = win._surf_worker
        if wk is not None and wk.isRunning():
            loop = QtCore.QEventLoop(); wk.finished.connect(loop.quit)
            if wk.isRunning():
                loop.exec()
        for _ in range(20):
            app.processEvents()


# ---- surfaces made in the background (G-54) ----
from test_tidy_panels import pump, window  # noqa: E402, F401 (fixture)


def run_for(app, seconds):
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents(); time.sleep(0.005)


def no_surfaces_on_this_thread(monkeypatch):
    """Surfaces made on the window's thread from now on (there should be none)."""
    import threading
    from tomoreveal import volumes
    here = []; real = volumes.surface
    monkeypatch.setattr(volumes, "surface", lambda *a, **k: here.append(threading.current_thread() is threading.main_thread()) or real(*a, **k))
    return here


def test_surfaces_are_made_in_the_background_and_the_pause_stays(window, monkeypatch):
    app, win, errs = window; made = no_surfaces_on_this_thread(monkeypatch)
    ds = win.st.ds; keys = [ds.key_of(2), ds.key_of(4)]; pause = win.param_timer.interval()
    for key in keys:
        win._param_changed(key, "smoothing", 7)
    win.apply_params(); win._param_changed(keys[0], "smoothing", 3)  # changed again while the first is being made
    run_for(app, 2.5)
    assert ds.param(ds.id_of(keys[0]), "smoothing") == 3 and ds.param(ds.id_of(keys[1]), "smoothing") == 7
    win.editor.undo(); run_for(app, 1.5)  # the next change that is noticed without the timer
    assert made and not any(made) and win.param_timer.interval() == pause == 500
    assert not errs, errs


def count_applied(app, win, seconds=1.0):
    runs = []; real = win.apply_params
    win.apply_params = lambda: (runs.append(1), real())[1]
    try:
        win.param_timer.stop(); win.build_controls(); run_for(app, seconds)
    finally:
        del win.apply_params
    return len(runs)


def test_asking_for_surfaces_comes_to_an_end(window, monkeypatch, tmp_path):
    from tomoreveal.volumes import write_mrc
    app, win, errs = window; ds = win.st.ds
    made = no_surfaces_on_this_thread(monkeypatch)
    # a background structure that is not drawn
    k = ds.id_of(ds.key_of(1)); ds.classes[k]["background"] = True; ds.meshes.pop(k); win.st.ren.update_class(k)
    win.spec["classes"][ds.key_of(1)]["smoothing"] = 4
    assert count_applied(app, win) <= 2 and ds.param(k, "smoothing") == 4 and k not in ds.meshes
    # a worker that fails: the message is shown, and it is asked again only when the setting changes
    key = ds.key_of(2); k = ds.id_of(key); real = ds.make_surface; calls = []

    def broken(ref, *a, **kw):
        if ds.id_of(ref) == k:
            calls.append(1); raise MemoryError("not enough memory")
        return real(ref, *a, **kw)
    monkeypatch.setattr(ds, "make_surface", broken)
    win.spec["classes"][key]["smoothing"] = 5; win.spec["classes"][ds.key_of(4)]["smoothing"] = 5
    assert count_applied(app, win) <= 2 and len(calls) == 1 and "not enough memory" in win.msg_lbl.text()
    assert ds.param(ds.id_of(ds.key_of(4)), "smoothing") == 5  # the others are swapped in
    win.build_controls(); run_for(app, 0.8); assert len(calls) == 1
    monkeypatch.setattr(ds, "make_surface", real)
    win._param_changed(key, "smoothing", 6); run_for(app, 1.5); assert ds.param(k, "smoothing") == 6
    # a score structure with nothing above its threshold
    low, vox = np.zeros(ds.shape, np.float32), 10.0
    low[20:24, 20:24, 20:24] = 0.3; p = str(tmp_path / "demo__low.mrc"); write_mrc(p, low, vox)
    wait_for(win, lambda: win.load(paths=[p], features=[p])); pump(app, 40); ds = win.st.ds
    key = next(c["key"] for c in win.info["structures"] if c["kind"] == "score"); assert ds.id_of(key) not in ds.meshes
    win.spec["classes"][key]["smoothing"] = 4
    assert count_applied(app, win) <= 2 and ds.param(ds.id_of(key), "smoothing") == 4
    assert not any(made) and not errs, errs


def wait_for(win, start):
    start()
    if win.worker is not None and win.worker.isRunning():
        loop = QtCore.QEventLoop(); win.worker.finished.connect(loop.quit); loop.exec()
