"""Phase 5: the Explore tab (2D slice with the segmentation on it, 3D at a slice, keys, hand-over to the movie)."""
import argparse
import sys
import time

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal.common import hex2rgb
from tomoreveal.explore import grey_slice, readout, slice_image
from tomoreveal.volumes import Dataset


@pytest.fixture(scope="module")
def both(demo):
    st = tr.Studio(headless=True)
    st.add_files([demo["tomogram"], demo["labels"]], log=lambda *a: None, make_renderer=False)
    return st


def test_2d_slice_image(both):
    ds = both.ds; spec = tr.default_spec(ds)
    vkey = ds.key_of(2); col = np.array(hex2rgb(spec["classes"][vkey]["color"])) * 255
    for k in (0, 20, 47):
        off = slice_image(ds, spec, k, "off")
        assert off.shape == (320, 256, 3)
        assert np.array_equal(off[..., 0], grey_slice(ds, k)) and np.array_equal(off[..., 0], off[..., 2])
    lab = ds._labels[2][0]
    k = int(np.argmax((lab == 2).sum((1, 2))))  # the slice with most vesicle voxels
    m = lab[k] == 2
    full = slice_image(ds, spec, k, "filled", alpha=1.0).astype(float)
    assert np.allclose(full[m], col, atol=1)
    part = slice_image(ds, spec, k, "filled", alpha=0.4).astype(float)
    hue = part[m] - part[m].mean(1, keepdims=True)  # the structure's hue shows through
    assert np.corrcoef(hue.mean(0), col - col.mean())[0, 1] > 0.9
    out = slice_image(ds, spec, k, "outlines")
    drawn = (np.abs(out.astype(int) - slice_image(ds, spec, k, "off")).sum(2) > 0)
    assert 0 < drawn.sum() < (lab[k] > 0).sum() and not drawn[lab[k] == 0].any()  # outlines only, inside the structures
    y, x = np.argwhere(m)[0]
    r = readout(ds, spec, k, x, y)
    assert r["name"] == "vesicles" and r["key"] == vkey and r["grey"] is not None
    spec["classes"][vkey]["show"] = False  # hidden in the Structures tab: not drawn, not read out
    assert readout(ds, spec, k, x, y)["name"] != "vesicles"
    assert np.array_equal(slice_image(ds, spec, k, "filled", alpha=1.0)[m], slice_image(ds, spec, k, "off")[m])


def test_segmentation_only_draws_on_black(demo):
    st = tr.Studio(headless=True)
    st.add_files([demo["labels"]], log=lambda *a: None, make_renderer=False)
    img = slice_image(st.ds, tr.default_spec(st.ds), 20, "filled", alpha=1.0)
    lab = st.ds._labels[1][0][20]
    assert (img[lab == 0] == 0).all() and (img[lab > 0].sum(1) > 0).all()


def test_large_slice_is_fast():
    rng = np.random.default_rng(0); nz, ny, nx = 4, 1166, 708
    lab = rng.integers(0, 11, (nz, ny // 8 + 1, nx // 4 + 1)).repeat(8, 1).repeat(4, 2)[:, :ny, :nx].astype(np.int8)
    ds = Dataset(); ds.set_tomogram(rng.normal(size=(nz, ny, nx)).astype(np.float32), 10, "t")
    ds.set_segmentation([dict(kind="labels", key="l", title="l", arr=lab, classes={})])
    spec = tr.default_spec(ds); slice_image(ds, spec, 0)
    t = time.time(); slice_image(ds, spec, 1, "filled"); slice_image(ds, spec, 2, "outlines")
    assert (time.time() - t) / 2 < 0.2


@pytest.fixture
def window(tmp_path, demo, needs_3d):
    from PySide6 import QtCore
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    errors = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errors.append(v)
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(40):
        app.processEvents()
    assert win.st.ren is not None, "the 3D renderer did not start after loading"
    win.tabs.setCurrentIndex(win.TAB_EXPLORE)
    for _ in range(20):
        app.processEvents()
    yield app, win, errors
    sys.excepthook = old; win.close()


def press(app, widget, key, n=1):
    from PySide6 import QtCore, QtGui
    for _ in range(n):
        app.sendEvent(widget, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, QtCore.Qt.NoModifier))


def test_keys_and_hand_over_to_the_movie(window):
    from PySide6 import QtCore
    app, win, errors = window
    ex = win.explore
    assert win.right.currentIndex() == 1 and ex.k == 0 and not ex.view.pix.pixmap().isNull()
    assert ex.view.pix.pixmap().width() == 256 and ex.view.pix.pixmap().height() == 320
    press(app, ex.view, QtCore.Qt.Key_PageDown, 10)
    assert ex.k == 47  # clamped at the last slice
    press(app, ex.view, QtCore.Qt.Key_Home)
    assert ex.k == 0
    press(app, ex.view, QtCore.Qt.Key_Down, 3); press(app, ex.view, QtCore.Qt.Key_Up)
    assert ex.k == 2 and ex.spin.value() == 3
    before = dict(win.spec)
    ex.go(10); ex.start_btn.click(); ex.go(30); ex.end_btn.click()
    assert win.spec["z_range"] == [10, 30] and {k for k in before if before[k] is not win.spec[k]} <= {"z_range"}
    frames, _ = tr.build_timeline(win.spec_for(), win.st.ds)
    assert {f["k"] for f in frames if f["shot"] == 0} == set(range(10, 31))
    win.tabs.setCurrentIndex(win.TAB_MOVIE)
    assert win.right.currentIndex() == 0
    assert not errors, errors



def test_overlay_follows_a_threshold_change(tmp_path, demo, needs_3d):
    """F-25: after a score threshold changes, the 2D overlay is drawn again, not taken from the cache."""
    from PySide6 import QtCore
    from tomoreveal.gui import build_gui
    from tomoreveal.volumes import load_volume, write_mrc
    tomo, vox = load_volume(demo["tomogram"]); z, y, x = np.indices(tomo.shape)
    blob = np.exp(-(((z - 24) / 12.0) ** 2 + ((y - 160) / 60.0) ** 2 + ((x - 128) / 50.0) ** 2)).astype("float32")
    feat = str(tmp_path / "demo_tomogram__blob.mrc"); write_mrc(feat, blob, vox)
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], feat]); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(40):
        app.processEvents()
    ex = win.explore; ex.go(24)
    key = [c["key"] for c in win.info["structures"] if c["kind"] == "score"][0]
    first = ex.image_2d(win.spec_for()).copy()
    win._thr_changed(key, 0.9); win._thr_settled()
    after = ex.image_2d(win.spec_for())
    assert not np.array_equal(first, after)
    assert np.array_equal(after, slice_image(win.st.ds, win.spec_for(), 24, ex.overlay_cb.currentData(), ex.alpha_s.value() / 100))
    win.close()


def test_3d_at_this_slice(window):
    app, win, errors = window
    ex = win.explore; ex.mode_3d.setChecked(True); ex.go(24)
    spec = win.spec_for(); spec["style"].update(legend=False, slice_label=False)
    coloured = {}
    for i, (_, cut) in enumerate((("", "above"), ("", "all"), ("", "none"))):
        ex.cut_cb.setCurrentIndex(i)
        img = win.st.ren.draw(ex.state_3d(spec), spec, (480, 270)).reshape(-1, 3).astype(int)
        coloured[cut] = ((np.abs(img[:, 0] - img[:, 1]) > 40) | (np.abs(img[:, 1] - img[:, 2]) > 40)).mean()
    assert coloured["all"] > coloured["above"] > coloured["none"] and coloured["none"] < 0.002
    assert not ex.view.coords and not errors, errors
