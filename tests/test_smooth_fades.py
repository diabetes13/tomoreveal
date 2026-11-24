"""Splitting a part and fading the tomogram in the second half: no pulse at the join, an option to fade over the whole
part, and the background blur of close-ups keeps the objects in focus sharp."""
import numpy as np

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
