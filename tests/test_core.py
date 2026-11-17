"""Tests that need no graphics: file recognition, matching, clip box, movie plan."""
import json
import subprocess
import sys

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal.volumes import load_volume, looks_like_labels, looks_like_scores, score_scale, sidecar_classes


def test_version_command():
    out = subprocess.run([sys.executable, "-m", "tomoreveal", "--version"], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == tr.__version__


def test_demo_files_are_recognised(demo):
    tomo, vox = load_volume(demo["tomogram"]); lab, _ = load_volume(demo["labels"])
    assert tomo.shape == lab.shape == (48, 320, 256) and vox == pytest.approx(10.0)
    assert looks_like_labels(lab) and not looks_like_labels(tomo) and not looks_like_scores(tomo)
    names = sidecar_classes(demo["labels"])
    assert names[1]["name"] == "membrane" and names[4]["color"].startswith("#")


def test_score_volume_conventions():
    p = np.zeros((8, 32, 32), np.float32); p[2:5, 8:20, 8:20] = 1.0
    assert score_scale((p * 127).astype(np.int8)) == 127
    assert score_scale((p * 255).astype(np.uint16)) == 255
    assert score_scale(p) == 1
    assert looks_like_scores((p * 127).astype(np.int8)) and looks_like_scores(p)


def test_wrong_file_type_is_reported(tmp_path):
    bad = tmp_path / "notes.txt"; bad.write_text("x")
    with pytest.raises(tr.UserError):
        load_volume(str(bad))


@pytest.fixture(scope="module")
def ref(demo):
    return load_volume(demo["tomogram"])[0]


def test_match_identical(ref):
    v, notes, r, tf = tr.match_volume(ref.copy(), 10.0, ref, 10.0, log=lambda *a: None)
    assert tf["identity"] and notes == [] and r > 0.99 and np.array_equal(v, ref)


def test_match_flipped(ref):
    v, notes, r, tf = tr.match_volume(np.ascontiguousarray(ref[:, ::-1, :]), 10.0, ref, 10.0, log=lambda *a: None)
    assert tf["flip"] == (1,) and "flipped in Y" in notes[0] and np.array_equal(v, ref)


def test_match_cropped_at_import(ref):
    big = np.pad(ref, ((5, 3), (40, 24), (16, 30)), mode="reflect")
    v, notes, r, tf = tr.match_volume(big, 10.0, ref, 10.0, log=lambda *a: None)
    assert tf["off"] == (5, 40, 16) and np.array_equal(v, ref)
    assert "cropped to x 16-271, y 40-359, z 5-52" in notes[0]
    # a score volume made from the bigger original follows the same crop
    score = np.pad(np.ones(ref.shape, np.int8), ((5, 3), (40, 24), (16, 30)))
    assert tr.apply_match(score, tf, ref.shape).all()


def test_match_binned_at_import(ref):
    fine = ref.repeat(2, 0).repeat(2, 1).repeat(2, 2)
    v, notes, r, tf = tr.match_volume(fine, 5.0, ref, 10.0, log=lambda *a: None)
    assert notes == ["binned x2"] and np.allclose(v, ref, atol=1e-5)


def test_match_refuses_unrelated_file(ref):
    other = np.random.default_rng(0).normal(size=(30, 200, 200)).astype(np.float32)
    with pytest.raises(tr.UserError):
        tr.match_volume(other, 10.0, ref, 10.0, log=lambda *a: None)


@pytest.fixture(scope="module")
def loaded(demo):
    st = tr.Studio(headless=True)
    info = st.load([demo["tomogram"], demo["labels"]], log=lambda *a: None, make_renderer=False)
    return st, info


def test_loading_names_and_surfaces(loaded):
    st, info = loaded
    assert info["ready"] and info["dims"] == [256, 320, 48]
    assert [c["name"] for c in info["classes"]] == ["membrane", "vesicles", "filaments", "particles"]
    assert all(c["mesh"] for c in info["classes"])


def test_default_movie_plan(loaded):
    st, _ = loaded
    spec = tr.default_spec(st.ds); frames, marks = tr.build_timeline(spec, st.ds)
    assert [m["label"] for m in marks] == ["Slices", "Reveal", "Overview"]
    assert {f["k"] for f in frames} == set(range(48))
    assert not frames[0]["reveal"] and frames[-1]["reveal"]
    json.dumps(spec)  # the settings must stay plain data


def test_clip_box_limits_sweep_and_view(loaded):
    st, _ = loaded
    spec = tr.default_spec(st.ds); full, _ = tr.build_timeline(spec, st.ds)
    assert tr.clip_box(spec, st.ds.tomo.shape) is None
    spec["clip"] = dict(x=[20, 200], y=[50, 250], z=[10, 30], fit=True)
    assert tr.clip_box(spec, st.ds.tomo.shape) == ((20, 50, 10), (200, 250, 30))
    clipped, _ = tr.build_timeline(spec, st.ds)
    assert {f["k"] for f in clipped} == set(range(10, 31))
    assert clipped[-1]["pose"]["dist"] < full[-1]["pose"]["dist"]      # camera moved in to frame the kept part
    spec["clip"]["fit"] = False
    unfit, _ = tr.build_timeline(spec, st.ds)
    assert unfit[-1]["pose"]["dist"] == pytest.approx(full[-1]["pose"]["dist"])
    spec["clip"] = dict(x=[0, 255], y=[0, 319], z=[0, 47])
    assert tr.clip_box(spec, st.ds.tomo.shape) is None                 # a full-size box is no clip


def test_closeup_shot_is_added_to_plan(loaded):
    st, _ = loaded
    spec = tr.default_spec(st.ds); n0 = len(tr.build_timeline(spec, st.ds)[0])
    spec["shots"].append(tr.focus_shot({"class": 2}, "vesicles", seconds=2))
    frames, marks = tr.build_timeline(spec, st.ds)
    assert len(frames) > n0 and marks[-1]["label"] == "vesicles"
    assert any(f["caption"] == "vesicles" and f["e"] == 1.0 for f in frames)
