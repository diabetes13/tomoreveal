"""Phase 1: tomogram only, segmentation only, both, several sources, sources on another grid, settings by stable key."""
import json
import os
import sys

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal.volumes import load_volume, write_mrc

from fake_session import write_session


def quiet(*a):
    pass


def plan(st, spec=None):
    frames, marks = tr.build_timeline(spec or tr.default_spec(st.ds), st.ds)
    return frames, [m["label"] for m in marks]


def names(info):
    return [c["name"] for c in info["structures"]]


@pytest.fixture(scope="module")
def extra(demo, tmp_path_factory):
    d = tmp_path_factory.mktemp("extra")
    lab, vox = load_volume(demo["labels"]); tomo, _ = load_volume(demo["tomogram"])
    other = str(d / "other_labels.mrc")
    write_mrc(other, ((lab == 1) * 1 + (lab == 3) * 2).astype("int8"), vox)
    json.dump({"1": {"name": "alpha"}, "2": {"name": "beta"}}, open(str(d / "other_labels.json"), "w"))
    small = str(d / "small_labels.mrc")
    write_mrc(small, np.ascontiguousarray(lab[:, :100, :100]), vox)  # another grid
    feat = str(d / "demo_tomogram__vesicles.mrc")
    write_mrc(feat, (lab == 2).astype("float32"), vox)
    ses = write_session(str(d / "demo.ORSSession"), dims=lab.shape[::-1], labels=lab, image=tomo,
                        names=("membrane", "vesicles", "filaments", "particles"),
                        colors=(0x6E56D7, 0xE8B945, 0x3FD2FF, 0x86E9B8), visible=(1, 1, 1, 1),
                        title="Segmentation demo", extra_title="training data")
    return dict(other=other, small=small, feat=feat, session=ses)


def test_tomogram_only(demo):
    st = tr.Studio(headless=True)
    info = st.add_files([demo["tomogram"]], log=quiet, make_renderer=False)
    assert info["can_show"] and info["has_tomogram"] and not info["has_segmentation"]
    assert info["structures"] == [] and info["dims"] == [256, 320, 48]
    frames, marks = plan(st)
    assert marks == ["Slices", "Overview"]
    assert not any(f["reveal"] and f["shot"] == 1 for f in frames)
    json.dumps(info["spec"])


def test_segmentation_only_renders(demo, needs_3d):
    st = tr.Studio(headless=True)
    info = st.add_files([demo["labels"]], log=quiet, make_renderer=False)
    assert info["can_show"] and not info["has_tomogram"] and info["dims"] == [256, 320, 48]
    assert names(info) == ["membrane", "vesicles", "filaments", "particles"]
    frames, marks = plan(st)
    assert "Slices" not in marks and marks[0] == "Reveal"
    assert all(f["slice"] == 0 for f in frames)
    st.ensure_renderer()
    img = st.ren.draw(frames[-1], info["spec"], (320, 180))
    rgb = img.reshape(-1, 3).astype(int)
    assert ((np.abs(rgb[:, 0] - rgb[:, 1]) > 40) | (np.abs(rgb[:, 1] - rgb[:, 2]) > 40)).mean() > 0.01
    st._drop_renderer()


def test_remove_tomogram_keeps_structures_and_settings(demo):
    st = tr.Studio(headless=True)
    info = st.add_files([demo["tomogram"], demo["labels"]], log=quiet, make_renderer=False)
    spec = info["spec"]; key = info["structures"][1]["key"]
    spec["classes"][key].update(name="my vesicles", color="#123456")
    info2 = st.remove_source("file:" + st._norm(demo["tomogram"]), log=quiet, make_renderer=False)
    assert not info2["has_tomogram"] and [c["key"] for c in info2["structures"]] == [c["key"] for c in info["structures"]]
    tr.merge_settings(spec, info2["spec"])
    assert spec["classes"][key] == dict(name="my vesicles", color="#123456", show=True)


def test_two_label_sources_on_one_grid(demo, extra):
    st = tr.Studio(headless=True)
    info = st.add_files([demo["labels"], extra["other"]], log=quiet, make_renderer=False)
    assert names(info) == ["membrane", "vesicles", "filaments", "particles", "alpha", "beta"]
    keys = [c["key"] for c in info["structures"]]
    assert len(set(keys)) == 6 and len({c["source"] for c in info["structures"]}) == 2
    info = st.set_segmentation_enabled("labels:" + st._norm(extra["other"]), False, log=quiet, make_renderer=False)
    assert [c["key"] for c in info["structures"]] == keys[:4]
    for _ in range(2):  # switching back and forth rebuilds the same structures each time
        info = st.set_segmentation_enabled("labels:" + st._norm(extra["other"]), True, log=quiet, make_renderer=False)
        assert [c["key"] for c in info["structures"]] == keys
        info = st.set_segmentation_enabled("labels:" + st._norm(demo["labels"]), False, log=quiet, make_renderer=False)
        assert names(info) == ["alpha", "beta"]
        info = st.set_segmentation_enabled("labels:" + st._norm(demo["labels"]), True, log=quiet, make_renderer=False)


def test_source_on_another_grid_is_listed_but_unusable(demo, extra):
    st = tr.Studio(headless=True)
    info = st.add_files([demo["tomogram"], demo["labels"], extra["small"]], log=quiet, make_renderer=False)
    src = {os.path.basename(s["key"].split(":", 1)[1]): s for s in info["segmentation_sources"]}  # keys are "<kind>:<path>"
    bad = src["small_labels.mrc"]
    assert not bad["usable"] and "100 × 100 × 48" in bad["reason"] and "256 × 320 × 48" in bad["reason"]
    assert src["demo_labels.mrc"]["usable"] and names(info) == ["membrane", "vesicles", "filaments", "particles"]


def test_settings_survive_easymode_file_and_tomogram_switch(demo, extra):
    st = tr.Studio(headless=True)
    info = st.add_files([demo["tomogram"], demo["labels"]], log=quiet, make_renderer=False)
    spec = info["spec"]; key = info["structures"][0]["key"]; spec["classes"][key]["name"] = "my membrane"
    spec["shots"].append(tr.focus_shot({"class": key}, "my membrane"))
    info = st.add_files([extra["feat"]], log=quiet, make_renderer=False)
    tr.merge_settings(spec, info["spec"])
    assert spec["classes"][key]["name"] == "my membrane" and names(info)[-1] == "vesicles" and len(info["structures"]) == 5
    for choice in (None, "file:" + st._norm(demo["tomogram"])):
        info = st.set_tomogram_source(choice, log=quiet, make_renderer=False)
        tr.merge_settings(spec, info["spec"])
        assert spec["classes"][key]["name"] == "my membrane" and spec["shots"][-1]["target"]["class"] == key
    _, marks = plan(st, spec)
    assert marks[-1] == "my membrane"  # the close-up still finds its structure by key


def test_session_alone_gives_tomogram_and_segmentation(demo, extra):
    st = tr.Studio(headless=True)
    info = st.add_files([extra["session"]], log=quiet, make_renderer=False)
    p = st._norm(extra["session"])
    assert info["tomogram"] == f"session:{p}:0" and info["has_tomogram"]
    src = {s["label"]: s for s in info["segmentation_sources"]}
    assert src["Segmentation demo"]["enabled"] and not src["training data"]["enabled"]
    assert src["training data"]["names"] == ["membrane", "vesicles", "filaments", "particles"]
    assert names(info) == ["membrane", "vesicles", "filaments", "particles"]
    assert info["structures"][0]["key"] == f"dragonfly:{p}:Segmentation demo#1" and info["structures"][0]["color"] == "#d7566e"
    info = st.add_files([demo["tomogram"]], log=quiet, make_renderer=False)  # the original file: preferred, and lined up
    assert info["tomogram"] == "file:" + st._norm(demo["tomogram"]) and "same grid" in info["match_note"]
    assert names(info) == ["membrane", "vesicles", "filaments", "particles"]
    info = st.remove_source(f"session:{p}", log=quiet, make_renderer=False)
    assert info["structures"] == [] and info["segmentation_sources"] == [] and info["has_tomogram"]


def test_start_over_and_old_load_interface(demo, extra):
    st = tr.Studio(headless=True)
    info = st.load([extra["session"]], roi="training", image="s0", log=quiet, make_renderer=False)
    assert [s["label"] for s in info["segmentation_sources"] if s["enabled"]] == ["training data"]
    info = st.reset()
    assert not info["can_show"] and info["segmentation_sources"] == [] and len(info["tomogram_choices"]) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows file names ignore letter case")
def test_one_file_under_two_spellings_is_loaded_once(demo, tmp_path):
    """F-33: the same file typed in other letter case is one source, labelled with the case on disk."""
    lab, vox = load_volume(demo["labels"]); p = str(tmp_path / "Mixed_Labels.mrc"); write_mrc(p, lab, vox)
    st = tr.Studio(headless=True)
    st.add_files([demo["tomogram"], p], log=quiet, make_renderer=False)
    st.add_files([p.lower(), p.upper()], log=quiet, make_renderer=False)
    assert [s["group"] for s in st.seg_sources] == ["Mixed_Labels.mrc"]



def test_voxel_size_follows_the_tomogram_in_use(demo, tmp_path):
    """F-47: the first voxel size was never cleared: a tomogram without one, or no tomogram, kept the old size."""
    tomo, _ = load_volume(demo["tomogram"]); lab, _ = load_volume(demo["labels"])
    a, b, seg = str(tmp_path / "a.mrc"), str(tmp_path / "b.mrc"), str(tmp_path / "seg.mrc")
    write_mrc(a, tomo, 10.0); write_mrc(b, tomo, None); write_mrc(seg, lab, 30.0)
    st = tr.Studio(headless=True)
    st.add_files([a, b], as_tomogram=[a, b], log=quiet, make_renderer=False)
    st.set_tomogram_source("file:" + st._norm(a), log=quiet, make_renderer=False); assert st.ds.vox == pytest.approx(10.0)
    st.set_tomogram_source("file:" + st._norm(b), log=quiet, make_renderer=False); assert st.ds.vox is None  # b has no size
    st.add_files([seg], as_labels=[seg], log=quiet, make_renderer=False)
    st.set_tomogram_source("file:" + st._norm(a), log=quiet, make_renderer=False); assert st.ds.vox == pytest.approx(10.0)
    st.set_tomogram_source(None, log=quiet, make_renderer=False); assert st.ds.vox == pytest.approx(30.0)  # the segmentation's
