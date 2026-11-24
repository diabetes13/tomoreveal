"""3.5: one label pointing at several things (arrows and text boxes with extra pointers), and caption options."""
import numpy as np

import tomoreveal as tr
from tomoreveal import annotations as an
from tomoreveal import overlays as ov
from tomoreveal import storyboard as sb
from test_arrows import main, orbit_spec
from test_tidy_panels import pump, window  # noqa: F401 (fixture)


def centre(ds, comp):
    lo, hi = ds.target_box({"class": ds.key_of(2), "component": comp}, pad=False)
    return (np.asarray(lo) + hi) / 2


def test_arrow_points_at_several_objects(studio):
    ds, ren = studio.ds, studio.ren
    c1, c2, c3 = centre(ds, 1), centre(ds, 2), centre(ds, 3)
    a = an.new_arrow(c1, ds.key_of(2), 1, "vesicles")
    an.add_pointer(a, c2, ds.key_of(2), 2); an.add_pointer(a, c3, ds.key_of(2), 3)
    assert len(an.pointers(a)) == 2
    spec, part = orbit_spec(ds, a, motion="none")
    f = main(spec, ds)[20]
    plain_spec, _ = orbit_spec(ds, motion="none")
    with_ptr = ren.draw(f, spec, (480, 270)).astype(int)
    for j, c in enumerate((c2, c3)):
        box = ren.last_rects[f"anntip:{a['id']}:{j}"]["box"]
        (fx, fy), ok = ren.project(c); assert ok
        assert abs((box[0] + box[2]) / 2 - fx * 480) <= 1 and abs((box[1] + box[3]) / 2 - fy * 270) <= 1
    plain = ren.draw(main(plain_spec, ds)[20], plain_spec, (480, 270)).astype(int)
    for c in (c2, c3):  # something is drawn at each extra tip
        (fx, fy), _ = ren.project(c); x, y = int(fx * 480), int(fy * 270)
        assert np.abs(with_ptr[y - 3:y + 4, x - 3:x + 4] - plain[y - 3:y + 4, x - 3:x + 4]).sum() > 0


def test_text_box_with_leader_lines(studio):
    ds, ren = studio.ds, studio.ren
    t = an.new_text("Vesicles", x=0.05, y=0.05)
    an.add_pointer(t, centre(ds, 1), ds.key_of(2), 1); an.add_pointer(t, centre(ds, 2), ds.key_of(2), 2)
    spec, part = orbit_spec(ds, t, motion="none")
    f = main(spec, ds)[20]
    ren.draw(f, spec, (480, 270))
    box = ren.last_rects[f"ann:{t['id']}"]["box"]
    assert box[2] - box[0] < 200  # the text box only, not the leaders
    assert f"anntip:{t['id']}:0" in ren.last_rects and f"anntip:{t['id']}:1" in ren.last_rects


def test_captions_can_fade_and_have_other_boxes(studio):
    ds, ren = studio.ds, studio.ren
    spec = tr.default_spec(ds)
    a = sb.new_part("overview", motion="none", seconds=2, caption="First"); b = sb.new_part("overview", motion="none", seconds=2, caption="Second")
    spec["shots"] = [a, b]
    fr = sb.build_plan(spec, ds).frames
    assert all("cap_a" not in f for f in fr)  # as before unless asked for
    ov.set_item(spec, "caption", fade=0.4)
    fr = sb.build_plan(spec, ds).frames
    caps = [(f["caption"], f.get("cap_a", 1.0)) for f in fr if f["caption"]]
    assert caps[0][1] < 0.5 and max(q for _, q in caps) == 1.0
    firsts = [q for c, q in caps if c == "First"]; assert firsts[-1] < 0.5  # fades out before the next caption
    f = next(g for g in fr if g["caption"] == "First" and g.get("cap_a") is None)
    boxes = {}
    for box in ("dark", "light", "outline", "none"):
        ov.set_item(spec, "caption", box=box, bold=box == "none")
        boxes[box] = ren.draw(f, spec, (480, 270)).astype(int)
    r = [int(round(v)) for v in ren.last_rects["caption"]["box"]]
    crops = {k: im[r[1]:r[3], r[0]:r[2]] for k, im in boxes.items()}
    assert len({int(c.sum()) for c in crops.values()}) == 4  # four different looks


def test_point_at_more_things_in_the_window(window):
    app, win, errs = window
    from tomoreveal import gui_annotations as ga
    ds = win.st.ds; win.tabs.setCurrentIndex(win.TAB_LOOK); win.show(); pump(app)
    win.req = win._main_req(); win.prev_timer.stop(); win.do_preview(); pump(app)
    f = next((fx, 0.55) for fx in np.linspace(0.3, 0.7, 17) if win.st.ren.pick_point(fx, 0.55) is not None)
    a = ga.add_arrow_at(win, f); pump(app)
    if win.label_editor is not None:
        win.label_editor.hide()
    ga.start_add_pointer(win, a["id"]); assert win.arrow_pick == a["id"]
    g = next((fx, 0.5) for fx in np.linspace(0.7, 0.3, 17) if win.st.ren.pick_point(fx, 0.5) is not None)
    win.on_click(*g); pump(app)
    assert len(an.pointers(an.find(win.spec, a["id"]))) == 1 and win.arrow_pick == a["id"]
    win.on_click(0.001, 0.001); pump(app)  # empty space: done
    assert win.arrow_pick is False
    win.ann_panel.rebuild(); win.ann_panel.rebuild(); pump(app)  # twice: rows are replaced cleanly
    assert win.ann_panel.rows.count() >= 1
    assert not errs
