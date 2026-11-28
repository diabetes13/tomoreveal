"""Splitting a part and fading the tomogram in the second half: no pulse at the join, an option to fade over the whole
part, and the background blur of close-ups keeps the objects in focus sharp."""
import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import edits, look
from tomoreveal import storyboard as sb


def split_slide(ds, mode="faded", whole=False):
    spec = tr.default_spec(ds)
    part = sb.new_part("slide", **{"from": "first", "to": "last", "structures": "above"}); part["seconds"] = 4
    spec["shots"] = [part]
    plan = sb.build_plan(spec, ds); start, count = sb.main_range(plan, part["id"]); mid = start + count // 2
    edits.split_part(spec, ds, part["id"], mid)
    spec["shots"][1]["tomogram"] = dict(sb.tomogram_setting(mode, 0.3), whole=whole)
    return spec, mid


def test_split_keeps_moving_through_the_join(studio):
    spec, mid = split_slide(studio.ds)
    assert spec["shots"][0]["ease_end"] == "sudden"
    ks = [f["k"] for f in sb.build_plan(spec, studio.ds).frames]
    steps = np.diff(ks[mid - 12: mid + 12])
    assert max(np.convolve(steps == 0, np.ones(4), "valid")) < 4  # never four frames in a row without moving on


def test_tomogram_can_fade_over_the_whole_part(studio):
    spec, mid = split_slide(studio.ds, whole=True)
    plan = sb.build_plan(spec, studio.ds); second = spec["shots"][1]["id"]
    start, count = sb.main_range(plan, second)
    sl = [f["slice"] for f in plan.frames[start: start + count]]
    assert sl[count // 2] > 0.4 and abs(sl[-1] - 0.3) < 0.02  # still half way at the middle, faded at the end
    assert np.all(np.diff(sl) <= 1e-9)


def test_no_jump_when_the_slice_starts_to_fade(studio):
    """Soft look (depth fade): the far slice kept its fade only while it was opaque, so the picture jumped."""
    ds, ren = studio.ds, studio.ren
    spec, mid = split_slide(ds); look.apply_look(spec, "soft")
    f = sb.build_plan(spec, ds).frames[mid - 3]
    a, b, c = (ren.draw(dict(f, slice=op), spec, (320, 180)).astype(float) for op in (1.0, 0.995, 0.9))
    assert abs(a.mean() - b.mean()) < 1.0
    assert abs(b.mean() - c.mean()) < 3.0


def test_background_blur_keeps_the_objects_in_focus_sharp(studio):
    ds, ren = studio.ds, studio.ren
    spec = tr.default_spec(ds); spec["style"].update(legend=False, slice_label=False)
    spec["shots"] = [sb.new_part("focus", target={"items": [{"structure": ds.key_of(2), "component": 1}]}, motion="none", seconds=1)]

    def frame(on):
        spec["look"] = {"vignette": {"when": "never", "blur": {"on": on, "strength": 1.0}}}
        f = [g for g in tr.build_timeline(spec, ds)[0] if g["e"] >= 0.999][-1]
        return ren.draw(f, spec, (320, 180)).astype(float)
    sharp, blurred = frame(False), frame(True)
    ren.draw([g for g in tr.build_timeline(spec, ds)[0] if g["e"] >= 0.999][-1], spec, (320, 180))
    idimg = ren._id_pass((320, 180))[0]
    tgt = ren.comp_actors[(2, 1)]
    col = next(c for c, a in ren._id_actors.items() if a is tgt)
    m = np.all(idimg == col, axis=2)
    from scipy import ndimage
    inner = ndimage.binary_erosion(m, iterations=3); outside = ~ndimage.binary_dilation(m, iterations=6)
    assert inner.sum() > 100
    assert np.abs(sharp - blurred)[inner].mean() < 1.0      # the vesicle in focus is unchanged
    assert np.abs(sharp - blurred)[outside].mean() > 3.0    # the rest is blurred


def test_flat_colour_pass_restores_the_render_passes(studio):
    """The outline / blur pass draws without shadow and SSAO passes (NVIDIA shader errors with real shadows)."""
    ds, ren = studio.ds, studio.ren
    spec = tr.default_spec(ds); look.apply_look(spec, "soft"); spec["look"]["lighting"] = {"preset": "soft"}
    f = tr.build_timeline(spec, ds)[0][-1]
    ren.draw(f, spec, (160, 90)); before = ren.pl.renderer.GetPass()
    assert before is not None  # SSAO of the Soft lighting
    ren._id_pass((160, 90))
    assert ren.pl.renderer.GetPass() is before


def test_a_tomogram_fade_starts_with_an_overlapping_transition(studio):
    """The fade noted its first frame before the overlap took frames back: it started as many frames late."""
    ds = studio.ds; spec = tr.default_spec(ds)
    a = sb.new_part("overview", seconds=2, tomogram=sb.tomogram_setting("shown"))
    b = sb.new_part("overview", seconds=2, view=dict(tilt=70.0, az=60.0, zoom=1.2), transition=2.0, transition_in=dict(overlap_prev=1.0),
                    tomogram=sb.tomogram_setting("hidden", fade=1.0))
    spec["shots"] = [a, b]
    s = [f["slice"] for f in sb.build_plan(spec, ds).frames if f["part"] == b["id"]]
    assert 0 < 1.0 - s[0] < 0.05 and s[29] == 0.0 and s[28] > 0.0  # from the first frame of the flight, over 1 s
    assert all(y <= x for x, y in zip(s, s[1:]))


def test_a_card_keeps_its_fade_when_the_next_part_overlaps_it(studio):
    ds = studio.ds; spec = tr.default_spec(ds)
    k = sb.new_part("card", seconds=2)
    b = sb.new_part("overview", seconds=2, view=dict(tilt=70.0, az=60.0, zoom=1.2), transition_in=dict(overlap_prev=0.5))
    spec["shots"] = [sb.new_part("overview", seconds=1), k, b]
    cards = [f for f in sb.build_plan(spec, ds).frames if f.get("card") == k["id"]]
    assert len(cards) == 60 and cards[-1]["card_fade"] < 0.1  # it fades out completely, then the flight begins


def test_an_overlapped_close_up_after_a_slide_without_structures_shows_them(studio):
    """The close-up switched the structures on, then took back the slide's frames and with them the slide's state."""
    ds = studio.ds; spec = tr.default_spec(ds)
    a = sb.new_part("slide_up", seconds=2, pause_after=0)
    b = sb.new_part("focus", target=sb.make_target([(ds.key_of(2), 1)]), seconds=2, transition_in=dict(overlap_prev=1.0))
    spec["shots"] = [a, b]
    mine = [f for f in sb.build_plan(spec, ds).frames if f["part"] == b["id"]]
    assert mine and all(f["reveal"] and f["cut"] == "all" for f in mine)


def arrival(ds, nxt, overlap):
    spec = tr.default_spec(ds)
    a = sb.new_part("slide", seconds=2, pause_after=0, **{"from": "first", "to": "last"})
    b = nxt(); b["transition_in"] = dict(b.get("transition_in") or {}, overlap_prev=overlap)
    spec["shots"] = [a, b]
    return next(f["k"] for f in sb.build_plan(spec, ds).frames if f["part"] == b["id"] and f["phase"] == "main")


@pytest.mark.parametrize("nxt", [
    lambda: sb.new_part("overview", seconds=1, view=dict(tilt=70.0, az=60.0, zoom=1.2)),
    lambda: sb.new_part("hold", seconds=1, view=dict(tilt=20.0, az=30.0, zoom=1.0)),
    lambda: sb.new_part("overview", seconds=1, transition_in=dict(kind="dissolve")),
    lambda: sb.new_part("slide", seconds=1, **{"from": "current", "to": "first"})], ids=["overview", "hold", "dissolve", "current"])
def test_an_overlap_arrives_at_the_slice_it_would_without(studio, nxt):
    assert arrival(studio.ds, nxt, 0.5) == arrival(studio.ds, nxt, 0.0)
