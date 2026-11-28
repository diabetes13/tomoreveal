"""One depth for every reader of the depth buffer: depth fade, background blur, arrows "behind", the camera path in
the preview and framing a region. The hidden outline pass draws over the depth buffer, and with Best (SSAA) VTK never
writes the scene's depth into the window's depth buffer at all."""
import numpy as np
import pytest
from scipy import ndimage

import tomoreveal as tr
from tomoreveal import annotations as an
from tomoreveal import look as lk
from tomoreveal import storyboard as sb


def main_frames(spec, ds, kind="overview"):
    return [f for f in sb.build_plan(spec, ds).frames if f["phase"] == "main" and f.get("type") == kind]


def soft(ds, **kw):
    spec = tr.default_spec(ds); spec["style"].update(legend=False, slice_label=False)
    lk.apply_look(spec, "soft"); lk.set_look(spec, **kw)
    return spec


@pytest.mark.parametrize("outline", ["thin", "off"])
def test_a_region_over_the_slice_alone_is_framed(studio, outline):
    ds = studio.ds; spec = soft(ds, outline=outline)
    for c in spec["classes"].values():
        c["show"] = False  # nothing but the slice
    f = main_frames(spec, ds)[0]
    studio.ren.draw(f, spec, (480, 270), annotate=False)
    assert studio.ren.region_box(0.4, 0.4, 0.6, 0.6) is not None


@pytest.mark.parametrize("look", ["xray", "glass"])
def test_a_region_on_see_through_structures_is_framed(studio, look):
    ds = studio.ds; spec = tr.default_spec(ds); spec["style"].update(legend=False, slice_label=False)
    lk.apply_look(spec, look)
    if look == "glass":
        for c in spec["classes"].values():
            c["membrane"] = True  # every structure is glass
    f = dict(main_frames(spec, ds)[0], slice=0.0)  # the slice hidden: only see-through structures are there
    studio.ren.draw(f, spec, (480, 270), annotate=False)
    box = studio.ren.region_box(0.3, 0.3, 0.7, 0.7)
    assert box is not None
    lo, hi = box
    assert np.all(hi - lo > 5)


def arrow_under_the_slice(ds, outline, blur=False):
    """An arrow whose tip lies under the opaque slice, seen from above, where no structure is above it."""
    a = an.new_arrow([145.0, 177.0, 1.0], None, None, "under")
    spec = soft(ds, outline=outline); spec["annotations"] = [a]; spec["view"].update(tilt=0)
    spec["shots"] = [sb.new_part("overview", motion="none", seconds=2)]
    if blur:  # background blur is drawn in close-ups only: a close-up of a region around the tip
        spec["look"]["vignette"] = dict(spec["look"].get("vignette") or {}, when="never", blur=dict(on=True, strength=0.5))
        spec["shots"] = [sb.new_part("focus", target=sb.make_target([]), region=dict(lo=[117, 150, 0], hi=[173, 205, 30]),
                                     motion="none", seconds=2, caption="")]
    return a, spec


@pytest.mark.parametrize("outline,blur", [("thin", False), ("off", False), ("thin", True), ("off", True)])
def test_an_arrow_behind_the_slice_is_hidden_with_and_without_outlines(studio, outline, blur):
    ds = studio.ds; a, spec = arrow_under_the_slice(ds, outline, blur)
    f = dict(main_frames(spec, ds, "focus" if blur else "overview")[-1], k=24)  # the slice half way up, above the tip
    assert f["ann"].get(a["id"], [0])[0] > 0.5 and f["slice"] > 0.99
    studio.ren.draw(f, spec, (320, 180)); assert f"ann:{a['id']}" in studio.ren.last_rects  # "normal": drawn
    a["behind"] = "hide"
    studio.ren.draw(f, spec, (320, 180)); assert f"ann:{a['id']}" not in studio.ren.last_rects


@pytest.mark.parametrize("outline", ["thin", "off"])
def test_depth_fade_is_the_same_in_every_smoothing_mode_from_the_first_draw(studio, outline):
    ds = studio.ds; spec = soft(ds, outline=outline, shadow="none")
    plain = soft(ds, outline=outline, shadow="none", depth_fade="off")
    f = main_frames(spec, ds)[0]
    other = dict(f, pose=dict(f["pose"], dist=2.5 * f["pose"]["dist"]), k=2)  # a frame with another depth everywhere
    faded = {}
    for aa in ("off", "fxaa", "ssaa"):
        studio.ren.draw(other, plain, (320, 180), aa="off")  # another frame first, in the window's depth buffer
        a = studio.ren.draw(f, spec, (320, 180), aa=aa).astype(int)
        studio.ren.draw(other, plain, (320, 180), aa="off")
        b = studio.ren.draw(f, plain, (320, 180), aa=aa).astype(int)
        faded[aa] = np.abs(a - b).sum(2) > 12
        if aa == "off":
            p = b.sum(2)  # edges of the picture: there smoothing itself changes pixels (measured 2.4 to 3.6 %)
            edge = (np.abs(np.diff(p, axis=0, prepend=p[:1])) > 40) | (np.abs(np.diff(p, axis=1, prepend=p[:, :1])) > 40)
            away = ~ndimage.binary_dilation(edge)
    n = faded["off"].sum()
    assert n > 1000
    for aa in ("fxaa", "ssaa"):
        assert abs(int(faded[aa].sum()) - int(n)) <= 0.03 * n, (aa, int(faded[aa].sum()), int(n))  # Best was 4,351 of 9,717
        assert ((faded[aa] ^ faded["off"]) & away).sum() <= 0.002 * n, aa
