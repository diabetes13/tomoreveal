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


def offset_along(poses):
    """Where the camera is, seen from the point it looks at, along the path (+ ahead, - behind), sideways (+ right) and up."""
    pos = np.array([p["pos"] for p in poses]); c = np.array([p["c"] for p in poses])
    T = np.gradient(c, axis=0); T /= np.linalg.norm(T, axis=1, keepdims=True) + 1e-12
    R = np.cross(T, [0.0, 0.0, 1.0]); R /= np.linalg.norm(R, axis=1, keepdims=True)
    off = pos - c; off /= np.linalg.norm(off, axis=1, keepdims=True); m = slice(len(poses) // 4, 3 * len(poses) // 4)
    return float(np.mean(np.sum(off * T, 1)[m])), float(np.mean(np.sum(off * -R, 1)[m])), float(np.mean(off[m, 2]))


def test_behind_in_an_old_follow_path_is_behind(noisy_tube):
    """N-11: a follow path from before 3.5 (no steadiness) drew the side "behind" exactly as "right"."""
    cam = lambda side: dict(paths.PATH_CAMERA, side=side)
    along, right, up = offset_along(follow(noisy_tube, "labels:tube#1", steadiness=None, camera=cam("behind"))[2])
    assert along < -0.5 and abs(right) < 0.2 and up > 0.2
    for side, want in (("right", 1), ("left", -1)):  # the other sides as before
        assert offset_along(follow(noisy_tube, "labels:tube#1", steadiness=None, camera=cam(side))[2])[1] * want > 0.5


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


# ---- a path stays on the same object, in the same direction, when the volume is flipped (G-33) ----
FLIPS = [("x",), ("y",), ("z",), ("x", "y")]


def shape_to_follow(kind):
    """A label volume of one object on an uneven grid: a curved filament, an S, or a wavy sheet."""
    from scipy import ndimage
    lab = np.zeros((47, 150, 173), bool); t = np.linspace(0, 1, 3000)
    if kind == "filament":
        z, y, x = 12 + 20 * t, 20 + 90 * t ** 2, 15 + 140 * t
    elif kind == "s":
        z, y, x = 20 + 6 * np.sin(5 * t), 70 + 30 * np.sin(2 * math.pi * t), 12 + 145 * t
    if kind == "sheet":
        for yy in range(30, 75):
            x = np.arange(15, 155); z = 20 + 6 * np.sin(x / 21.0) + 0.05 * (yy - 30)
            for dz in (0, 1, 2):
                lab[np.round(z).astype(int) + dz, yy, x] = True
    else:
        lab[np.round(z).astype(int), np.round(y).astype(int), np.round(x).astype(int)] = True
        lab = ndimage.binary_dilation(lab, ndimage.generate_binary_structure(3, 1), iterations=2)
    return lab.astype(np.int16)


def shown_flipped(lab, axes):
    ds = Dataset(); ds.flip = tuple(axes); ax = tuple({"z": 0, "y": 1, "x": 2}[a] for a in axes)
    ds.set_segmentation([dict(kind="labels", key="labels:o", title="o", arr=np.flip(lab, ax) if ax else lab, classes={1: {"name": "o"}})], vox=10.0)
    return ds


def mirrored(points, shape, axes):
    p = np.array(points, float); n = dict(zip("zyx", shape))
    for a in axes:
        i = "xyz".index(a); p[..., i] = n[a] - 1 - p[..., i]
    return p


@pytest.mark.parametrize("clip", [None, ((10, 5, 0), (120, 140, 40))])
@pytest.mark.parametrize("most", [4_000_000, 30_000])  # with and without binning
@pytest.mark.parametrize("kind", ["filament", "s", "sheet"])
def test_a_line_is_the_same_when_the_volume_is_flipped(kind, most, clip, monkeypatch):
    real = paths._items_mask; monkeypatch.setattr(paths, "_items_mask", lambda *a: real(*a, max_voxels=most))
    lab = shape_to_follow(kind); items = [("labels:o#1", None)]
    plain = paths.centre_line(shown_flipped(lab, ()), items, clip)
    for axes in FLIPS:
        c = clip
        if clip is not None:
            a, b = mirrored(clip[0], lab.shape, axes), mirrored(clip[1], lab.shape, axes)
            c = tuple(map(int, np.minimum(a, b))), tuple(map(int, np.maximum(a, b)))
        line = paths.centre_line(shown_flipped(lab, axes), items, c)
        assert line.shape == plain.shape and np.abs(mirrored(line, lab.shape, axes) - plain).max() < 1e-6, axes


@pytest.mark.parametrize("side,steadiness", [(s, v) for s in ("above", "left", "right", "behind") for v in (None, 0, 50, 100)
                                             if (s, v) != ("behind", None)])  # only the steady camera has that side
def test_the_camera_stays_beside_the_same_thing_when_the_volume_is_flipped(side, steadiness):
    lab = shape_to_follow("s"); cam = dict(side=side, height=25.0)
    spec, part, plain = follow(shown_flipped(lab, ()), "labels:o#1", steadiness=steadiness, camera=dict(cam))
    for axes in FLIPS:
        ds = shown_flipped(lab, axes); sp = tr.default_spec(ds); sp["shots"] = [dict(part, camera=dict(cam))]
        sb.flip_spec(sp, lab.shape, axes)
        poses = [f["pose"] for f in sb.build_plan(sp, ds).frames if f.get("part") == part["id"] and f["phase"] == "main"]
        assert len(poses) == len(plain)
        if axes == ("z",):  # still on the same side, and still above (a flip of z does not put the camera below)
            assert sp["shots"][0]["camera"]["side"] == side and all(p["pos"][2] > p["c"][2] for p in poses)
            continue
        for what in ("pos", "c"):
            got = mirrored([p[what] for p in poses], lab.shape, axes)
            assert np.abs(got - np.array([p[what] for p in plain])).max() < 1e-6, (axes, what)
        sb.flip_spec(sp, lab.shape, axes); assert sp["shots"][0]["camera"]["side"] == side  # flipped back: as it was


def test_a_free_camera_follows_the_main_view_also_when_flipped():
    lab = shape_to_follow("s"); cam = dict(side="free")
    _, part, plain = follow(shown_flipped(lab, ()), "labels:o#1", camera=dict(cam)); ds = shown_flipped(lab, ("x",))
    sp = tr.default_spec(ds); sp["shots"] = [dict(part, camera=dict(cam))]; sb.flip_spec(sp, lab.shape, ("x",))
    poses = [f["pose"] for f in sb.build_plan(sp, ds).frames if f.get("part") == part["id"] and f["phase"] == "main"]
    assert np.allclose(mirrored([p["c"] for p in poses], lab.shape, ("x",)), [p["c"] for p in plain], atol=2)
    assert np.allclose([p["pos"] - p["c"] for p in poses], [p["pos"] - p["c"] for p in plain], atol=2)
