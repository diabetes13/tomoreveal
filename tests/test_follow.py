"""Plan 3.5 phase 4: a steady "follow" camera (smooth rail, upright view, limited turning, no shake)."""
import math

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import paths
from tomoreveal import storyboard as sb
from tomoreveal.volumes import Dataset


@pytest.fixture(scope="module")
def noisy_tube():
    """A half circle (radius 70, z = 20) whose centre line wobbles by about ±3 voxels, tube radius 3."""
    from scipy import ndimage
    nz, ny, nx = 44, 200, 200
    lab = np.zeros((nz, ny, nx), bool); rng = np.random.default_rng(3)
    th = np.linspace(0.02, math.pi - 0.02, 4000)
    wob = sum(rng.uniform(0.6, 1.2) * np.sin(k * th + rng.uniform(0, 6)) for k in (9, 15, 23, 31))
    wob *= 3 / np.abs(wob).max()
    x = 100 + (70 + wob) * np.cos(th); y = 100 + (70 + wob) * np.sin(th); z = 20 + 0.8 * wob[::-1]
    lab[np.round(z).astype(int), np.round(y).astype(int), np.round(x).astype(int)] = True
    lab = ndimage.binary_dilation(lab, ndimage.generate_binary_structure(3, 1), iterations=3).astype(np.int16)
    ds = Dataset()
    ds.set_segmentation([dict(kind="labels", key="labels:tube", title="tube", arr=lab, classes={1: {"name": "tube"}})], vox=10.0)
    return ds


def follow(ds, key, **kw):
    spec = tr.default_spec(ds)
    part = sb.new_part("path", seconds=6, source=dict(kind="follow", items=[{"structure": key, "component": None}]), **kw)
    spec["shots"] = [part]
    frames = [f for f in sb.build_plan(spec, ds).frames if f["part"] == part["id"] and f["phase"] == "main"]
    return spec, part, [f["pose"] for f in frames]


def turn_deg(vs):
    v = np.asarray(vs, float); v /= np.linalg.norm(v, axis=1, keepdims=True)
    return np.degrees(np.arccos(np.clip(np.sum(v[1:] * v[:-1], axis=1), -1, 1)))


def test_steady_on_a_noisy_tube(noisy_tube):
    spec, part, poses = follow(noisy_tube, "labels:tube#1")
    assert part["steadiness"] == 50
    pos = np.array([p["pos"] for p in poses]); look = np.array([p["c"] for p in poses])
    mid = slice(10, -10)

    def shake(p):  # turn per frame minus its running mean: jitter, not the bend of the tube itself
        t = turn_deg(np.diff(p, axis=0))[mid]
        return np.abs(t - np.convolve(t, np.ones(15) / 15, mode="same"))[8:-8].max()
    assert turn_deg(np.diff(pos, axis=0))[mid].mean() < 1.2  # a half circle in 6 s is about 1 degree per frame
    assert shake(pos) < 0.1  # and the camera glides without shaking
    r = np.hypot(look[:, 0] - 100, look[:, 1] - 100)
    assert np.hypot(r - 70, look[:, 2] - 20).max() < 10  # and still looks at the tube
    old = follow(noisy_tube, "labels:tube#1", steadiness=None)[2]
    assert shake(np.array([p["pos"] for p in old])) > 3 * shake(pos)  # the old way follows every wobble


@pytest.mark.parametrize("side", ["above", "left", "behind"])
def test_upright_and_limited_turning(studio, side):
    ds = studio.ds
    spec, part, poses = follow(ds, ds.key_of(1), camera=dict(paths.PATH_CAMERA, side=side))
    ups = np.array([p["up"] for p in poses])
    assert turn_deg(ups).max() < 1.0  # the picture never rolls
    dirs = np.array([np.asarray(p["c"]) - p["pos"] for p in poses])
    assert turn_deg(dirs).max() <= paths.TURN_LIMIT / 30 * 1.1  # 20 degrees per second at most
    if side != "above":
        z = np.array([p["pos"][2] for p in poses])
        assert np.ptp(z[5:-5]) < 3  # a constant height


def test_steadiness_levels_and_edit_as_points(noisy_tube):
    from tomoreveal.gui_paths import points_from_poses
    line = paths.centre_line(noisy_tube, [("labels:tube#1", None)])
    rails = [paths.steady_rail(line, v) for v in (0, 50, 100)]
    bends = [turn_deg(np.diff(r, axis=0)).mean() for r in rails]
    assert bends[0] > bends[1] > bends[2]
    spec, part, poses = follow(noisy_tube, "labels:tube#1")
    wps = points_from_poses(poses)
    assert 7 <= len(wps) <= 9
    p2 = sb.new_part("path", seconds=6, source=dict(kind="points", items=[]), waypoints=wps)
    spec["shots"] = [p2]
    pts = [f["pose"]["pos"] for f in sb.build_plan(spec, noisy_tube).frames if f["part"] == p2["id"] and f["phase"] == "main"]
    ref = np.array([p["pos"] for p in poses])
    d = [np.linalg.norm(ref - q, axis=1).min() for q in pts]
    assert max(d) < 5.0
