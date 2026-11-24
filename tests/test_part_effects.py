"""3.5: effects (soft shadows, outlines, depth fade, membranes, slice shadows) in some parts only, blended at the
change; structures drawn without soft shadows or outline."""
import numpy as np

import tomoreveal as tr
from tomoreveal import look as lk
from tomoreveal import storyboard as sb


def two_parts(ds, second_look=None, first_look=None):
    spec = tr.default_spec(ds); lk.apply_look(spec, "soft"); spec["style"].update(legend=False, slice_label=False)
    a = sb.new_part("overview", motion="none", seconds=1); b = sb.new_part("overview", motion="none", seconds=1)
    if first_look:
        a["look"] = first_look
    if second_look:
        b["look"] = second_look
    spec["shots"] = [a, b]
    return spec, a, b


def test_without_part_effects_frames_are_unchanged(studio):
    spec, a, b = two_parts(studio.ds)
    assert all("lk" not in f for f in sb.build_plan(spec, studio.ds).frames)
    assert sb.part_look({"look": {"shadow": "none", "outline": "huge", "x": 1, "depth_fade": None}}) == {"shadow": "none"}


def test_effects_change_in_one_part_and_blend_in(studio):
    ds, ren = studio.ds, studio.ren
    spec, a, b = two_parts(ds, second_look={"shadow": "none", "outline": "off", "depth_fade": "off"})
    plan = sb.build_plan(spec, ds); fr = plan.frames
    sa, sb_ = sb.main_range(plan, a["id"]), sb.main_range(plan, b["id"])
    first, second = fr[sa[0] + 5], fr[sb_[0] + sb_[1] - 1]
    assert first["lk"] == {} and second["lk"] == {"shadow": "none", "outline": "off", "depth_fade": "off"}
    blend = [f for f in fr if f.get("lkfrom") is not None]
    assert blend and all(0 < f["lkq"] < 1 for f in blend) and blend[0]["lkfrom"] == {}
    qs = [f["lkq"] for f in blend]; assert qs == sorted(qs)
    i0 = fr.index(blend[0])
    assert all("lkfrom" not in f for f in fr[i0 + len(blend):])
    soft = ren.draw(dict(first, lk={}), spec, (320, 180)).astype(int)
    plain = ren.draw(dict(first, lk=second["lk"]), spec, (320, 180)).astype(int)
    assert np.abs(soft - plain).mean() > 1.0   # shadows and outlines really go
    mid = blend[len(blend) // 2]
    m = ren.draw(dict(mid, pose=first["pose"]), spec, (320, 180)).astype(int)
    q = mid["lkq"]
    assert np.abs(m - (soft * (1 - q) + plain * q)).mean() < 2.0  # a smooth blend in between


def test_structure_without_shadow_or_outline(studio):
    ds, ren = studio.ds, studio.ren
    spec, a, b = two_parts(ds)
    lk.set_look(spec, outline="thick")
    f = sb.build_plan(spec, ds).frames[5]
    mem = next(kk for kk, c in ds.classes.items() if c["name"] == "membrane")
    before = ren.draw(f, spec, (320, 180)).astype(int)
    assert ren.actors[mem].mapper.GetScalarVisibility()
    spec["classes"][ds.key_of(mem)].update(no_shadow=True, no_outline=True)
    after = ren.draw(f, spec, (320, 180)).astype(int)
    assert not ren.actors[mem].mapper.GetScalarVisibility()
    assert np.abs(before - after).mean() > 0.5
