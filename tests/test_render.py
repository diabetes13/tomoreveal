"""Tests that draw: one frame, the clip box, picking, a short movie and the window. Skipped where no renderer can start."""
import os

import numpy as np
import pytest

import tomoreveal as tr


def _overview(st, spec):
    frames, _ = tr.build_timeline(spec, st.ds)
    return [f for f in frames if f["shot"] == 2][0]


def test_frame_shows_tomogram_and_structures(studio):
    spec = tr.default_spec(studio.ds)
    img = studio.ren.draw(_overview(studio, spec), spec, (640, 360))
    assert img.shape == (360, 640, 3)
    rgb = img.reshape(-1, 3).astype(int)
    coloured = (np.abs(rgb[:, 0] - rgb[:, 1]) > 40) | (np.abs(rgb[:, 1] - rgb[:, 2]) > 40)
    assert coloured.mean() > 0.03          # structures are drawn in colour
    assert (rgb.sum(1) > 60).mean() > 0.3  # and the grey slice fills a good part of the frame


def test_clip_box_removes_geometry(studio):
    spec = tr.default_spec(studio.ds); spec["style"]["legend"] = False
    full = studio.ren.draw(_overview(studio, spec), spec, (640, 360)).astype(int)
    spec["clip"] = dict(x=[0, 255], y=[0, 100], z=[0, 47], fit=False)
    part = studio.ren.draw(_overview(studio, spec), spec, (640, 360)).astype(int)
    assert (part.sum(2) > 60).sum() < 0.6 * (full.sum(2) > 60).sum()
    hits = [studio.ren.pick(x, y) for x in np.linspace(0.05, 0.95, 19) for y in np.linspace(0.1, 0.9, 9)]
    ys = [h[1][1] for h in hits if h]
    assert ys and max(ys) <= 101.5         # nothing can be picked in the part that was clipped away


def test_short_movie_is_written(studio, tmp_path):
    spec = tr.default_spec(studio.ds)
    for s in spec["shots"]:
        s["seconds"] = 0.3
    spec["output"].update(size=[320, 180], fps=10, aa=False)
    out = str(tmp_path / "test.mp4")
    n, secs = tr.render_movie(studio.ren, spec, out)
    assert n > 5 and os.path.getsize(out) > 2000
    assert os.path.exists(str(tmp_path / "test_poster.png"))


def test_window_opens_and_loads_demo(demo):
    QtCore = pytest.importorskip("PySide6.QtCore")
    import argparse
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=os.path.dirname(demo["tomogram"]), headless=True))
    loop = QtCore.QEventLoop()
    win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(40):
        app.processEvents()
    if not win.st.can_show or win.st.ren is None:
        win.close(); pytest.skip("3D rendering is not available here")
    assert win.tabs.isTabVisible(win.TAB_STRUCT) and win.clip_s["z"].mx == 47
    win._clip_changed("z", 10, 30)
    assert win.spec["clip"]["z"] == [10, 30]
    win.reset_clip()
    assert win.spec["clip"] is None
    win.close()


def test_structure_list_follows_each_new_segmentation(demo, tmp_path):
    """F1 regression: the Structures list follows what is switched on, every time it is rebuilt (3.1.0 kept the first list)."""
    QtCore = pytest.importorskip("PySide6.QtCore")
    import argparse
    import json
    from PySide6 import QtWidgets
    from tomoreveal.gui import build_gui
    from tomoreveal.volumes import load_volume, write_mrc
    lab, vox = load_volume(demo["labels"])
    other = str(tmp_path / "other_labels.mrc")
    write_mrc(other, ((lab == 1) * 1 + (lab == 3) * 2).astype("int8"), vox)
    json.dump({"1": {"name": "alpha"}, "2": {"name": "beta"}}, open(str(tmp_path / "other_labels.json"), "w"))
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    errors = []
    old_hook = __import__("sys").excepthook
    __import__("sys").excepthook = lambda t, v, tb: errors.append(v)

    def wait(start):
        loop = QtCore.QEventLoop()
        start(); win.worker.finished.connect(loop.quit); loop.exec()
        for _ in range(40):
            app.processEvents()
        return [w.text() for w in (win.struct_grid.itemAt(i).widget() for i in range(win.struct_grid.count()))
                if isinstance(w, QtWidgets.QLineEdit)]

    first_key, other_key = "labels:" + win.st._norm(demo["labels"]), "labels:" + win.st._norm(other)
    try:
        first = wait(lambda: win.load(paths=[demo["tomogram"], demo["labels"]]))
        if not win.st.can_show or win.st.ren is None:
            pytest.skip("3D rendering is not available here")
        assert first == ["membrane", "vesicles", "filaments", "particles"]
        assert wait(lambda: win.load(paths=[other])) == first + ["alpha", "beta"]  # added to what is loaded
        for _ in range(2):  # every rebuild of a list is tested at least twice
            assert wait(lambda: win.set_segmentation(first_key, False)) == ["alpha", "beta"]
            assert wait(lambda: win.set_segmentation(first_key, True)) == first + ["alpha", "beta"]
            assert wait(lambda: win.set_segmentation(other_key, False)) == first
            assert wait(lambda: win.set_segmentation(other_key, True)) == first + ["alpha", "beta"]
        assert not errors, errors
    finally:
        __import__("sys").excepthook = old_hook
        win.close()
