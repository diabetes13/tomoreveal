"""3.6 phase 2: surfaces kept on disk, label counts without big copies, the tomogram in its own data type."""
import os

import pytest
import numpy as np

import tomoreveal as tr
from tomoreveal import surface_cache


def test_surfaces_come_from_disk_the_second_time(demo):
    a = tr.Studio(headless=True); a.add_files([demo["tomogram"], demo["labels"]], log=lambda *x: None, make_renderer=False)
    b = tr.Studio(headless=True); b.add_files([demo["tomogram"], demo["labels"]], log=lambda *x: None, make_renderer=False)
    assert getattr(b.ds, "built", 0) == 0 and b.ds.disk_hits >= len(b.ds.meshes) > 0
    for k, m in a.ds.meshes.items():
        n = b.ds.meshes[k]
        assert m.n_points == n.n_points and np.allclose(m.points, n.points)
        assert np.allclose(m.point_data["Normals"], n.point_data["Normals"]) and np.array_equal(m.faces, n.faces)


def test_other_settings_or_voxels_never_meet_an_old_surface():
    m = np.zeros((6, 8, 8), bool); m[2:4, 2:6, 2:6] = True
    base = surface_cache.fingerprint(m, (0, 0, 0), (6, 8, 8), (15, 1, 0))
    m2 = m.copy(); m2[3, 3, 3] = False
    others = [surface_cache.fingerprint(m2, (0, 0, 0), (6, 8, 8), (15, 1, 0)),       # one voxel differs
              surface_cache.fingerprint(m, (1, 0, 0), (6, 8, 8), (15, 1, 0)),        # elsewhere in the grid
              surface_cache.fingerprint(m, (0, 0, 0), (7, 8, 8), (15, 1, 0)),        # another grid
              surface_cache.fingerprint(m, (0, 0, 0), (6, 8, 8), (15, 1, 2))]        # smoother
    assert len({base, *others}) == 5


def test_the_cache_stays_under_its_limit(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(surface_cache, "folder", lambda: str(tmp_path))
    from tomoreveal.volumes import surface
    m = np.zeros((10, 12, 12), bool); m[3:7, 3:9, 3:9] = True
    mesh = surface(m, (0, 0, 0), 5, 1)
    surface_cache.save("a" * 40, mesh); size = os.path.getsize(tmp_path / ("a" * 40 + ".npz"))
    monkeypatch.setattr(surface_cache, "LIMIT", int(size * 2.5))
    for c in "bcd":
        surface_cache.save(c * 40, mesh)
    left = sorted(p.name[0] for p in tmp_path.glob("*.npz"))
    assert left == ["c", "d"]  # the oldest went first
    assert surface_cache.load("d" * 40).n_points == mesh.n_points and surface_cache.load("a" * 40) is None


def test_label_counts_without_big_copies():
    from tomoreveal.volumes import Dataset
    rng = np.random.default_rng(3)
    for dt in (np.uint8, np.int8, np.int16, np.uint16, np.int32):
        lab = rng.integers(0, 6, (20, 30, 40)).astype(dt)
        counts, boxes = Dataset.label_stats(lab)
        assert np.array_equal(counts, np.bincount(lab.ravel().astype(np.int64), minlength=6))
        from scipy import ndimage
        assert boxes == ndimage.find_objects(lab.astype(np.int32), max_label=5)


def test_an_8_bit_tomogram_stays_8_bit(tmp_path, needs_3d):
    """An 8-bit tomogram was turned into float32 (4 x its size); now only the slice drawn is converted."""
    import mrcfile
    rng = np.random.default_rng(1)
    t = rng.integers(-120, 120, (24, 96, 128)).astype(np.int8)  # MRC mode 0
    p = str(tmp_path / "t8.mrc")
    with mrcfile.new(p) as m:
        m.set_data(t); m.voxel_size = 10.0
    st = tr.Studio(headless=True); st.add_files([p], log=lambda *x: None)
    assert st.ds.tomo.dtype == np.int8 and st.ds.tomo.nbytes < 1.2 * t.nbytes
    spec = tr.default_spec(st.ds); f = tr.build_timeline(spec, st.ds)[0][3]
    img = st.ren.draw(f, spec, (160, 90))
    assert img.std() > 5  # the slice is drawn
    from tomoreveal.explore import grey_slice, readout
    g = grey_slice(st.ds, 5)
    assert g.dtype == np.uint8 and g.std() > 5
    assert readout(st.ds, spec, 5, 10, 20)["grey"] == float(t[5, 20, 10])


from test_tidy_panels import pump, window  # noqa: E402,F401 (fixture)


def test_a_drawing_error_retries_the_graphics_card_and_asks_before_software(window, monkeypatch):
    """It switched to software rendering for the rest of the session at the first error, with one short message."""
    app, win, errs = window
    st, ren = win.st, win.st.ren
    calls = dict(ensure=0, ask=0, fail=1)
    real_draw = ren.draw

    def flaky(*a, **k):
        if calls["fail"] > 0:
            calls["fail"] -= 1
            raise RuntimeError("test: the graphics card failed")
        return real_draw(*a, **k)
    monkeypatch.setattr(ren, "draw", flaky)
    monkeypatch.setattr(st, "headless", False)
    monkeypatch.setattr(st, "ensure_renderer", lambda: calls.__setitem__("ensure", calls["ensure"] + 1) or ren)
    monkeypatch.setattr(win, "ask_software", lambda exc: calls.__setitem__("ask", calls["ask"] + 1) or False)
    win._gpu_retried = False; win.do_preview(); pump(app)
    assert calls["ensure"] == 1 and calls["ask"] == 0 and not st.headless  # one quiet retry, still the graphics card
    calls["fail"] = 5; win._gpu_retried = False; win.do_preview(); pump(app)
    assert calls["ask"] == 1 and not st.headless  # asked, and "Not now" keeps it
    assert "Preview failed" in win.msg.text() if hasattr(win, "msg") else True


def test_performance_window_and_diagnose(window):
    from tomoreveal import diagnose
    from tomoreveal.gui_performance import PerformanceDialog
    app, win, errs = window
    assert diagnose.in_plain_words("NVIDIA GeForce RTX 4070 SUPER/PCIe/SSE2", "vtkXOpenGLRenderWindow") == \
        "your NVIDIA GeForce RTX 4070 SUPER (fast)"
    assert "software" in diagnose.in_plain_words("llvmpipe (LLVM 17)", "") and "software" in diagnose.in_plain_words("x", "vtkOSOpenGLRenderWindow")
    d = PerformanceDialog(win); d.measure(); pump(app)
    assert "Drawing with:" in d.text.text() and "Memory:" in d.text.text() and "frame:" in d.bars.text()
    assert "Drawing with:" in diagnose.report(win.st)
    d.close(); assert not errs


def test_loading_reports_its_stages(demo, monkeypatch):
    from tomoreveal.studio import Studio
    msgs = []
    st = Studio(headless=True); st.add_files([demo["tomogram"], demo["labels"]], log=msgs.append, make_renderer=False)
    assert any(m.startswith("reading demo_tomogram") for m in msgs) and any(m.startswith("built ") for m in msgs)
    msgs.clear(); monkeypatch.setattr(Studio, "SLOW_DRIVE", 1e30)
    import os
    monkeypatch.setattr(os.path, "getsize", lambda p: 5e8)  # pretend the file is large
    import time
    real = time.monotonic; t = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: t.__setitem__(0, t[0] + 3.0) or t[0])
    st2 = Studio(headless=True); st2.add_files([demo["tomogram"]], log=msgs.append, make_renderer=False)
    monkeypatch.setattr(time, "monotonic", real)
    assert any("slow drive" in m for m in msgs), msgs


def test_open_smaller_bins_the_volume_and_keeps_the_settings_in_place(demo):
    from tomoreveal import storyboard as sb
    from tomoreveal.volumes import bin_volume
    a = np.arange(4 * 6 * 8, dtype=np.int16).reshape(4, 6, 8)
    m = bin_volume(a, 2, average=True)
    assert m.shape == (2, 3, 4) and m.dtype == np.int16 and m[0, 0, 0] == int(round(a[:2, :2, :2].mean()))
    assert np.array_equal(bin_volume(a, 2), a[::2, ::2, ::2])
    st = tr.Studio(headless=True); st.add_files([demo["tomogram"], demo["labels"]], log=lambda *x: None, make_renderer=False)
    full_shape, full_vox = st.ds.shape, st.ds.vox
    n_full = {c["name"]: c["voxels"] for c in st.ds.classes.values()}
    st.set_bin(2, log=lambda *x: None, make_renderer=False)
    assert st.ds.shape == tuple(n // 2 for n in full_shape) and abs(st.ds.vox - 2 * full_vox) < 1e-6
    n_half = {c["name"]: c["voxels"] for c in st.ds.classes.values()}
    assert set(n_half) == set(n_full) and st.ds.meshes
    for k in n_full:
        assert 0.06 < n_half[k] / n_full[k] < 0.2  # about 1/8 of the voxels
    st.set_bin(1, log=lambda *x: None, make_renderer=False)
    assert st.ds.shape == full_shape and {c["name"]: c["voxels"] for c in st.ds.classes.values()} == n_full
    spec = dict(annotations=[{"anchor": {"xyz": [100.0, 40.0, 20.0]}}], clip=dict(x=[10, 200], y=[0, 99], z=[0, 47]),
                shots=[{"type": "slide", "from": 40, "to": "last"}], classes={"k": {"min_object_voxels": 80}}, z_range=[4, 44])
    sb.bin_spec(spec, 0.5, (24, 160, 128))
    assert spec["annotations"][0]["anchor"]["xyz"] == [50.0, 20.0, 10.0] and spec["clip"]["x"] == [5, 100]
    assert spec["shots"][0]["from"] == 20 and spec["z_range"] == [2, 22] and spec["classes"]["k"]["min_object_voxels"] == 10


def test_open_smaller_in_the_window_and_in_the_project(window):
    from tomoreveal import storyboard as sb
    from tomoreveal.project import load_into
    app, win, errs = window
    ds = win.st.ds; key = next(c["key"] for c in ds.classes.values() if c["name"] == "vesicles")
    p = sb.new_part("focus", target={"class": key, "component": 1}); win.editor.insert(p, "start"); pump(app)
    before = np.array([s.start + s.stop for s in ds._comp_slices(ds.id_of(key), 1)], float) / 2
    from PySide6 import QtCore
    loop = QtCore.QEventLoop(); win.bin_volume(2); win.worker.finished.connect(loop.quit); loop.exec(); pump(app)
    assert win.st.bin == 2 and win.spec["bin"] == 2
    ds = win.st.ds; q = win.parts_panel.part(p["id"]); cid = q["target"].get("component")
    after = np.array([s.start + s.stop for s in ds._comp_slices(ds.id_of(key), int(cid))], float) / 2
    assert np.allclose(after, before / 2, atol=2)  # the same vesicle, at half size
    data = win.project.data()
    assert data["spec"]["bin"] == 2
    st2 = tr.Studio(headless=True)
    from tomoreveal.project import resolve_sources
    load_into(st2, data, resolve_sources(data, None), log=lambda *x: None)
    assert st2.bin == 2 and st2.ds.shape == ds.shape
    assert not errs


def test_half_detail_surfaces(demo):
    st = tr.Studio(headless=True); st.add_files([demo["tomogram"], demo["labels"]], log=lambda *x: None, make_renderer=False)
    ds = st.ds; k = next(k for k, c in ds.classes.items() if c["name"] == "membrane")
    full = ds.meshes[k]
    assert ds.set_params(k, detail=2)
    half = ds.meshes[k]
    assert half is not full and half.n_cells < 0.4 * full.n_cells
    assert ds.set_params(k, detail=None) and ds.meshes[k] is full  # back to the full surface, from the cache


def test_own_folder_without_the_window(monkeypatch):
    """Bug found in 3.6 dev: without the window (command line, scripts) the application names were not set, so the
    folder was ~/.local/share itself and cached surfaces went to ~/.local/share/surfaces."""
    QtCore = pytest.importorskip("PySide6.QtCore")
    from tomoreveal.project import data_dir
    A = QtCore.QCoreApplication; old = (A.organizationName(), A.applicationName())
    monkeypatch.delenv("TOMOREVEAL_DATA_DIR", raising=False)
    try:
        A.setOrganizationName(""); A.setApplicationName("")
        assert os.path.basename(os.path.normpath(data_dir())) == "TomoReveal"  # nothing is written: only the path
    finally:
        A.setOrganizationName(old[0]); A.setApplicationName(old[1])
