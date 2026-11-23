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
