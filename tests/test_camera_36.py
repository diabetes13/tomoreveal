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


# ---- phase 1: steady spins, rotation centre, screen axis, gentle start, longer flights ----

def _framer(spec, ds):
    w, h = spec["output"]["size"]
    return sb.Framer(ds.shape, w / h, float(spec["style"].get("fov", 30)), bool(spec["style"].get("ortho", False)))


def _main(plan, part):
    lo, n = sb.main_range(plan, part["id"])
    return plan.frames[lo:lo + n]


def test_new_overviews_keep_one_size_while_they_turn(studio):
    ds = studio.ds
    for motion in ("spin", "rock"):
        spec = tr.default_spec(ds); p = sb.new_part("overview", motion=motion, seconds=4); spec["shots"] = [p]
        d = [f["pose"]["dist"] for f in _main(sb.build_plan(spec, ds), p)]
        assert max(d) / min(d) - 1 < 1e-4, motion
        spec["shots"] = [sb.as_legacy(p)]  # saved before 3.6: refitted every frame, as before
        d = [f["pose"]["dist"] for f in _main(sb.build_plan(spec, ds), spec["shots"][0])]
        assert max(d) / min(d) - 1 > 0.05, motion


def test_turning_around_the_middle_of_what_is_shown(studio):
    ds = studio.ds; nz, ny, nx = ds.shape
    spec = tr.default_spec(ds)
    spec["clip"] = dict(on=True, x=[10, nx // 3], y=[20, ny // 2], z=[0, nz - 1], fit=False)
    cb = sb.clip_box(spec, ds.shape); mid = (np.array(cb[0], float) + np.array(cb[1], float)) / 2
    p = sb.new_part("overview", motion="spin", seconds=2); spec["shots"] = [p]
    assert all(np.allclose(f["pose"]["c"], mid) for f in _main(sb.build_plan(spec, ds), p))
    spec["shots"] = [sb.as_legacy(p)]  # old parts: the whole volume's middle
    vol = np.array([nx - 1, ny - 1, nz - 1], float) / 2
    assert all(np.allclose(f["pose"]["c"], vol) for f in _main(sb.build_plan(spec, ds), spec["shots"][0]))


def test_spin_around_the_screens_vertical_after_a_drag(studio):
    ds = studio.ds
    spec = tr.default_spec(ds); spec["view"].update(tilt=55.0, az=40.0)  # as after dragging the view
    p = sb.new_part("overview", motion="spin", spin_axis="screen", seconds=3); spec["shots"] = [p]
    fr = _framer(spec, ds); main = _main(sb.build_plan(spec, ds), p)
    ax = [view_axes(fr, f["pose"]) for f in main]
    up0 = ax[0][1]
    assert all(angle(up, up0) < 0.5 for _, up in ax)  # the screen's vertical does not move
    assert all(np.allclose(f["pose"]["c"], main[0]["pose"]["c"]) for f in main)
    assert angle(ax[0][0], ax[-1][0]) < 1.0  # ends where it began
    assert max(angle(ax[0][0], d) for d, _ in ax) > 170  # and really went round


def test_rocks_and_spins_start_gently(studio):
    ds = studio.ds
    spec = tr.default_spec(ds); p = sb.new_part("overview", motion="rock", angle=30, seconds=4); spec["shots"] = [p]
    az = [f["pose"]["az"] for f in _main(sb.build_plan(spec, ds), p)]
    speed = np.abs(np.diff(az))
    assert speed[0] < 0.1 * speed.max()


def test_a_large_turn_gets_a_longer_flight(studio):
    ds = studio.ds
    spec = tr.default_spec(ds)
    a = sb.new_part("overview", motion="none", seconds=1, view=dict(tilt=42.0, az=0.0, zoom=1.0))
    b = sb.new_part("overview", motion="none", seconds=1, view=dict(tilt=42.0, az=170.0, zoom=1.0))
    spec["shots"] = [a, b]; plan = sb.build_plan(spec, ds); fps = plan.fps
    n = sum(1 for f in plan.frames if f.get("part") == b["id"] and f["phase"] == "transition")
    assert abs(n - round(fps * sb.auto_flight_seconds(dict(az=0.0, tilt=42.0), dict(az=170.0, tilt=42.0)))) <= 1
    assert n > fps * 1.5
    spec["shots"] = [a, sb.as_legacy(b)]; plan = sb.build_plan(spec, ds)
    assert sum(1 for f in plan.frames if f.get("part") == b["id"] and f["phase"] == "transition") == round(fps * sb.OTHER_TRANSITION)


def test_one_writer_of_the_camera():
    """camera.pose_camera is the only way the VTK camera is set (render._set_camera; the shadow self-test has its own)."""
    import re
    from pathlib import Path
    pkg = Path(tr.__file__).parent
    hits = []
    for f in pkg.glob("*.py"):
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if re.search(r"(cam|camera)\.(position|focal_point|parallel_scale|view_angle)\s*=(?!=)|camera_position\s*=", line):
                hits.append(f"{f.name}:{i}")
    assert all(h.startswith(("render.py", "shadows.py")) for h in hits), hits
    assert sum(h.startswith("render.py") for h in hits) <= 3  # position, focal point and up on one line; scale; angle


def test_the_preview_path_line_uses_the_movies_shape(studio):
    """The camera path drawn in the preview used a fixed 16:9 framer: wrong for portrait movies (audit, phase 0.3)."""
    from tomoreveal.paths import PATH_CAMERA
    ds, ren = studio.ds, studio.ren
    spec = tr.default_spec(ds); spec["output"]["size"] = [1080, 1920]
    nz, ny, nx = ds.shape
    p = sb.new_part("path", seconds=3, source=dict(kind="points"),
                    waypoints=[dict(point=[nx * 0.3, ny * 0.3, nz / 2]), dict(point=[nx * 0.7, ny * 0.6, nz / 2])])
    spec["shots"] = [p]; f = sb.build_plan(spec, ds).frames[3]
    ren.preview_part = p["id"]
    try:
        ren.draw(f, spec, (270, 480), preview=True)
        raws = ren._path3d["raw"]
    finally:
        ren.preview_part = None
    md, _ = _framer(spec, ds).axes(float(spec["view"]["tilt"]), float(spec["view"]["az"]))
    D = 60.0 * float(PATH_CAMERA["distance"])
    for w, P in zip(p["waypoints"], raws):
        assert np.allclose(P, np.asarray(w["point"], float) + D * np.asarray(md)), "path line not in the movie's framing"


def test_typed_numbers_move_the_sliders(window):
    """R10: every slider's value can be typed; clip boxes take voxels or nm."""
    from PySide6 import QtWidgets
    from tomoreveal.gui_numbers import ClipNumbers, NumberEdit, nearest_step, to_voxels
    app, win, errs = window
    edit = win.tilt_s._val
    assert isinstance(edit, NumberEdit)
    edit.setText("30"); edit.apply(); pump(app)
    assert win.tilt_s.value() == 30 and abs(float(win.spec["view"]["tilt"]) - 30) < 1e-6
    edit.setText("500°"); edit.apply(); pump(app)
    assert win.tilt_s.value() == win.tilt_s.maximum()  # clamped
    zoom = win.zoom_s._val; zoom.setText("2.5x"); zoom.apply(); pump(app)
    assert abs(float(win.spec["view"]["zoom"]) - 2.5) < 0.02
    assert to_voxels("120 nm", 4.11) == 29 and to_voxels("120", 4.11) == 120
    clip = win.clip_s["x"]._val; assert isinstance(clip, ClipNumbers)
    clip.lo.setText("20"); clip.hi.setText("100"); clip._typed(); pump(app)
    assert win.spec["clip"]["x"] == [20, 100]
    s = QtWidgets.QSlider(); s.setRange(0, 100)
    assert nearest_step(s, lambda v: f"{v / 100:.2f}x", 0.333) == 33
    assert not errs
