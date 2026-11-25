"""3.6 phase 3: a steady legend (R1), dragging overlay items without redrawing the 3D scene, part colours (R20)."""
import numpy as np

import tomoreveal as tr
from tomoreveal import overlays as ov
from tomoreveal import storyboard as sb

from test_tidy_panels import pump, window  # noqa: F401 (fixture)


def _closeup_spec(ds, fixed, others="grey"):
    spec = tr.default_spec(ds); spec["style"].update(legend=True)
    ov.set_item(spec, "legend", fixed=fixed)
    key = ds.key_of(2)
    p = sb.new_part("focus", target={"class": key}, seconds=3, others=others); spec["shots"] = [p]
    return spec, p


def _legend_boxes(ren, plan, spec, size=(640, 360)):
    out = []
    for f in plan.frames[::3]:
        ren.draw(f, spec, size)
        r = ren.last_rects["legend"]
        if r["shown"]:
            out.append((round(r["box"][2] - r["box"][0], 3), round(r["box"][3] - r["box"][1], 3), float(f["e"])))
    return out


def test_a_fixed_legend_keeps_one_size_through_a_closeup(studio):
    ds, ren = studio.ds, studio.ren
    spec, _ = _closeup_spec(ds, fixed=True)
    boxes = _legend_boxes(ren, sb.build_plan(spec, ds), spec)
    assert any(e > 0.9 for *_, e in boxes) and any(e < 0.1 for *_, e in boxes)
    assert len({(w, h) for w, h, _ in boxes}) == 1, boxes
    spec, _ = _closeup_spec(ds, fixed=False)  # older projects: the box shrinks to the rows left, as before
    boxes = _legend_boxes(ren, sb.build_plan(spec, ds), spec)
    assert len({(w, h) for w, h, _ in boxes}) > 1


def test_leaving_rows_fade_instead_of_vanishing(studio):
    ds, ren = studio.ds, studio.ren
    spec, p = _closeup_spec(ds, fixed=True)
    plan = sb.build_plan(spec, ds)
    seen = []
    for f in plan.frames:
        ren.draw(f, spec, (640, 360))
        x0, y0, x1, y1 = (int(round(v)) for v in ren.last_rects["legend"]["box"])
        seen.append((float(f["e"]), np.asarray(ren.last_layer)[y0:y1, x0:x1, 3].astype(float).sum()))
    seen.sort()
    ink = [s for _, s in seen]
    steps = np.abs(np.diff(ink)) / max(ink)
    assert ink[-1] < ink[0] * 0.95  # the other rows are fainter in the close-up
    assert steps.max() < 0.08, f"the legend changes by {steps.max():.0%} between two frames"


def test_text_keeps_its_proportion_in_quick_frames(studio):
    ds, ren = studio.ds, studio.ren
    spec, _ = _closeup_spec(ds, fixed=True)
    f = sb.build_plan(spec, ds).frames[0]
    rel = []
    for W, H in ((960, 540), (640, 360), (480, 270)):
        ren.text_ref_h = 540
        try:
            ren.draw(f, spec, (W, H))
        finally:
            ren.text_ref_h = None
        b = ren.last_rects["legend"]["box"]
        rel.append(((b[2] - b[0]) * 960 / W, (b[3] - b[1]) * 540 / H))
    for w, h in rel[1:]:  # within 1 px of the full-size box, measured at the preview's own size
        assert abs(w - rel[0][0]) <= 1.5 and abs(h - rel[0][1]) <= 1.5, rel


def test_dragging_the_legend_moves_its_picture_without_redrawing(window):
    app, win, errs = window
    od = win.prev.overlay
    plan = win.timeline.canvas.plan
    win.frame = next(i for i, f in enumerate(plan.frames) if f["reveal"])
    win.req = {"frame": win.frame}; win.do_preview(); pump(app)
    r = od.rects()["legend"]; assert r[1], "the legend is shown"
    (x0, y0, x1, y1), _ = r; start = ((x0 + x1) / 2, (y0 + y1) / 2)
    draws = []; real = win.st.ren.draw
    win.st.ren.draw = lambda *a, **k: (draws.append(1), real(*a, **k))[1]
    try:
        from PySide6 import QtCore
        assert od.press(start, QtCore.Qt.NoModifier)
        n0 = len(draws)  # one frame without the legend
        for i in range(1, 11):
            od.move((start[0] - 0.03 * i, start[1] + 0.02 * i), QtCore.Qt.NoModifier); pump(app, 2)
        assert len(draws) == n0, "no frame is drawn while the legend moves"
        g = od.ghost; assert g is not None
        assert abs(g["at"][0] - (x0 - 0.3)) < 0.03 and abs(g["at"][1] - (y0 + 0.2)) < 0.03  # 1:1 with the mouse
        od.release(); pump(app)
    finally:
        win.st.ren.draw = real
    assert od.ghost is None and win.st.ren.hide_overlay is None
    nb = od.rects()["legend"][0]
    assert abs(nb[0] - g["at"][0]) < 0.01 and abs(nb[1] - g["at"][1]) < 0.01  # drawn where the picture was let go
    assert not errs


def test_part_stripes_have_the_timeline_colours(window):
    from tomoreveal.gui_timeline import TYPE_COLOURS
    app, win, errs = window
    for p in win.spec["shots"]:
        st = win.parts_panel.rows[p["id"]]["stripe"].styleSheet()
        assert TYPE_COLOURS[p["type"]] in st
