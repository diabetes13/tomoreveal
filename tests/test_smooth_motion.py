"""Plan 3.5 phase 3: steady close-up framing, a smoother wiggle, 60 fps and motion blur."""
import math

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import storyboard as sb


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
    assert sb.wiggle_offsets(part["wiggle"], T / 2, T)[0] == pytest.approx(0.0, abs=1e-6)  # half-way through two cycles


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
    from tomoreveal.render import motion_blurred
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
