"""3.6 camera: reports from the user reproduced as tests (PLAN-3.6 phase 0.2)."""
import math

import numpy as np

import tomoreveal as tr
from tomoreveal import storyboard as sb


def view_axes(fr, pose):
    """(view direction centre -> camera, up) of a frame's pose, as the renderer sets the camera."""
    if pose.get("pos") is not None:
        d = np.asarray(pose["pos"], float) - np.asarray(pose["c"], float); up = np.asarray(pose["up"], float)
    else:
        d, up = (np.asarray(v, float) for v in fr.axes(pose["tilt"], pose["az"]))
    d = d / np.linalg.norm(d); up = up - d * (up @ d)
    return d, up / np.linalg.norm(up)


def angle(a, b):
    return math.degrees(math.acos(max(-1.0, min(1.0, float(a @ b)))))


def test_y_spin_after_a_top_view_turned_90_does_not_swing_round_first(studio):
    """R15/R11 (user's video, 2026-11-25): top view, Rotate 90°, slide revealing the segmentation, then an overview
    spinning around Y. The hand-over swung the picture 90° round in 0.3 s and snapped back when the spin began."""
    ds = studio.ds
    spec = tr.default_spec(ds); spec["view"].update(tilt=0.0, az=90.0)
    slide = sb.new_part("slide", seconds=1); spin = sb.new_part("overview", motion="spin", spin_axis="y", seconds=3)
    spec["shots"] = [slide, spin]
    plan = sb.build_plan(spec, ds); w, h = spec["output"]["size"]
    fr = sb.Framer(ds.shape, w / h, float(spec["style"].get("fov", 30)), bool(spec["style"].get("ortho", False)))
    ax = [view_axes(fr, f["pose"]) for f in plan.frames]
    worst = max(angle(a[1], b[1]) for a, b in zip(ax, ax[1:]))
    assert worst < 5.0, f"the picture turns {worst:.0f}° between two frames"
    lo, hi = sb.main_range(plan, spin["id"])
    first = ax[lo]
    assert angle(first[0], ax[lo - 1][0]) < 5.0 and angle(first[1], ax[lo - 1][1]) < 5.0


def test_caption_and_legend_keep_their_size_when_the_preview_plays(studio):
    """User report (2026-11-25): the caption box changes while the preview plays. Playback draws at 2/3 and 1/2 of
    the 960 x 540 preview; a 10-pixel floor on text made the caption and legend up to half as big again there. The
    window now sizes text from the still preview (ren.text_ref_h); movies keep the old rule."""
    ds, ren = studio.ds, studio.ren
    spec = tr.default_spec(ds); spec["style"].update(legend=True)
    part = sb.new_part("overview", motion="none", seconds=1, caption="Outer membrane and inner membrane"); spec["shots"] = [part]
    f = sb.build_plan(spec, ds).frames[5]
    rel = {}
    for W, H in ((960, 540), (640, 360), (480, 270)):
        ren.text_ref_h = 540  # as the window sets it around preview and playback draws
        try:
            ren.draw(f, spec, (W, H))
        finally:
            ren.text_ref_h = None
        rel[H] = {k: ((r["box"][2] - r["box"][0]) / W, (r["box"][3] - r["box"][1]) / H) for k, r in ren.last_rects.items()
                  if k in ("caption", "legend") and r.get("shown")}
    assert set(rel[540]) == {"caption", "legend"}
    for H in (360, 270):
        for k, (w, h) in rel[H].items():
            w0, h0 = rel[540][k]
            assert abs(w / w0 - 1) < 0.08 and abs(h / h0 - 1) < 0.08, (k, H, w / w0, h / h0)


import pytest  # noqa: E402

from test_tidy_panels import pump, window  # noqa: E402,F401 (fixture)


@pytest.mark.xfail(strict=True, reason="PLAN-3.6 phase 3.2 fixes this: every edit rebuilds the Structures list and the open part")
def test_an_edit_does_not_rebuild_the_panels(window):
    """User report (2026-11-25): the Movie tab's parts and the Structures tab flicker and are jittery. Every edit that
    ends in MovieEditor.commit (ticking a part, locking a view, a timeline drag, undo, any arrow change) threw away and
    rebuilt the Structures list, the arrows list and the open part's settings (twice), so they emptied and refilled."""
    import shiboken6
    from PySide6 import QtWidgets
    app, win, errs = window
    pid = win.spec["shots"][1]["id"]; win.select_part(pid); pump(app)
    before = [w for w in win.parts_panel.findChildren(QtWidgets.QComboBox)] + \
             [w for w in win.struct_grid.parentWidget().findChildren(QtWidgets.QWidget) if isinstance(w, (QtWidgets.QCheckBox, QtWidgets.QSlider))]
    assert len(before) > 10
    other = win.spec["shots"][0]["id"]
    win.parts_panel.set_on(other, False); pump(app)  # untick another part: one commit
    from PySide6 import QtCore
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)  # widgets given to deleteLater go now
    gone = [w for w in before if not shiboken6.isValid(w) or w.parent() is None]
    assert not gone, f"{len(gone)} of {len(before)} controls were thrown away and made again"
