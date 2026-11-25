"""3.6 phase 7 (R13): Blur the background, a row of its own in every close-up (no need to open Vignette)."""
import numpy as np
from PySide6 import QtWidgets

import tomoreveal as tr
from tomoreveal import storyboard as sb

from test_arrows import window  # noqa: F401 (fixture)


def _closeup_frame(ds, spec):
    part = sb.new_part("focus", target={"items": [{"structure": ds.key_of(2), "component": 1}]}, motion="none", seconds=1)
    spec["shots"] = [part]
    return part, [g for g in tr.build_timeline(spec, ds)[0] if g["e"] >= 0.999][-1]


def test_the_row_draws_like_the_look_tab_blur(studio):
    """Medium blur from the close-up's own row = the Look tab's blur at the same strength, and the vignette is not changed."""
    ds, ren = studio.ds, studio.ren
    spec = tr.default_spec(ds); spec["style"].update(legend=False, slice_label=False)
    part, f = _closeup_frame(ds, spec)
    spec["look"] = {"vignette": {"when": "never", "blur": {"on": True, "strength": 0.5}}}
    look_tab = ren.draw(f, spec, (320, 180))
    spec["look"] = {"vignette": {"when": "never"}}
    part["vignette"] = {"on": "default", "blur": {"on": True, "strength": 0.5}}
    own = ren.draw(f, spec, (320, 180))
    assert np.array_equal(look_tab, own)
    part["vignette"] = None; plain = ren.draw(f, spec, (320, 180))
    assert np.abs(plain.astype(int) - own).sum(2).astype(bool).mean() > 0.2  # it does blur
    spec["look"] = {"vignette": {"when": "closeups", "strength": 0.8}}  # the Look tab's vignette stays on with the row's blur
    part["vignette"] = {"on": "default", "blur": {"on": False}}
    a = ren.draw(f, spec, (320, 180)); part["vignette"] = None; b = ren.draw(f, spec, (320, 180))
    assert np.array_equal(a, b)
    ren.blur_cap = 0.25  # preview playback: the light level
    part["vignette"] = {"on": "default", "blur": {"on": True, "strength": 0.85}}; capped = ren.draw(f, spec, (320, 180))
    part["vignette"] = {"on": "default", "blur": {"on": True, "strength": 0.25}}; ren.blur_cap = None
    assert np.array_equal(capped, ren.draw(f, spec, (320, 180)))


def test_old_closeup_blur_settings_draw_the_same(studio):
    ds, ren = studio.ds, studio.ren
    spec = tr.default_spec(ds); spec["style"].update(legend=False, slice_label=False)
    part, f = _closeup_frame(ds, spec)
    part["vignette"] = dict(on=True, strength=0.3, blur=dict(on=True))  # as 3.5 wrote it (vignette on + blur box)
    a = ren.draw(f, spec, (320, 180))
    spec["look"] = {"vignette": {"blur": {"strength": 0.5}}}
    assert np.array_equal(a, ren.draw(f, spec, (320, 180)))


def _blur_combo(win):
    found = [c for c in win.findChildren(QtWidgets.QComboBox) if c.findData("medium") >= 0 and c.findData("strong") >= 0]
    assert found, [c.toolTip()[:40] for c in win.parts_panel.findChildren(QtWidgets.QComboBox)]
    return found[-1]


def test_the_row_in_the_closeup_settings(window):
    app, win, errs = window
    ds = win.st.ds
    part = sb.new_part("focus", target={"items": [{"structure": ds.key_of(2), "component": 1}]}, caption="")
    win.editor.insert(part); pid = part["id"]; win.parts_panel.open(pid)
    for _ in range(5):
        app.processEvents()
    cb = _blur_combo(win); assert cb.currentData() == "default"
    j = cb.findData("medium"); cb.setCurrentIndex(j); cb.activated.emit(j)
    for _ in range(5):
        app.processEvents()
    p = win.parts_panel.part(pid)
    assert p["vignette"] == {"on": "default", "blur": {"on": True, "strength": 0.5}}
    cb = _blur_combo(win); assert cb.currentData() == "medium"
    j = cb.findData("strong"); cb.setCurrentIndex(j); cb.activated.emit(j)
    assert win.parts_panel.part(pid)["vignette"]["blur"] == {"on": True, "strength": 0.85}
    cb = _blur_combo(win); j = cb.findData("default"); cb.setCurrentIndex(j); cb.activated.emit(j)
    assert win.parts_panel.part(pid).get("vignette") is None  # back to the Look tab: nothing of its own left
    win.parts_panel.part(pid)["vignette"] = dict(on=True, strength=0.3, blur=dict(on=True, strength=0.4))  # an older own setting
    win.parts_panel.fill_details(pid)
    for _ in range(3):
        app.processEvents()
    cb = _blur_combo(win); assert cb.currentData() == "own" and "40 %" in cb.currentText()
    j = cb.findData("off"); cb.setCurrentIndex(j); cb.activated.emit(j)
    assert win.parts_panel.part(pid)["vignette"] == dict(on=True, strength=0.3, blur=dict(on=False))  # its vignette kept
    assert not errs, errs
