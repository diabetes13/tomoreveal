"""An interrupted movie is continued only when its settings, its data and the program are the same (G-08)."""
import argparse
import copy
import os
import shutil
import time

import pytest

import tomoreveal as tr
from tomoreveal import encode
from test_movie_output import fails_from, make, movie_file, run_for, window  # noqa: F401 (fixture)


def copy_of_demo(demo, folder):
    shutil.copytree(os.path.dirname(demo["tomogram"]), folder)
    return {k: os.path.join(folder, os.path.basename(demo[k])) for k in ("tomogram", "labels")}


def change_voxels(path, change):
    """Write the file again with `change` applied to its voxels: the same name, grid and structures."""
    import mrcfile
    with mrcfile.open(path) as m:
        arr = m.data.copy()
    change(arr); tr.write_mrc(path, arr, 10.0)


def loaded(paths):
    st = tr.Studio(headless=True); st.load([paths["tomogram"], paths["labels"]], log=lambda *a: None)
    return st


def test_the_same_data_loaded_twice_gives_the_same_hash(demo, tmp_path, needs_3d):
    paths = copy_of_demo(demo, str(tmp_path / "a")); a, b = loaded(paths), loaded(paths)
    spec = tr.default_spec(a.ds)
    assert encode.settings_hash(spec, a.ds) == encode.settings_hash(spec, b.ds) == encode.settings_hash(copy.deepcopy(spec), a.ds)


@pytest.mark.parametrize("which, change", [
    ("tomogram", lambda a: a.__setitem__((slice(None), slice(None), slice(0, 6)), a[:, :, :6] * 0.5)),
    ("labels", lambda a: a.__setitem__((slice(0, 3), slice(0, 3), slice(0, 3)), 2)),
])
def test_other_voxels_give_another_hash(demo, tmp_path, needs_3d, which, change):
    """The same files, grid and structures: before, only those were compared."""
    paths = copy_of_demo(demo, str(tmp_path / "a")); a = loaded(paths); spec = tr.default_spec(a.ds)
    h = encode.settings_hash(spec, a.ds)
    change_voxels(paths[which], change); b = loaded(paths)
    assert a.ds.tomo_name == b.ds.tomo_name and a.ds.shape == b.ds.shape
    assert sorted(c["key"] for c in a.ds.classes.values()) == sorted(c["key"] for c in b.ds.classes.values())
    assert encode.settings_hash(spec, b.ds) != h


def test_a_flip_a_threshold_or_another_program_give_another_hash(demo, tmp_path, needs_3d, monkeypatch):
    st = loaded(copy_of_demo(demo, str(tmp_path / "a"))); spec = tr.default_spec(st.ds); h = encode.settings_hash(spec, st.ds)
    st.set_flip(("x",), log=lambda *a: None, make_renderer=False)
    assert encode.settings_hash(spec, st.ds) != h
    st.set_flip((), log=lambda *a: None, make_renderer=False)
    assert encode.settings_hash(spec, st.ds) == h  # (the voxels of now, not the way there)
    other = copy.deepcopy(spec); other.setdefault("classes", {})[st.ds.key_of(1)] = dict(threshold=0.3)
    assert encode.settings_hash(other, st.ds) != h
    monkeypatch.setattr(encode, "PICTURE_VERSION", encode.PICTURE_VERSION + 1)
    assert encode.settings_hash(spec, st.ds) != h
    monkeypatch.undo(); monkeypatch.setattr(encode, "__version__", "0.0.0")
    assert encode.settings_hash(spec, st.ds) != h


def wait_for(app, win, seconds=120):
    end = time.monotonic() + seconds
    while (win.worker is not None and win.worker.isRunning()) or (win.export.est_gen is not None):
        assert time.monotonic() < end, "it took too long"
        run_for(app, 0.02)
    run_for(app, 0.3)


def test_continue_is_offered_after_a_flip_an_estimate_a_crash_and_reopening(window, tmp_path, monkeypatch):
    from tomoreveal.gui import build_gui
    from tomoreveal.encode import count_frames, read_partial
    app, win, errs = window; out = movie_file(win); monkeypatch.setattr(encode, "CHUNK_FRAMES", 2)
    win.flip_volume(("x",)); wait_for(app, win); assert win.st.flip == ("x",)
    win.tabs.setCurrentIndex(win.TAB_MOVIE); win.export.start(); wait_for(app, win)
    heal = fails_from(monkeypatch, 5); make(app, win); heal()
    assert read_partial(out)["chunks_done"] == 2 and not os.path.exists(out)
    assert win.project.autosave_now(); auto = str(tmp_path / "crash.tomoreveal"); shutil.copy(win.project.autosave.path, auto)
    n = len(tr.storyboard.build_plan(win.spec_for(), win.st.ds).frames)
    app, again = build_gui(argparse.Namespace(files=[], outdir=os.path.dirname(out), headless=True))
    again.project.ask_save = lambda what: "discard"
    try:
        again.project.restore(auto); wait_for(app, again)
        assert again.st.flip == ("x",)
        again.name_le.setText("m"); again.folder_le.setText(os.path.dirname(out)); again.quality_cb.setCurrentIndex(0)
        asked = []; monkeypatch.setattr(again, "ask_resume", lambda part, same: (asked.append(same), True)[1])
        make(app, again)
        assert asked == [True] and count_frames(out) == n and read_partial(out) is None
    finally:
        again.render_timer.stop(); again.gen = None; again.close()
    assert not errs, errs
