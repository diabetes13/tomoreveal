"""3.6 phase 6 (R12, R17): shapes drawn with the mouse (curved and freehand arrows, lines, ellipses, rectangles) and
arrows, text and labels as separate transparent layer movies that lay back into the merged movie."""
import json
import math
import os

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import annotations as an
from tomoreveal import drawing, layers
from tomoreveal import storyboard as sb


# ---- geometry (no 3D needed) --------------------------------------------------------------------------------------
def _off_line(pts, line):
    """Largest distance (px) from any of pts to the polyline."""
    a, b = line[:-1], line[1:]; ab = b - a; L2 = np.maximum((ab ** 2).sum(1), 1e-12)
    worst = 0.0
    for p in pts:
        t = np.clip(((p - a) * ab).sum(1) / L2, 0, 1); q = a + ab * t[:, None]
        worst = max(worst, float(np.hypot(*(q - p).T).min()))
    return worst


def test_a_freehand_stroke_is_simplified():
    t = np.linspace(0, 2 * math.pi, 200)
    raw = np.column_stack([200 + 120 * np.cos(t), 150 + 60 * np.sin(1.5 * t)])
    raw += np.random.default_rng(1).normal(0, 0.3, raw.shape)  # a slightly shaky hand
    kept = drawing.simplify(raw)
    assert len(kept) < 40 and _off_line(raw, kept) < 2.0  # the kept points stay within 2 px of the stroke
    assert _off_line(raw, drawing.smooth(kept)) < 2.0  # and so does the smooth curve drawn through them


def test_the_curve_handle_moves_the_middle():
    p0, c, p1 = (0.0, 0.0), (50.0, 40.0), (100.0, 0.0)
    m = drawing.bezier_middle(p0, c, p1); assert np.allclose(m, (50, 20))
    curve = drawing.bezier(p0, c, p1, n=49); assert np.allclose(curve[24], m)
    c2 = drawing.control_for_middle(p0, m + (7.0, -12.0), p1)
    assert np.allclose(drawing.bezier_middle(p0, c2, p1), m + (7.0, -12.0))  # the middle goes where the handle goes


def test_snapping_and_drag_one_end():
    q = drawing.snap_angle((0, 0), (10, 1.2)); assert abs(q[1]) < 1e-9 and abs(q[0] - math.hypot(10, 1.2)) < 1e-9
    q = drawing.snap_angle((0, 0), (10, 9)); assert abs(math.degrees(math.atan2(q[1], q[0])) - 45) < 1e-6
    P = np.array([(0.0, 0.0), (5.0, 3.0), (10.0, 0.0)])
    Q = drawing.similarity(P, P[0], P[-1], (0.0, 10.0))  # turn the stroke a quarter round about its start
    assert np.allclose(Q[0], P[0]) and np.allclose(Q[-1], (0, 10)) and np.allclose(np.hypot(*(Q[1] - Q[0])), np.hypot(5, 3))


def test_old_arrows_stay_as_they_were():
    a = an.new_arrow([1, 2, 3]); assert an.layer_of(a) == "arrows" and not an.is_drawn(a)
    spec = {"annotations": [a, {"kind": "curve", "points": [[0.1, 0.1]]}, an.new_text("x")]}
    assert [b["kind"] for b in an.items(spec)] == ["arrow", "text"]  # a broken shape is left out, not drawn


def test_layer_alpha_maths():
    rng = np.random.default_rng(0)
    base = rng.integers(0, 255, (8, 8, 3)).astype(np.uint8)
    top = rng.integers(0, 255, (8, 8, 4)).astype(np.uint8)
    pm = layers.premul(top); back = layers.straight(pm)
    ok = top[..., 3] > 20
    assert np.abs(back[ok].astype(int) - top[ok]).max() <= 2
    want = np.round(top[..., :3] * (top[..., 3:4] / 255.0) + base * (1 - top[..., 3:4] / 255.0))
    assert np.abs(layers.composite(base, [top]).astype(int) - want).max() <= 1


# ---- drawing in frames ---------------------------------------------------------------------------------------------
def _one_part(ds, *anns, motion="none", seconds=2):
    spec = tr.default_spec(ds); spec["style"].update(legend=False, slice_label=False)
    spec["shots"] = [sb.new_part("overview", motion=motion, seconds=seconds)]
    spec["annotations"] = list(anns)
    return spec


def _main(spec, ds):
    return [f for f in sb.build_plan(spec, ds).frames if f["phase"] == "main"]


def test_drawn_shapes_show_and_a_pinned_curve_follows_its_spot(studio):
    ds, ren = studio.ds, studio.ren
    lo, hi = ds.target_box({"class": ds.key_of(2), "component": 1}, pad=False); c = (np.asarray(lo) + hi) / 2
    free = an.new_drawn("freehand", [(0.1, 0.8), (0.2, 0.7), (0.3, 0.75), (0.35, 0.6)])
    curve = an.new_drawn("curve", [(0.6, 0.2), (0.7, 0.15), (0.5, 0.5)], anchor=dict(xyz=[float(v) for v in c], structure=ds.key_of(2), component=1))
    ell = an.new_drawn("ellipse", [(0.7, 0.7), (0.9, 0.9)]); box = an.new_drawn("rect", [(0.05, 0.05), (0.2, 0.2)])
    spec = _one_part(ds, free, curve, ell, box, motion="spin", seconds=3); spec["style"]["bg"] = "#000000"
    w, h = 320, 180; offs = []
    for f in _main(spec, ds)[20::12]:
        img = ren.draw(f, spec, (w, h))
        for a in (free, curve, ell, box):
            assert f"ann:{a['id']}" in ren.last_rects, a["kind"]
        (fx, fy), ok = ren.project(c); assert ok
        tb = ren.last_rects[f"anntip:{curve['id']}"]["box"]; tx, ty = (tb[0] + tb[2]) / 2, (tb[1] + tb[3]) / 2
        assert abs(tx - fx * w) <= 1 and abs(ty - fy * h) <= 1  # the tip stays on the vesicle
        tl = ren.last_rects[f"anntail:{curve['id']}"]["box"]; offs.append(((tl[0] + tl[2]) / 2 - tx, (tl[1] + tl[3]) / 2 - ty))
        x0, y0, x1, y1 = (int(v) for v in ren.last_rects[f"ann:{box['id']}"]["box"])
        assert img[max(0, y0):y1, max(0, x0):x1].max() > 200  # a white rectangle on black
    assert np.ptp(np.array(offs), axis=0).max() <= 1.0  # the curve keeps its shape on screen while the camera turns


def test_draw_on_grows_the_shape(studio):
    ds = studio.ds
    a = an.new_drawn("freehand", [(0.1, 0.5), (0.4, 0.4), (0.8, 0.5)]); a["show"]["appear"] = "draw"; a["show"]["draw"] = 1.0
    spec = _one_part(ds, a, seconds=3)
    fr = _main(spec, ds); fps = spec["output"]["fps"]
    g = [f["ann"][a["id"]][1] for f in fr[:int(1.5 * fps)] if a["id"] in f.get("ann", {})]
    assert g[0] < 0.1 and g[-1] == 1.0 and all(x <= y for x, y in zip(g, g[1:]))
    assert sum(1 for x in g if x < 1.0) == pytest.approx(fps, abs=2)  # about one second


# ---- separate layers -----------------------------------------------------------------------------------------------
def _layered_spec(ds):
    lo, hi = ds.target_box({"class": ds.key_of(2), "component": 1}, pad=False); c = (np.asarray(lo) + hi) / 2
    arrow = an.new_arrow(c, ds.key_of(2), 1, "vesicle")
    note = an.new_text("note", x=0.6, y=0.1); note["layer"] = "labels"
    curve = an.new_drawn("curve", [(0.5, 0.75), (0.6, 0.6), (0.7, 0.75)])
    spec = tr.default_spec(ds); spec["style"].update(legend=True, slice_label=True, title="Layers")
    spec["shots"] = [sb.new_part("overview", motion="spin", seconds=1.5),
                     sb.new_part("overview", motion="none", seconds=1.5, view=dict(tilt=60.0, az=40.0, zoom=1.4))]
    spec["shots"][1]["transition_in"] = dict(kind="dissolve"); spec["shots"][1]["transition"] = 1.0
    spec["annotations"] = [arrow, note, curve]
    return spec, (arrow, note, curve)


def _ssim(a, b):
    from skimage.metrics import structural_similarity
    return structural_similarity(a, b, channel_axis=2, data_range=255)


def test_layers_lay_back_into_the_merged_frame(studio):
    ds, ren = studio.ds, studio.ren
    spec, (arrow, note, curve) = _layered_spec(ds)
    frames = sb.build_plan(spec, ds).frames
    mains = [f for f in frames if f["phase"] == "main" and len(f.get("ann") or {}) == 3 and min(v[0] for v in f["ann"].values()) > 0.99]
    blends = [f for f in frames if f.get("blend") and 0.3 < f["blend"]["q"] < 0.7]
    assert blends, "the dissolve between the parts"
    for f in (mains[len(mains) // 2], blends[0]):
        merged = ren.draw(f, spec, (320, 180))
        L = ren.draw_layers(f, spec, (320, 180))
        plain = ren.draw(f, spec, (320, 180), annotate=False) if not f.get("blend") else None
        if plain is not None:
            assert np.array_equal(L["base"], plain)  # the 3D movie has no overlay pixels at all
        back = layers.composite(L["base"], [L["labels"], L["arrows"]])
        assert _ssim(back, merged) >= 0.99 and np.abs(back.astype(int) - merged).max() <= 40
        for n in ("arrows", "labels"):
            assert L[n].shape == (180, 320, 4) and (L[n][..., 3] == 0).mean() > 0.8  # transparent outside the shapes
    f = mains[len(mains) // 2]; L = ren.draw_layers(f, spec, (320, 180)); ren.draw(f, spec, (320, 180))
    def alpha_in(img, name):
        x0, y0, x1, y1 = (int(round(v)) for v in ren.last_rects[name]["box"])
        return int(img[max(0, y0):y1, max(0, x0):x1, 3].max())
    assert alpha_in(L["arrows"], f"ann:{curve['id']}") > 200 and alpha_in(L["labels"], f"ann:{curve['id']}") < 30
    assert alpha_in(L["labels"], f"ann:{note['id']}") > 200 and alpha_in(L["arrows"], f"ann:{note['id']}") == 0
    assert alpha_in(L["labels"], "title") > 200 and alpha_in(L["arrows"], "title") == 0


def _rgba_frame(path, n, size):
    import subprocess
    from tomoreveal.encode import ffmpeg_exe
    r = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-i", path, "-vf", f"select=eq(n\\,{n})", "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(size[1], size[0], 4)


def test_layer_movies_and_the_merge_tool(studio, tmp_path):
    import imageio.v2 as imageio
    from tomoreveal.movie import render_movie
    ds, ren = studio.ds, studio.ren
    spec, _ = _layered_spec(ds)
    spec["output"].update(size=[160, 90], fps=10, aa=False, formats=["mp4"], file_size="larger")
    one = str(tmp_path / "one.mp4"); render_movie(ren, spec, one, poster=False)
    spec["output"]["layers"] = "separate"; out = str(tmp_path / "split.mp4"); render_movie(ren, spec, out, poster=False)
    info = json.load(open(layers.info_path(out)))
    assert info["base"] == "split.mp4" and [L["name"] for L in info["layers"]] == ["labels", "arrows"]
    for L in info["layers"]:
        fr = _rgba_frame(str(tmp_path / L["file"]), 8, (160, 90))  # alpha kept
        assert (fr[..., 3] < 10).mean() > 0.5 and fr[..., 3].max() > 200  # (text is large in a 160 x 90 movie)
    merged = layers.merge(layers.info_path(out), crf=12)
    assert os.path.basename(merged) == "split_merged.mp4"
    ra, rb = imageio.get_reader(one), imageio.get_reader(merged)
    n = 0
    for i, (x, y) in enumerate(zip(ra, rb)):
        if i % 4 == 0:
            assert _ssim(x, y) >= 0.97, i  # two H.264 encodes of the same frames (the layer test above checks 0.99)
            n += 1
    assert n >= 8 and ra.count_frames() == rb.count_frames()
    ra.close(); rb.close()
