"""3.5: in one close-up, single structures can be see-through or hidden ("Each structure"); other parts are unchanged."""
import numpy as np

import tomoreveal as tr
from tomoreveal import storyboard as sb
from test_see_through import BACK, FRONT, centre_of, slabs  # noqa: F401 (fixture)


def closeup(ds, each=None, others="keep"):
    spec = tr.default_spec(ds)
    spec["style"].update(legend=False, slice_label=False, ortho=True); spec["view"].update(tilt=0, az=0)
    spec["look"] = {"lighting": {"preset": "flat"}}
    k1, k2 = ds.key_of(1), ds.key_of(2)
    spec["classes"][k1].update(color=FRONT); spec["classes"][k2].update(color=BACK)
    part = sb.new_part("focus", target={"class": k2}, others=others, motion="none", seconds=0.5, tilt=0.0)
    if each:
        part["each"] = {k1 if k == "front" else k2: v for k, v in each.items()}
    spec["shots"] = [part]
    return spec


def last_frame(ds, ren, spec):
    frames = tr.build_timeline(spec, ds)[0]
    f = [g for g in frames if g["e"] >= 0.999][-1]  # in the close-up (the movie then returns to the overview)
    return f, ren.draw(f, spec, (320, 320)).astype(int)


def test_hidden_structure_shows_what_is_behind_it(slabs):
    ds, ren = slabs
    _, plain = last_frame(ds, ren, closeup(ds))
    assert np.abs(centre_of(ds, ren, plain, 35, 40) - [255, 0, 0]).max() <= 12  # the front slab covers the back one
    f, img = last_frame(ds, ren, closeup(ds, {"front": "hide"}))
    assert f["others"]["each"] == {ds.key_of(1): "hide"}
    assert np.abs(centre_of(ds, ren, img, 35, 40) - [0, 0, 255]).max() <= 12  # now the back slab shows
    assert np.abs(centre_of(ds, ren, img, 15, 40) - [0, 0, 0]).max() <= 12   # and the front one is gone


def test_see_through_structure_lets_the_one_behind_show(slabs):
    ds, ren = slabs
    _, img = last_frame(ds, ren, closeup(ds, {"front": "glass"}))
    both = centre_of(ds, ren, img, 35, 40)
    assert both[2] > 60 and both[0] > 60  # a mix: pale front over the blue back


def test_without_choices_the_frames_are_as_before(slabs):
    ds, _ = slabs
    spec = closeup(ds, others="grey")
    assert all(f["others"] == "grey" for f in tr.build_timeline(spec, ds)[0] if f["e"] > 0)
    assert "each" not in sb.as_legacy(dict(spec["shots"][0], each={"x": "hide"}))
    assert sb.each_of({"each": {"a": "glass", "b": "bogus"}}) == {"a": "glass"}
