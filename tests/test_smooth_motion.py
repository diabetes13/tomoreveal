"""Plan 3.5 phase 3: steady close-up framing, a smoother wiggle, 60 fps and motion blur."""
import math

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import storyboard as sb
from tomoreveal import animations


def closeup(ds, seconds=4, **kw):
    spec = tr.default_spec(ds)
    part = sb.new_part("focus", target=sb.make_target([(ds.key_of(2), 1)]), caption="", seconds=seconds, **kw)
    spec["shots"] = [sb.new_part("overview", motion="none", seconds=0.5), part]
    frames = [f for f in sb.build_plan(spec, ds).frames if f["part"] == part["id"] and f["phase"] == "main"]
    return spec, part, frames


@pytest.mark.parametrize("motion", ["orbit", "rock", "wiggle"])
def test_steady_zoom(studio, motion):
    ds = studio.ds
    spec, part, fr = closeup(ds, motion=motion, tilt=40.0)
    d = np.array([f["pose"]["dist"] for f in fr])
    assert d.max() / d.min() - 1 < 1e-4
    w, h = spec["output"]["size"]; frm = sb.Framer(ds.shape, w / h, 30.0, False)
    lo, hi = sb.focus_box(part, ds)
    for f in fr[::7]:  # the object fits at every angle reached
        assert frm.fit(lo, hi, f["pose"]["tilt"], f["pose"]["az"], 1.0)["dist"] <= d[0] * 1.02
    old = [f["pose"]["dist"] for f in closeup(ds, motion="orbit", tilt=40.0, steady_zoom=None)[2]]
    assert max(old) / min(old) - 1 > 0.01  # the old way pumps


def test_wiggle_starts_and_ends_at_rest(studio):
    spec, part, fr = closeup(studio.ds, seconds=5.3, motion="wiggle")
    off = np.array([f["fx"]["wiggle"] for f in fr])
    assert np.allclose(off[0], 0) and np.allclose(off[-1], 0, atol=1e-3)
    v = np.diff(off, axis=0); a = np.diff(v, axis=0)
    assert np.abs(v[0]).max() < 0.02 and np.abs(v[-1]).max() < 0.02  # speed 0 at both ends
    old = closeup(studio.ds, seconds=5.3, motion="wiggle", wiggle=dict(amplitude=5, frequency=0.35, shape="circle"))[2]  # 3.4 formula
    ao = np.diff(np.array([f["fx"]["wiggle"] for f in old]), n=2, axis=0)
    jerk, jerk_old = np.abs(np.diff(a, axis=0)).max(), np.abs(np.diff(ao, axis=0)).max()
    assert jerk < 0.5 * jerk_old, (jerk, jerk_old)  # no kinks: far smaller jumps in acceleration than the 3.4 wiggle
    assert np.allclose(fr[0]["pose"]["pos"], fr[-1]["pose"]["pos"], atol=1e-2)  # ends where it began
    T = (len(fr) - 1) / 30
    assert round(0.35 * T) == 2  # 5.3 s at speed 0.35: two whole cycles
    assert animations.wiggle_offsets(part["wiggle"], T / 2, T)[0] == pytest.approx(0.0, abs=1e-6)  # half-way through two cycles


def test_wiggle_from_the_top_does_not_spin_the_picture(studio):
    spec, part, fr = closeup(studio.ds, motion="wiggle")  # new close-ups look from the top
    assert part["tilt"] == 0.0
    ups = np.array([f["pose"]["up"] for f in fr])
    assert np.degrees(np.arccos(np.clip(ups @ ups[0], -1, 1))).max() < part["wiggle"]["amplitude"] + 1


def test_60_fps_doubles_frames_and_keeps_durations(studio):
    spec = tr.default_spec(studio.ds)
    n30 = len(sb.build_plan(spec, studio.ds).frames)
    spec["output"]["fps"] = 60
    plan = sb.build_plan(spec, studio.ds)
    assert abs(len(plan.frames) - 2 * n30) <= 2 * len(spec["shots"]) + 4
    assert plan.fps == 60


def test_motion_blur_only_where_things_move(studio, tmp_path):
    from tomoreveal.movie import motion_blurred
    spec = tr.default_spec(studio.ds); spec["style"].update(legend=False, slice_label=False)
    spec["shots"] = [sb.new_part("overview", motion="spin", seconds=2)]
    frames = sb.build_plan(spec, studio.ds).frames
    s, nxt = frames[20], frames[21]
    sharp = studio.ren.draw(s, spec, (320, 180)).astype(int)
    blur = motion_blurred(studio.ren, s, nxt, spec, (320, 180), False, False).astype(int)
    diff = np.abs(blur - sharp).sum(2)
    assert diff.max() > 30 and (diff > 0).mean() < 0.9
    c = (90, 160)  # the turning centre hardly moves
    assert diff[c[0] - 3:c[0] + 4, c[1] - 3:c[1] + 4].mean() < diff.mean() * 2 + 5
    spec["output"].update(size=[160, 90], motion_blur=True, aa=False)
    spec["shots"][0]["seconds"] = 0.3
    n, _ = tr.render_movie(studio.ren, spec, str(tmp_path / "b.mp4"), poster=False)
    assert n == len(sb.build_plan(spec, studio.ds).frames)


def framer_of(spec, ds):
    w, h = spec["output"]["size"]
    return sb.Framer(ds.shape, w / h, float(spec["style"].get("fov", 30)), False)


def camera_rotation(p, fr):
    """The camera's orientation (right, up, back) as the renderer sets it up, roll included."""
    if p.get("pos") is not None:
        back = np.asarray(p["pos"], float) - np.asarray(p["c"], float); up = np.asarray(p["up"], float)
    else:
        back, up = (np.asarray(v, float) for v in fr.axes(p["tilt"], p["az"]))
    back = back / np.linalg.norm(back); up = up - back * float(up @ back); up /= np.linalg.norm(up)
    return np.column_stack([np.cross(up, back), up, back])


def largest_turn(spec, ds, within=None):
    """The largest change of the camera's orientation between two frames (degrees): of a flight, or into or out of one;
    or, with within (a part id), between two frames of that part's own motion."""
    fr = framer_of(spec, ds); frames = sb.build_plan(spec, ds).frames; R = [camera_rotation(f["pose"], fr) for f in frames]
    if within is None:
        pick = lambda a, b: "transition" in (a["phase"], b["phase"])
    else:
        pick = lambda a, b: a["part"] == b["part"] == within and a["phase"] == b["phase"] == "main"
    return max(math.degrees(math.acos(min(1.0, max(-1.0, (np.trace(R[i].T @ R[i + 1]) - 1) / 2))))
               for i in range(len(R) - 1) if pick(frames[i], frames[i + 1]))


def spin(axis, **kw):
    return sb.new_part("overview", motion="spin", spin_axis=axis, seconds=3, **kw)


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize("overlap", [0.5, 1.0, 1.5])
@pytest.mark.parametrize("where", ["into", "out of", "between"])
def test_an_overlapping_flight_and_a_spin_around_x_or_y_never_flip(studio, axis, overlap, where):
    """The flight blended tilt and azimuth, but such a spin has a position and an up direction of its own: the picture
    flipped (92 to 180 degrees) between two frames."""
    ds = studio.ds; spec = tr.default_spec(ds); other = "y" if axis == "x" else "x"
    if where == "into":
        parts = [sb.new_part("overview", seconds=1), spin(axis, transition=2.0, transition_in=dict(overlap_next=overlap))]
    elif where == "out of":
        parts = [spin(axis), sb.new_part("overview", seconds=2, view=dict(tilt=20.0, az=60.0, zoom=1.0), transition=2.0,
                                         transition_in=dict(overlap_prev=overlap))]
    else:
        parts = [spin(axis), spin(other, transition=2.0, transition_in=dict(overlap_prev=overlap, overlap_next=overlap))]
    spec["shots"] = parts
    assert largest_turn(spec, ds) < 20


@pytest.mark.parametrize("source", ["follow", "points"])
def test_flights_into_and_out_of_a_camera_path_never_flip(studio, source):
    """The picture turned by up to 180 degrees in one frame on the way into or out of a camera path."""
    ds = studio.ds; spec = tr.default_spec(ds)
    wps = [dict(pos=[40.0, 40.0, 150.0], focal=[128.0, 160.0, 24.0]), dict(pos=[200.0, 120.0, 120.0], focal=[128.0, 160.0, 24.0]),
           dict(pos=[60.0, 280.0, 90.0], focal=[128.0, 200.0, 24.0])]
    path = sb.new_part("path", seconds=4, source=dict(kind=source, items=[{"structure": ds.key_of(3), "component": None}] if source == "follow" else []),
                       waypoints=wps if source == "points" else [])
    spec["shots"] = [sb.new_part("overview", seconds=1), path, sb.new_part("overview", seconds=1, view=dict(tilt=30.0, az=200.0, zoom=1.0))]
    assert not sb.build_plan(spec, ds).problems
    assert largest_turn(spec, ds) < 20
    if source == "points":  # N-21: where the view crossed 20 degrees from straight down, the picture rolled by 40 in one frame
        assert largest_turn(spec, ds, within=path["id"]) < 10


def test_an_arc_keeps_a_camera_of_its_own_at_its_distance(studio):
    ds = studio.ds; spec = tr.default_spec(ds)
    b = spin("x", transition=2.0, transition_in=dict(path="arc", overlap_next=1.0))
    spec["shots"] = [sb.new_part("overview", seconds=1), b]
    t = [f["pose"] for f in sb.build_plan(spec, ds).frames if f["part"] == b["id"] and f["phase"] == "transition"]
    assert t and all(p.get("pos") is not None for p in t)
    for p in t:
        assert np.linalg.norm(np.asarray(p["pos"]) - np.asarray(p["c"])) == pytest.approx(p["dist"], rel=1e-6)


def test_cameras_without_a_position_of_their_own_move_as_before(studio):
    from tomoreveal import transitions
    rng = np.random.default_rng(1); fr = framer_of(tr.default_spec(studio.ds), studio.ds)
    for _ in range(50):
        a, b = ({"c": rng.uniform(0, 200, 3), "tilt": rng.uniform(0, 90), "az": rng.uniform(-360, 360), "dist": rng.uniform(50, 900),
                 "scale": rng.uniform(10, 300)} for _ in range(2))
        q, d = rng.uniform(), rng.uniform(-400, 400)
        for want, got in ((transitions.lerp_pose(a, b, q, d_az=d, fov=30.0), transitions.lerp_camera(fr, a, b, q, d, a, b)),
                          (transitions.lerp_pose(a, b, q, fov=30.0), transitions.lerp_camera(fr, a, b, q, None, a, b))):
            assert set(want) == set(got) and all(np.allclose(want[k], got[k], rtol=0, atol=0) for k in want)
