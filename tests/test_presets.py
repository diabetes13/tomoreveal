"""3.5: more looks (Soft on black, Ceramic), outlines of see-through membranes at their outer edge only, and the
Presets tab (pictures of every look, the user's own saved looks)."""
import numpy as np
import pytest
from scipy import ndimage

import tomoreveal as tr
from tomoreveal import look as lk
from test_see_through import BACK, FRONT, slabs  # noqa: F401 (fixture)
from test_tidy_panels import pump, window  # noqa: F401 (fixture)


def test_every_look_has_a_note_and_new_settings_default_to_the_old_drawing():
    assert {d for _, d in lk.LOOKS} == set(lk.LOOK_PRESETS) == set(lk.LOOK_NOTES)
    old = lk.style35({"look": {"style35": dict(preset="glass", glass=True)}})
    assert old["glass_outline"] == "all" and old["material"] == "normal" and not old["pastel"] and old["membrane_colour"] is None
    spec = {}; lk.apply_look(spec, "ceramic")
    assert spec["style"]["bg"] == "#ffffff" and spec["look"]["style35"]["membrane_colour"] == lk.CREAM
    lk.set_look(spec, background="black")
    assert spec["style"]["bg"] == "#000000" and spec["look"]["style35"]["preset"] == "custom"
    assert lk.membrane_look(lk.style35(spec)) == "cream"


def glass_frame(ds, ren, edges):
    """Both slabs are membranes: where the front one overlaps the back one there is an edge inside the glass."""
    spec = tr.default_spec(ds)
    spec["style"].update(legend=False, slice_label=False, ortho=True); spec["view"].update(tilt=0, az=0)
    k1, k2 = ds.key_of(1), ds.key_of(2)
    spec["classes"][k1].update(color=FRONT, membrane=True); spec["classes"][k2].update(color=BACK, membrane=True)
    lk.apply_look(spec, "glass"); lk.set_look(spec, outline="thick", between=True, glass_outline=edges, depth_fade="off", shadow="none")
    spec["look"]["lighting"] = {"preset": "flat"}
    spec["shots"] = [tr.storyboard.new_part("overview", seconds=0.1, motion="none")]
    f = tr.build_timeline(spec, ds)[0][0]
    return ren.draw(f, spec, (320, 320)).astype(int)


def test_see_through_membranes_can_keep_only_their_outer_edge(slabs):
    ds, ren = slabs
    imgs = {e: glass_frame(ds, ren, e) for e in ("all", "outer", "none")}
    bgd = imgs["none"].sum(2) >= 3 * 250  # the white background
    dark = {e: (np.abs(im - imgs["none"]).sum(2) > 30) for e, im in imgs.items()}  # outline pixels
    assert dark["none"].sum() == 0 and dark["all"].sum() > dark["outer"].sum() > 0
    # the outer edge stays: every outline pixel of "outer" lies next to the background
    near_bg = ndimage.binary_dilation(bgd, iterations=4)
    assert (dark["outer"] & ~near_bg).sum() <= 0.05 * dark["outer"].sum()
    assert (dark["all"] & ~near_bg).sum() > 50  # "all": also the line where one membrane overlaps the other


def test_ceramic_draws_membranes_cream_and_the_legend_follows(studio):
    ds, ren = studio.ds, studio.ren
    spec = tr.default_spec(ds); lk.apply_look(spec, "ceramic")
    f = tr.build_timeline(spec, ds)[0][-1]
    ren.draw(f, spec, (320, 180))
    mem = next(kk for kk, c in ds.classes.items() if c["name"] == "membrane")
    other = next(kk for kk, c in ds.classes.items() if c["name"] == "filaments")
    assert np.allclose(ren._legend_col[mem], lk.hex2rgb(lk.CREAM) if hasattr(lk, "hex2rgb") else ren._legend_col[mem])
    base = tr.render.hex2rgb(ds.classes[other]["color"])
    assert all(p >= b - 1e-9 for p, b in zip(ren._legend_col[other], base)) and ren._legend_col[other] != tuple(base)  # paler
    assert ren.actors[mem].GetProperty().GetSpecularPower() == 6


def test_presets_tab_pictures_and_saved_looks(window):
    from tomoreveal import gui_presets as gp
    app, win, errs = window
    win.tabs.setCurrentIndex(win.TAB_PRESETS); pump(app)
    win.presets.refresh(force=True)
    while win.presets.timer.isActive():
        app.processEvents()
    assert all(not b.icon().isNull() for b in win.presets.cards.values())
    win.presets.use_builtin("dark"); pump(app)
    assert win.spec["style"]["bg"] == "#000000" and win.look35.preset.currentData() == "dark"
    assert win.look35.bg.currentData() == "black" and win.bg_cb.currentData() == "black"
    lk.set_look(win.spec, outline="thick"); win.spec["look"].setdefault("vignette", {})["when"] = "always"
    assert win.presets.save("Mine")
    assert ("mine", "Mine") in win.presets.cards and "Mine" in gp.saved_presets()
    win.presets.use_builtin("soft"); pump(app)
    win.presets.use_saved("Mine"); pump(app)
    assert lk.style35(win.spec)["outline"] == "thick" and win.spec["look"]["vignette"]["when"] == "always"
    assert win.spec["style"]["bg"] == "#000000"
    win.presets.delete("Mine"); win.presets.delete("Mine")  # twice: the list is rebuilt each time
    assert "Mine" not in gp.saved_presets() and ("mine", "Mine") not in win.presets.cards
    assert not errs
