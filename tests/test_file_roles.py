"""What a file is: the role you give wins (F-04a); better guesses for files without one (F-04b)."""
import argparse

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal.cli import cli_studio
from tomoreveal.volumes import load_volume, write_mrc


def quiet(*a):
    pass


def cli_args(**kw):
    a = dict(headless=True, smooth=15, mesh_step=1, keep_background=False, threshold=0.5, tomogram=None, session=None,
             labels=None, features=[], roi=None, classes=None)
    a.update(kw)
    return argparse.Namespace(**a)


def label_keys(st):
    return {c["key"].split("#")[0] for c in st.ds.classes.values() if c["kind"] == "label"}


def test_cli_labels_option_forces_a_float32_label_file_to_be_labels(tmp_path, demo):
    lab, vox = load_volume(demo["labels"])
    p = str(tmp_path / "labels_f32.mrc"); write_mrc(p, lab.astype(np.float32), vox)
    st, info = cli_studio(cli_args(tomogram=demo["tomogram"], labels=p))
    assert st.tomo_source == "file:" + st._norm(demo["tomogram"])  # the tomogram you named stays the tomogram
    assert label_keys(st) == {"labels:" + st._norm(p)} and len(info["structures"]) == 4


def test_cli_labels_option_forces_an_instance_segmentation_to_be_labels(tmp_path):
    lab = np.zeros((12, 140, 140), np.uint16)
    for i in range(400):  # 400 objects, each seen by the guess's 1-in-7 sampling: far more ids than it allows
        y, x = divmod(i, 20)
        lab[3:9, 7 * y:7 * y + 6, 7 * x:7 * x + 6] = i + 1
    p = str(tmp_path / "instances.mrc"); write_mrc(p, lab, 10.0)
    st, info = cli_studio(cli_args(labels=p))
    assert st.tomo_source is None and label_keys(st) == {"labels:" + st._norm(p)}


@pytest.mark.parametrize("make, kind", [
    (lambda lab: (lab * 1).astype(np.uint8), "labels"),          # labels 1..4 also look like a score map
    (lambda lab: (lab > 0).astype(np.uint8), "labels"),          # a 0/1 mask
    (lambda lab: (lab == 2).astype(np.float32) * 0.9, "scores"),  # an easymode score map
])
def test_add_segmentation_keeps_its_meaning(tmp_path, demo, make, kind):
    lab, vox = load_volume(demo["labels"])
    p = str(tmp_path / "seg.mrc"); write_mrc(p, make(lab), vox)
    st = tr.Studio(headless=True)
    st.add_files([demo["tomogram"]], log=quiet, make_renderer=False)
    st.add_files([p], as_segmentation=[p], log=quiet, make_renderer=False)
    assert [s["kind"] for s in st.seg_sources] == [kind]


# ---- F-04b: guesses for files given without a role ----

def _vol(rng, kind):
    shape = (32, 64, 64)
    if kind == "narrow 8-bit tomogram":  # a uint8 tomogram with only ~60 grey values
        return np.clip(rng.normal(128, 8, shape), 0, 255).astype(np.uint8)
    lab = np.zeros(shape, np.int16); lab[8:16, 10:40, 10:40] = 1; lab[20:24, 30:60, 5:20] = 2; lab[2:5, 50:60, 50:60] = 3
    if kind == "uint8 labels":
        return lab.astype(np.uint8)
    if kind == "float32 labels":
        return lab.astype(np.float32)
    if kind == "float32 labels above 32767":  # what extract writes for large Dragonfly label numbers
        return np.where(lab == 3, 70000, lab).astype(np.float32)
    if kind == "score map":
        return (lab == 1).astype(np.float32) * 0.9 + rng.random(shape).astype(np.float32) * 0.05
    return rng.normal(0, 1, shape).astype(np.float32)  # "float tomogram"


@pytest.mark.parametrize("kind, stem, have_tomo, expected", [
    ("float32 labels", "membranes", True, "labels"),                     # was taken as the tomogram
    ("float32 labels above 32767", "labels", True, "labels"),
    ("uint8 labels", "TS01__labels", True, "labels"),                     # the easymode name rule made it an empty score map
    ("narrow 8-bit tomogram", "TS01", False, "tomogram"),                 # was ~60 "classes"
    ("score map", "TS01__vesicles", True, "scores"),
    ("score map", "vesicles", True, "scores"),
    ("float tomogram", "TS01", False, "tomogram"),
    ("uint8 labels", "labels", False, "labels"),
])
def test_guess_kind(kind, stem, have_tomo, expected):
    from tomoreveal.volumes import guess_kind
    assert guess_kind(_vol(np.random.default_rng(0), kind), stem, have_tomo, 1) == expected


def test_demo_files_are_still_recognised(demo):
    from tomoreveal.volumes import guess_kind
    assert guess_kind(load_volume(demo["tomogram"])[0], "demo_tomogram", False, 2) == "tomogram"
    assert guess_kind(load_volume(demo["labels"])[0], "demo_labels", False, 2) == "labels"


def test_rgb_tiff_is_refused(tmp_path):
    import tifffile
    from tomoreveal.common import UserError
    p = str(tmp_path / "picture.tif"); tifffile.imwrite(p, np.zeros((50, 80, 3), np.uint8), photometric="rgb")
    with pytest.raises(UserError, match="colour"):
        load_volume(p)
