"""Plan 3.4 phase 7: close-up animations (wiggle, push-in, pulse, glowing outline, cross-section sweep, grow-in)."""
import math

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import storyboard as sb
from tomoreveal import edits, transitions


def closeup(st, legacy=False, **kw):
    spec = tr.default_spec(st.ds)
    fields = dict(target=sb.make_target([(st.ds.key_of(2), 2)]), caption="", seconds=4, motion="none")
    fields.update(kw)
    part = sb.new_part("focus", **fields)
    if legacy:  # as saved by 3.4
        part = sb.as_legacy(part)
    edits.insert_part(spec, part)
    frames, _ = tr.build_timeline(spec, st.ds)
    return spec, part, [f for f in frames if f["part"] == part["id"] and f["phase"] == "main"]


def test_wiggle_follows_the_formula_and_eases(studio):
    spec, part, fr = closeup(studio, legacy=True, motion="wiggle", wiggle=dict(amplitude=8, frequency=0.5, shape="circle"))
    T = (len(fr) - 1) / 30
    base = fr[0]["pose"]["az"]
    for i, f in enumerate(fr):
        t = i / 30; A = 8 * transitions.smooth01(t / 0.5) * transitions.smooth01((T - t) / 0.5)
        assert f["pose"]["az"] - base == pytest.approx(A * math.cos(math.pi * t), abs=1e-6)
        assert f["fx"]["wiggle"][1] == pytest.approx(A * math.sin(math.pi * t), abs=1e-3)
    assert fr[0]["fx"]["wiggle"] == [0.0, 0.0] and fr[-1]["fx"]["wiggle"] == [0.0, 0.0]


def test_push_in_moves_closer_and_keeps_the_object_in_frame(studio):
    spec, part, fr = closeup(studio, motion="none", push=dict(on=True, start=1.0, end=1.35))
    d = [f["pose"]["dist"] for f in fr]
    assert all(a >= b - 1e-9 for a, b in zip(d, d[1:])) and d[-1] < d[0] * 0.85
    spec, part, fr = closeup(studio, motion="orbit", push=dict(on=True, start=1.0, end=1.35))  # at the largest zoom
    lo, hi = studio.ds.targets_box(part["target"], pad=False)
    studio.ren.draw(fr[-1], spec, (320, 180))
    ren = studio.ren.pl.renderer
    for x in (lo[0], hi[0]):
        for y in (lo[1], hi[1]):
            for z in (lo[2], hi[2]):
                ren.SetWorldPoint(x, y, z, 1.0); ren.WorldToDisplay(); px, py, _ = ren.GetDisplayPoint()
                assert -1 <= px <= 321 and -1 <= py <= 181


def test_pulse_has_n_maxima_and_brightens(studio):
    spec, part, fr = closeup(studio, highlight=dict(kind="pulse", pulses=3))
    p = np.array([f["fx"]["pulse"] for f in fr])
    peaks = [i for i in range(1, len(p) - 1) if p[i] >= p[i - 1] and p[i] > p[i + 1]]
    assert len(peaks) == 3
    hi = fr[peaks[0]]; lo = fr[0]
    studio.ren.draw(lo, spec, (320, 180)); a = studio.ren.comp_actors[(studio.ds.id_of(studio.ds.key_of(2)), 2)].GetProperty().GetColor()
    studio.ren.draw(hi, spec, (320, 180)); b = studio.ren.comp_actors[(studio.ds.id_of(studio.ds.key_of(2)), 2)].GetProperty().GetColor()
    assert max(b) > max(a)


def test_cross_section_shows_a_ring_and_moves_the_slice(studio):
    spec, part, fr = closeup(studio, effect=dict(kind="section", direction="slices", move_slice=True))
    mid = fr[len(fr) // 2]; c = mid["fx"]["cut"]
    assert mid["k"] == math.floor(c["origin"][2] - 0.6)
    k = studio.ds.id_of(studio.ds.key_of(2))
    studio.ren.draw(mid, spec, (320, 180))
    body, cap = studio.ren._clipped(k, 2, c["normal"], c["origin"])
    assert cap is not None and cap.n_cells > 0 and np.allclose(cap.points[:, 2], round(c["origin"][2] * 2) / 2, atol=0.01)
    lo, hi = studio.ds.targets_box(part["target"], pad=False); centre = (lo + hi) / 2
    r = np.hypot(cap.points[:, 0] - centre[0], cap.points[:, 1] - centre[1])
    assert r.min() > 5 and r.max() > r.min() + 1.5  # a ring: the hollow vesicle's wall, not a filled disc
    assert body.bounds[5] <= c["origin"][2] + 0.6  # nothing is left above the cut
    assert studio.ren.cut_actors[((k, 2), "cap")].GetVisibility()


def test_grow_in_from_the_bottom(studio):
    spec, part, fr = closeup(studio, effect=dict(kind="grow", how="bottom"))
    z = [f["fx"]["cut"]["origin"][2] for f in fr if "cut" in f.get("fx", {})]
    assert z and all(a <= b + 1e-9 for a, b in zip(z, z[1:]))
    k = studio.ds.id_of(studio.ds.key_of(2)); tops = []
    for f in fr[::10]:
        if "cut" in f.get("fx", {}):
            body, _ = studio.ren._clipped(k, 2, f["fx"]["cut"]["normal"], f["fx"]["cut"]["origin"])
            tops.append(body.bounds[5] if body is not None else -1)
    assert all(a <= b + 1e-6 for a, b in zip(tops, tops[1:])) and tops[-1] > tops[0]
    frames, _ = tr.build_timeline(spec, studio.ds)
    first_main = next(i for i, f in enumerate(frames) if f["part"] == part["id"] and f["phase"] == "main")
    into = [f for f in frames[:first_main] if f["part"] == part["id"] and f["phase"] == "transition"]
    assert into[0]["fx"]["fade"] > 0.9 and into[-1]["fx"]["fade"] < 0.05  # hidden on the way in, then grown


def test_everything_together_renders(studio):
    for direction in ("slices", "viewer", "axis"):
        spec, part, fr = closeup(studio, motion="wiggle", push=dict(on=True), highlight=dict(kind="glow"),
                                 effect=dict(kind="section", direction=direction), others=dict(colour="darken", opacity=0.4),
                                 vignette=dict(on=True, strength=0.7))
        for f in fr[::25]:
            img = studio.ren.draw(f, spec, (320, 180))
            assert img.shape == (180, 320, 3)
    spec, part, fr = closeup(studio, target=sb.make_target([(studio.ds.key_of(2), None)]), effect=dict(kind="grow", how="fade"),
                             highlight=dict(kind="pulse"))
    for f in fr[::25]:
        studio.ren.draw(f, spec, (320, 180))
    spec = tr.default_spec(studio.ds)  # leave the renderer as the other tests expect it
    studio.ren.draw(tr.build_timeline(spec, studio.ds)[0][-1], spec, (320, 180))


def test_state_key_includes_the_effects(studio):
    spec, part, fr = closeup(studio, highlight=dict(kind="pulse"))
    assert sb.state_key(fr[0]) != sb.state_key(fr[len(fr) // 4])  # same pose, other brightness: drawn again
