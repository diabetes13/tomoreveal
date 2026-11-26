"""F-05: files made from your own tomogram file get the same flip / binning as that file when it is lined up with Dragonfly's copy."""
import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal.volumes import load_volume, write_mrc

from fake_session import write_session


def quiet(*a):
    pass


def dice(a, b):
    a, b = a > 0, b > 0
    return 2 * (a & b).sum() / max(1, a.sum() + b.sum())


@pytest.fixture(scope="module")
def setup(demo, tmp_path_factory):
    """A Dragonfly session (image + segmentation on its grid), and your tomogram file, which is that image flipped in Y."""
    d = tmp_path_factory.mktemp("flip")
    lab, vox = load_volume(demo["labels"]); tomo, _ = load_volume(demo["tomogram"])
    ses = write_session(str(d / "s.ORSSession"), dims=lab.shape[::-1], labels=lab, image=tomo.astype(np.float32),
                        names=("membrane", "vesicles", "filaments", "particles"))
    mine = str(d / "mine.mrc"); write_mrc(mine, np.ascontiguousarray(tomo[:, ::-1, :]), vox)
    return dict(d=d, lab=lab, tomo=tomo, vox=vox, ses=ses, mine=mine)


def placed(st, path):
    return next(s for s in st.seg_sources if s.get("path") == st._norm(path))["arr"]


def test_score_file_made_from_your_flipped_tomogram_is_flipped_too(setup):
    sc = str(setup["d"] / "mine__vesicles.mrc")
    write_mrc(sc, np.ascontiguousarray((setup["lab"] == 2)[:, ::-1, :]).astype(np.float32), setup["vox"])
    st = tr.Studio(headless=True)
    st.add_files([setup["ses"], setup["mine"], sc], log=quiet, make_renderer=False)
    assert "flipped" in st.match_note
    assert dice(placed(st, sc) >= 0.5, setup["lab"] == 2) > 0.99


def test_label_file_on_your_binned_tomogram_grid_is_lined_up(setup):
    up = lambda a: np.repeat(np.repeat(np.repeat(a, 2, 0), 2, 1), 2, 2)  # your file is finer than Dragonfly's copy
    fine = str(setup["d"] / "fine.mrc"); write_mrc(fine, np.ascontiguousarray(up(setup["tomo"][:, ::-1, :])), setup["vox"] / 2)
    labf = str(setup["d"] / "fine_labels.mrc"); write_mrc(labf, np.ascontiguousarray(up(setup["lab"][:, ::-1, :])).astype(np.int8), setup["vox"] / 2)
    st = tr.Studio(headless=True)
    st.add_files([setup["ses"], fine, labf], log=quiet, make_renderer=False)
    arr = placed(st, labf)
    assert arr.shape == setup["lab"].shape and np.issubdtype(arr.dtype, np.integer)
    assert dice(arr == 2, setup["lab"] == 2) > 0.95


def test_label_file_on_dragonfly_grid_is_used_as_it_is_with_a_note(setup):
    labf = str(setup["d"] / "export_labels.mrc"); write_mrc(labf, setup["lab"].astype(np.int8), setup["vox"])  # e.g. a Dragonfly TIFF export
    st = tr.Studio(headless=True)
    st.add_files([setup["ses"], setup["mine"], labf], log=quiet, make_renderer=False)
    assert np.array_equal(placed(st, labf), setup["lab"])
    assert "export_labels.mrc: used as it is" in st.match_note
    st.set_segmentation_enabled(next(x["key"] for x in st.seg_sources if x["kind"] == "dragonfly"), False, log=quiet, make_renderer=False)
    assert "export_labels.mrc: used as it is" in st.match_note  # still there after another update


# ---- the tomogram shown is always the one that is loaded (G-32, N-04) ----
def same(a, b):
    return a is not None and a.shape == b.shape and np.corrcoef(a.ravel(), b.ravel())[0, 1] > 0.999


def test_a_tomogram_file_written_anew_is_read_anew(setup, tmp_path):
    p = str(tmp_path / "t.mrc"); st = tr.Studio(headless=True); a = setup["tomo"].astype(np.float32); b = np.ascontiguousarray(a[::-1])
    write_mrc(p, a, 10.0); st.add_files([p], as_tomogram=[p], log=quiet, make_renderer=False)
    assert same(st.ds.tomo, a)
    write_mrc(p, b, 10.0); st.add_files([p], as_tomogram=[p], log=quiet, make_renderer=False)  # without removing it first
    assert same(st.ds.tomo, b)
    before = st.ds.tomo; lab = str(tmp_path / "lab.mrc"); write_mrc(lab, setup["lab"].astype(np.int8), 10.0)
    st.add_files([lab], as_labels=[lab], log=quiet, make_renderer=False)
    assert st.ds.tomo is before  # nothing is put in again without need
    st.remove_source("file:" + st._norm(p), log=quiet, make_renderer=False)
    assert st.ds.tomo is None and not st.has_tomogram
    st.add_files([p], as_tomogram=[p], log=quiet, make_renderer=False); assert same(st.ds.tomo, b)


def test_a_file_written_anew_is_lined_up_again(setup, tmp_path):
    p = str(tmp_path / "t.mrc"); st = tr.Studio(headless=True); tomo = setup["tomo"].astype(np.float32)
    write_mrc(p, np.ascontiguousarray(tomo[:, ::-1]), setup["vox"])
    st.add_files([setup["ses"], p], as_tomogram=[p], log=quiet, make_renderer=False)
    assert "flipped in Y" in st.match_note and same(st.ds.tomo, tomo)
    st.remove_source("file:" + st._norm(p), log=quiet, make_renderer=False)
    write_mrc(p, tomo * 2 + 5, setup["vox"])  # not flipped this time
    st.add_files([p], as_tomogram=[p], log=quiet, make_renderer=False)
    assert "same grid" in st.match_note and abs(float(st.ds.tomo.mean()) - float(tomo.mean() * 2 + 5)) < 0.01


def test_a_session_added_after_the_tomogram_shows_the_same_as_both_at_once(setup):
    both = tr.Studio(headless=True); both.add_files([setup["mine"], setup["ses"]], log=quiet, make_renderer=False)
    st = tr.Studio(headless=True)
    st.add_files([setup["mine"]], as_tomogram=[setup["mine"]], log=quiet, make_renderer=False); raw = st.ds.tomo.copy()
    st.add_files([setup["ses"]], log=quiet, make_renderer=False)
    assert st.match_note == both.match_note and "flipped in Y" in st.match_note
    assert np.array_equal(st.ds.tomo, both.ds.tomo) and same(st.ds.tomo, setup["tomo"]) and st.ds.vox == both.ds.vox
    st.remove_source("session:" + st._norm(setup["ses"]), log=quiet, make_renderer=False)  # the file as it is again
    assert np.array_equal(st.ds.tomo, raw)


def test_flipping_keeps_the_tomogram_and_the_segmentation_together(setup):
    st = tr.Studio(headless=True); st.add_files([setup["mine"]], as_tomogram=[setup["mine"]], log=quiet, make_renderer=False)
    st.set_flip(("x",), log=quiet, make_renderer=False)
    st.add_files([setup["ses"]], log=quiet, make_renderer=False)
    assert same(st.ds.tomo, setup["tomo"][:, :, ::-1])
    st.set_flip((), log=quiet, make_renderer=False); assert same(st.ds.tomo, setup["tomo"])
