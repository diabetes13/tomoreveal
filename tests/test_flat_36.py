"""3.6 phase 9 (R21): flat membrane surfaces with an even thickness, gaps filled."""
import numpy as np
import pytest
from scipy import ndimage

from tomoreveal import flat_membrane as fm
from tomoreveal.volumes import FLAT, surface


def curved_sheet(thick=3, noise=1.0, holes=True, seed=0, shape=(40, 120, 120)):
    """A gently curved sheet `thick` voxels thick, its middle shaken by about +-1 voxel, with three 4-voxel holes."""
    rng = np.random.default_rng(seed); nz, ny, nx = shape
    z, y, x = np.mgrid[0:nz, 0:ny, 0:nx]
    mid = 20 + 6 * np.sin(x / 25.0) + 4 * np.cos(y / 30.0)
    bump = ndimage.gaussian_filter(rng.normal(0, 1, (ny, nx)), 2); bump = bump / np.abs(bump).max() * noise
    m = (np.abs(z - (mid + bump[None])) < thick / 2) & (y > 8) & (y < 112) & (x > 8) & (x < 112)
    if holes:
        for cy, cx in ((40, 40), (60, 80), (85, 50)):
            m[:, cy - 2:cy + 2, cx - 2:cx + 2] = False
    return m


def normal_error(pd):
    """Median angle (degrees) between the surface's normals and the true sheet's, away from its rim."""
    P = np.asarray(pd.points); N = np.asarray(pd.point_normals); x, y = P[:, 0], P[:, 1]
    T = np.stack([-6 * np.cos(x / 25.0) / 25.0, 4 * np.sin(y / 30.0) / 30.0, np.ones_like(x)], 1)
    T /= np.linalg.norm(T, axis=1, keepdims=True)
    inner = (x > 14) & (x < 106) & (y > 14) & (y < 106)
    return float(np.median(np.degrees(np.arccos(np.clip(np.abs((N * T).sum(1)), 0, 1)))[inner]))


def regions(pd):
    return len(np.unique(pd.connectivity("all").cell_data["RegionId"]))


def euler(pd):
    f = pd.faces.reshape(-1, 4)[:, 1:]
    e = np.unique(np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1), axis=0)
    return pd.n_points - len(e) + len(f)


def test_one_flat_sheet_with_the_holes_filled_and_its_thickness():
    m = curved_sheet(); info = {}
    pd = fm.flat_surface(m, (0, 0, 0), m.shape, dict(gaps=3), info=info)
    assert regions(pd) == 1 and euler(pd) == 2  # one closed sheet: no holes left (each would take 2 off)
    assert abs(info["measured"] - 3.0) <= 0.3 and info["thickness"] == info["measured"]
    assert normal_error(pd) < 0.6 * normal_error(surface(m, (0, 0, 0), 20, 1, 3, m.shape))  # flatter than "much"


def test_the_sign_of_n_does_not_matter():
    m = curved_sheet(holes=False)
    flip = np.random.default_rng(3).random(m.shape) < 0.5  # every second voxel's n turned round, on purpose
    a = fm.flat_surface(m, (0, 0, 0), m.shape, dict(gaps=3)); b = fm.flat_surface(m, (0, 0, 0), m.shape, dict(gaps=3), flip=flip)
    assert a.n_points == b.n_points and np.allclose(a.points, b.points, atol=1e-4)


def test_two_sheets_stay_two_and_a_broken_one_is_joined():
    z, y, x = np.mgrid[0:50, 0:80, 0:80]
    inside = (y > 5) & (y < 75) & (x > 5) & (x < 75)
    two = ((np.abs(z - 20) < 1.5) | (np.abs(z - 28) < 1.5)) & inside  # 8 voxels apart
    assert regions(fm.flat_surface(two, (0, 0, 0), two.shape, dict(gaps=3))) == 2
    broken = (np.abs(z - 20) < 1.5) & inside; broken[:, :, 38:41] = False  # a 3-voxel break
    assert regions(fm.flat_surface(broken, (0, 0, 0), broken.shape, dict(gaps=1))) == 2
    assert regions(fm.flat_surface(broken, (0, 0, 0), broken.shape, dict(gaps=3))) == 1  # "Fill gaps: medium"


def test_typed_thickness_and_settings():
    m = curved_sheet(holes=False, noise=0); info = {}
    thin = fm.flat_surface(m, (0, 0, 0), m.shape, dict(thickness=2.0), info=info)
    thick = fm.flat_surface(m, (0, 0, 0), m.shape, dict(thickness=5.0))
    assert info["thickness"] == 2.0 and abs(info["measured"] - 3.0) <= 0.3
    zr = lambda pd: np.ptp(np.asarray(pd.points)[np.hypot(np.asarray(pd.points)[:, 0] - 60, np.asarray(pd.points)[:, 1] - 60) < 1.0, 2])
    assert abs(zr(thick) - zr(thin) - 3.0) < 0.8
    assert fm.key(None) == (3, 3.0, None) and fm.key({"gaps": 6, "thickness": 0}) == (6, 3.0, None)


def test_the_demo_membrane_as_a_flat_sheet_is_kept(studio, tmp_path, monkeypatch):
    from tomoreveal import surface_cache
    ds = studio.ds; k = ds.id_of(ds.key_of(1))
    monkeypatch.setattr(surface_cache, "folder", lambda: str(tmp_path / "surfaces"))
    want = dict(soften=FLAT, flat=(3, 3.0, None))
    m = ds.make_surface(k, params=want)
    assert m is not None and m.n_cells > 1000 and 1.5 < float(m.field_data["measured"][0]) < 5
    assert ds.make_surface(k, params=want) is m  # kept in memory
    ds._mesh_cache.pop(ds._cache_key(k, params=want))  # (only this one: the session's other surfaces stay)
    again = ds.make_surface(k, params=want)  # from the disk cache, with its thickness
    assert again.n_cells == m.n_cells and float(again.field_data["thickness"][0]) == pytest.approx(float(m.field_data["thickness"][0]))
    assert ds._cache_key(k, params=want) != ds._cache_key(k, params=dict(want, flat=(6, 3.0, None)))


def test_choose_a_flat_sheet_in_the_window(tmp_path, demo, needs_3d):
    import argparse
    import sys
    from PySide6 import QtCore, QtWidgets
    from tomoreveal.gui import build_gui
    from tomoreveal.volumes import SURFACES
    from test_structure_settings import surfaces_made
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    errs = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errs.append(v)
    try:
        loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
        for _ in range(30):
            app.processEvents()
        c = next(c for c in win.info["structures"] if c["name"] == "membrane"); key = c["key"]
        box = win._struct_details(c, win.spec["classes"][key])
        so = win._soften_cb[key]; i = next(j for j, (_, v) in enumerate(SURFACES) if v == FLAT)
        flat_box = win._flat_box[key]
        assert flat_box.isHidden()
        so.setCurrentIndex(i); so.activated.emit(i)
        assert not flat_box.isHidden() and win.spec["classes"][key]["soften"] == FLAT
        surfaces_made(app, win)
        m = win.st.ds.meshes[win.st.ds.id_of(key)]
        assert "measured" in m.field_data and win.st.ds.param(c["id"], "soften") == FLAT
        from tomoreveal.gui_flat import measured
        assert measured(win, key)[1] == pytest.approx(float(m.field_data["measured"][0]))
        win.close()
        assert not errs, errs
    finally:
        sys.excepthook = old
