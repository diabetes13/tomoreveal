"""Plan 3.4 phase 3: title, end and blank cards."""
import os

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import project as pj
from tomoreveal import storyboard as sb
from tomoreveal import edits


def with_cards(ds):
    spec = tr.default_spec(ds)
    n0 = len(tr.build_timeline(spec, ds)[0])
    edits.insert_part(spec, sb.new_part("title_card", title="Endolysosomes"), after="start")
    edits.insert_part(spec, sb.new_part("end_card"), after=spec["shots"][-1]["id"])
    return spec, n0


def card_frames(frames, pid):
    return [f for f in frames if f.get("card") == pid]


def test_cards_at_start_and_end(studio):
    spec, n0 = with_cards(studio.ds)
    frames, _ = tr.build_timeline(spec, studio.ds)
    title, end = spec["shots"][0], spec["shots"][-1]
    assert frames[0]["card"] == title["id"] and frames[-1]["card"] == end["id"]
    assert len(frames) == n0 + 4 * 30 + 4 * 30
    assert all(f.get("card") is None for f in frames[120:-120])
    assert sb.part_name(title) == "Card: Endolysosomes" and title["blocks"][0]["bold"]


def test_fade_in_from_black_and_the_middle_is_the_card(studio):
    spec, _ = with_cards(studio.ds)
    frames, _ = tr.build_timeline(spec, studio.ds)
    cf = card_frames(frames, spec["shots"][0]["id"])
    first = studio.ren.draw(cf[0], spec, (320, 180)).astype(int)
    mid = studio.ren.draw(cf[len(cf) // 2], spec, (320, 180))
    assert first.max() <= 255 * 1 / 19 + 1  # 0.6 s fade at 30 fps: frame 0 is nearly black
    from tomoreveal.cards import draw_card
    card, rects = draw_card(spec["shots"][0], (320, 180), studio.ren._font)
    assert np.array_equal(mid, np.asarray(card)[..., :3])
    assert rects["block:0"]["shown"] and not rects["block:1"]["shown"]  # the subtitle is empty
    assert studio.ren.draw(cf[-1], spec, (320, 180)).max() < 30  # and fades out to black


def test_cards_hide_the_legend_unless_asked(studio):
    spec, _ = with_cards(studio.ds)
    frames, _ = tr.build_timeline(spec, studio.ds)
    f = card_frames(frames, spec["shots"][-1]["id"])[60]
    studio.ren.draw(f, spec, (320, 180))
    assert not studio.ren.last_rects["legend"]["shown"] and not studio.ren.last_rects["slice_label"]["shown"]
    spec["shots"][-1]["overlays"] = {"legend": "show"}
    studio.ren.draw(f, spec, (320, 180))
    assert studio.ren.last_rects["legend"]["shown"]


def test_logo_is_drawn_with_its_transparency(studio, tmp_path):
    from PIL import Image
    logo = np.zeros((40, 40, 4), np.uint8); logo[..., 0] = 255; logo[:, :20, 3] = 255; logo[:, 20:, 3] = 128
    path = str(tmp_path / "logo.png"); Image.fromarray(logo, "RGBA").save(path)
    spec = tr.default_spec(studio.ds)
    card = sb.new_part("card", background=dict(kind="color", color="#ffffff"), fade_in=0, fade_out=0)
    card["blocks"].append(sb.image_block(path, height=0.5, x=0.5, y=0.5, anchor="middle-center"))
    edits.insert_part(spec, card, after="start")
    f = tr.build_timeline(spec, studio.ds)[0][0]
    img = studio.ren.draw(f, spec, (200, 100)).astype(int)
    x0, y0, x1, y1 = studio.ren.last_rects["block:0"]["box"]
    left = img[int(y0) + 5, int(x0) + 5]; right = img[int(y0) + 5, int(x1) - 5]
    assert tuple(left) == (255, 0, 0) and abs(right[1] - 127) <= 2 and right[0] == 255  # half see-through over white


def test_dissolve_blends_into_the_next_part(studio):
    spec = tr.default_spec(studio.ds)
    edits.insert_part(spec, sb.new_part("title_card", title="T", transition_to_next="dissolve", fade_in=0), after="start")
    frames, _ = tr.build_timeline(spec, studio.ds)
    cf = card_frames(frames, spec["shots"][0]["id"])
    last = cf[-1]; assert 0 < last["dissolve"] < 1 and last["dissolve_to"] is frames[len(cf)]
    a = studio.ren.draw(cf[len(cf) // 2], spec, (320, 180)).astype(int)
    b = studio.ren.draw(last, spec, (320, 180)).astype(int)
    c = studio.ren.draw(frames[len(cf)], spec, (320, 180)).astype(int)
    assert np.abs(b - c).mean() < np.abs(a - c).mean()  # the last card frame is close to the next part's first frame


@pytest.mark.parametrize("transparent", [False, True])
def test_each_dissolve_frame_mixes_the_card_and_the_next_frame_as_it_is_now(studio, transparent):
    """The next frame was kept from the first dissolve frame: old colours, old smoothing, and drawn see-through."""
    ds, ren = studio.ds, studio.ren; size = (320, 180)
    spec = tr.default_spec(ds); spec["style"].update(legend=False, slice_label=False, title="", bg="#ffffff")
    edits.insert_part(spec, sb.new_part("title_card", title="T", transition_to_next="dissolve", fade_in=0), after="start")
    frames, _ = tr.build_timeline(spec, ds)
    cf = [f for f in card_frames(frames, spec["shots"][0]["id"]) if 0 < f.get("dissolve", 0) < 1]
    assert len(cf) >= 3

    def check(s, aa="off"):
        q = float(s["dissolve"])
        got = ren.draw(s, spec, size, aa=aa, transparent=transparent).astype(float)
        card = ren.draw(dict(s, dissolve=0.0), spec, size, aa=aa, transparent=transparent).astype(float)
        nxt = ren.draw(s["dissolve_to"], spec, size, aa=aa, transparent=transparent).astype(float)
        assert got.shape == card.shape == nxt.shape
        assert np.abs(got - ((1 - q) * card + q * nxt)).max() <= 2
        return got
    for s in cf[::max(1, len(cf) // 3)]:
        check(s)
    s = cf[-1]; before = check(s)
    spec["style"]["invert"] = True  # a setting of the next part changes (its slice, white on black): seen at once
    assert np.abs(check(s) - before).max() > 30
    check(s, "ssaa")  # Off, then Best at the same size: Best's edges


def test_project_keeps_card_pictures(studio, tmp_path):
    from PIL import Image
    path = str(tmp_path / "logo.png"); Image.new("RGBA", (10, 10), (0, 0, 255, 255)).save(path)
    spec = tr.default_spec(studio.ds)
    card = sb.new_part("end_card"); card["blocks"].append(sb.image_block(path)); edits.insert_part(spec, card)
    p = str(tmp_path / "p.tomoreveal")
    pj.write_atomic(p, pj.with_file_facts(pj.make_project(pj.source_state(studio), spec), p))
    back = pj.read_project(p)
    assert back["spec"]["shots"][-1]["blocks"][-1]["path"] == path
    assert any(s["kind"] == "image" and s["path"] == path for s in back["sources"])
    os.remove(path)
    problems = [r for r in pj.resolve_sources(back, p) if r["status"] != "ok"]
    assert [(r["entry"]["kind"], r["status"]) for r in problems] == [("image", "missing")]
    # located somewhere else: the card follows
    os.makedirs(tmp_path / "pics"); new = str(tmp_path / "pics" / "logo2.png"); Image.new("RGBA", (10, 10)).save(new)
    res = pj.resolve_sources(back, p)
    for r in res:
        if r["entry"]["kind"] == "image":
            r["path"] = new
    assert pj.project_spec(back, res)["shots"][-1]["blocks"][-1]["path"] == new


def test_window_adds_cards(tmp_path, demo):
    QtCore = pytest.importorskip("PySide6.QtCore")
    import argparse
    import sys
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    errs = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errs.append(v)
    try:
        loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
        for _ in range(40):
            app.processEvents()
        if win.st.ren is None:
            pytest.skip("3D rendering is not available here")
        for _ in range(2):
            win.editor.add("title_card"); win.editor.add("end_card")
            for _ in range(10):
                app.processEvents()
            shots = win.spec["shots"]
            assert shots[0]["type"] == "card" and shots[0]["blocks"][0]["text"] == "demo_tomogram"
            assert shots[-1]["type"] == "card" and shots[-1]["blocks"][0]["text"] == "Thank you"
            win.parts_panel.open(shots[0]["id"])  # the card's settings build without errors
            win.prev_timer.stop(); win.do_preview()
            win.editor.undo(); win.editor.undo()
        assert not errs, errs
    finally:
        sys.excepthook = old; win.close()
