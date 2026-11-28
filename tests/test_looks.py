"""Plan 3.5 phase 7: segmentation looks like ChimeraX and Amira (presets, baked soft shadows, outlines, see-through
membranes, depth fade, backgrounds)."""
import json

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import look as lk
from tomoreveal import project as pj
from tomoreveal import storyboard as sb
from tomoreveal.volumes import Dataset


def test_presets_and_custom(studio):
    spec = tr.default_spec(studio.ds)
    assert lk.style35(spec)["preset"] == "classic" and lk.style35(spec)["background"] == "black"  # old settings look as before
    for name, values in lk.LOOK_PRESETS.items():
        lk.apply_look(spec, name); st = lk.style35(spec)
        assert st["preset"] == name and all(st[k] == v for k, v in values.items())
    lk.apply_look(spec, "soft"); assert spec["style"]["bg"] == "#ffffff"
    lk.set_look(spec, outline="thick"); assert lk.style35(spec)["preset"] == "custom"
    assert lk.is_membrane(spec, "x", "Outer membrane") and lk.is_membrane(spec, "x", "ER") and not lk.is_membrane(spec, "x", "ribosomes")
    spec["classes"]["x"] = {"membrane": False}; assert not lk.is_membrane(spec, "x", "membrane")


def test_old_projects_migrate_to_classic(tmp_path, studio):
    spec = tr.default_spec(studio.ds)
    p = tmp_path / "old.tomoreveal"; p.write_text(json.dumps(dict(format=pj.FORMAT, schema=1, spec=spec, sources=[])))
    data = pj.read_project(str(p))
    assert data["schema"] == 2 and lk.style35(data["spec"])["preset"] == "classic"


@pytest.fixture(scope="module")
def cup():
    """A sphere resting in a cup (a hollow half-shell) with a small gap between them."""
    n = 64; z, y, x = np.mgrid[0:n, 0:n, 0:n].astype(np.float32)
    r = np.sqrt((x - 32) ** 2 + (y - 32) ** 2 + (z - 30) ** 2)
    lab = np.zeros((n, n, n), np.int16)
    lab[r < 12] = 1                                       # the ball
    lab[(r > 15) & (r < 19) & (z < 30)] = 2               # the cup under it
    ds = Dataset()
    ds.set_segmentation([dict(kind="labels", key="labels:cup", title="cup", arr=lab, classes={1: {"name": "ball"}, 2: {"name": "cup"}})], vox=10.0)
    ds.build_meshes(log=lambda *a: None)
    return ds


def test_soft_shadows_darken_the_gap(cup):
    ao = lk.BakedShadows(); ks = sorted(cup.meshes)
    ao.prepare(cup, ks, "normal")
    ball = cup.meshes[1]; f = ao.factor(ball, lk.SHADOW_DARKEST["medium"])
    p = np.asarray(ball.points)
    top, gap = f[p[:, 2] > 39].mean(), f[p[:, 2] < 22].mean()
    assert gap <= 0.7 * top  # at least 30 % darker in the gap than on the open top
    assert np.array_equal(f, ao.factor(ball, lk.SHADOW_DARKEST["medium"]))  # the same in every frame: no flicker
    ao.prepare(cup, [1], "normal")  # the cup hidden: it no longer shades the ball
    assert ao.factor(ball, lk.SHADOW_DARKEST["medium"])[p[:, 2] < 22].mean() > gap + 0.1


def two_balls(touching):
    n = 48; z, y, x = np.mgrid[0:n, 0:n, 0:96].astype(np.float32)
    lab = np.zeros((n, n, 96), np.int16)
    lab[np.sqrt((x - 30) ** 2 + (y - 24) ** 2 + (z - 24) ** 2) < 12] = 1
    if touching:  # two flat boxes side by side: their tops meet at the same depth
        lab[:] = 0; lab[16:32, 12:36, 20:42] = 1; lab[16:32, 12:36, 42:64] = 2
    else:
        lab[(np.sqrt((x - 66) ** 2 + (y - 24) ** 2 + (z - 24) ** 2) < 12) & (lab == 0)] = 2
    ds = Dataset()
    ds.set_segmentation([dict(kind="labels", key="labels:b", title="b", arr=lab,
                              classes={1: {"name": "a", "color": "#3080c0"}, 2: {"name": "b", "color": "#3080c0"}})], vox=10.0)
    ds.build_meshes(log=lambda *a: None)
    return ds


def draw_balls(ds, ren, between, size=(480, 270)):
    spec = tr.default_spec(ds); lk.apply_look(spec, "soft"); lk.set_look(spec, between=between, depth_fade="off", shadow="none")
    spec["style"].update(legend=False, slice_label=False); spec["view"].update(tilt=0)
    spec["shots"] = [sb.new_part("overview", motion="none", seconds=0.2)]
    f = tr.build_timeline(spec, ds)[0][0]
    return ren.draw(f, spec, size, aa=False).astype(int), spec, f


def test_outlines_ring_each_ball_and_between_touching_ones(needs_3d):
    from tomoreveal.render import Renderer
    ds = two_balls(False); ren = Renderer(ds, headless=True)
    try:
        img, spec, f = draw_balls(ds, ren, False)
        dark = img.sum(2) < 200
        (cx, cy), _ = ren.project([30, 24, 24]); W, H = 480, 270
        row = dark[int(cy * H)]
        xs = np.nonzero(row)[0]
        assert len(xs) >= 4  # the outline crosses this row on both sides of each ball
        runs = np.split(xs, np.nonzero(np.diff(xs) > 1)[0] + 1)
        assert all(1 <= len(r) <= 3 for r in runs)  # thin: about 1.5 px at 1080 lines, scaled down here
    finally:
        ren.close()
    # touching at the same depth: only the object-id picture finds the line between them
    lin = np.full((40, 60), 100.0); bg = np.zeros((40, 60), bool); ids = np.zeros((40, 60, 3), np.uint8)
    ids[:, :30] = (70, 30, 30); ids[:, 30:] = (110, 30, 30)
    assert not lk.outline_edges(lin, bg, None).any()
    e = lk.outline_edges(lin, bg, ids)
    assert e[:, 29].all() and e.sum() == 40  # one line, one pixel wide


def test_glass_membranes_and_transparent_export(studio):
    ds = studio.ds; spec = tr.default_spec(ds); lk.apply_look(spec, "glass")
    spec["style"].update(legend=False, slice_label=False); spec["style"]["tomogram"]["show"] = False
    f = [x for x in tr.build_timeline(spec, ds)[0] if x["type"] == "overview" and x["phase"] == "main"][0]
    studio.ren.draw(f, spec, (320, 180))
    mem = studio.ren.actors[1].GetProperty()
    assert mem.GetOpacity() == pytest.approx(0.3) and np.allclose(mem.GetColor(), tr.common.hex2rgb("#e6dcc4"))
    assert studio.ren.actors[3].GetProperty().GetOpacity() == pytest.approx(1.0)  # filaments stay solid
    img = studio.ren.draw(f, spec, (320, 180), transparent=True)
    assert img[2, 2, 3] == 0 and (img[..., 3] == 255).sum() > 500 and img.shape[2] == 4


def test_depth_fade_is_steady_within_a_part(studio):
    spec = tr.default_spec(studio.ds); lk.apply_look(spec, "soft")
    spec["shots"] = [sb.new_part("overview", motion="spin", seconds=2)]
    frames = [f for f in tr.build_timeline(spec, studio.ds)[0] if f["phase"] == "main"]
    assert len({f["dr"] for f in frames}) == 1
    img = studio.ren.draw(frames[10], spec, (320, 180))
    assert img.shape == (180, 320, 3)


def test_backgrounds(studio):
    spec = tr.default_spec(studio.ds); spec["style"].update(legend=False, slice_label=False); spec["style"]["tomogram"]["show"] = False
    f = tr.build_timeline(spec, studio.ds)[0][0]
    lk.set_look(spec, background="gradient")
    img = studio.ren.draw(f, spec, (320, 180), annotate=False).astype(int)
    assert img[2, 160].sum() < img[177, 160].sum() and img[177, 160].min() >= 250  # light grey at the top, white at the bottom
    lk.set_look(spec, background="black")
    assert studio.ren.draw(f, spec, (320, 180), annotate=False)[2, 160].max() == 0


INSIDE = [dict(pos=[100.0, 100.0, 20.0], focal=[128.0, 160.0, 24.0]), dict(pos=[150.0, 200.0, 22.0], focal=[128.0, 160.0, 24.0])]


def fade_span(f):
    """(where the fade starts, where it ends) of a frame's depth range, as the renderer uses it."""
    near, far = f["dr"]
    return np.array([near + 0.4 * (far - near), far])


def every_kind_of_flight(ds):
    spec = tr.default_spec(ds); lk.apply_look(spec, "soft")
    vt = sb.make_target([(ds.key_of(2), 1)])
    spec["shots"] = [sb.new_part("overview", motion="rock", seconds=2), sb.new_part("focus", target=vt, seconds=2),
                     sb.new_part("overview", seconds=2, view=dict(tilt=60.0, az=40.0, zoom=1.3)),
                     sb.new_part("hold", seconds=1, view=dict(tilt=20.0, az=-30.0, zoom=0.8)),
                     sb.new_part("path", seconds=3, source=dict(kind="points", items=[]), waypoints=INSIDE),
                     sb.new_part("focus", target=sb.make_target([(ds.key_of(3), None)]), seconds=2,
                                 transition_in=dict(overlap_prev=0.5, overlap_next=0.5))]
    return spec


def test_depth_fade_glides_through_every_flight(studio):
    """A flight's frames had the range of the part it flies to from its first frame (a jump), and the end of the movie
    kept the close-up's range."""
    ds = studio.ds; spec = every_kind_of_flight(ds)
    frames = sb.build_plan(spec, ds).frames
    assert all(f.get("dr") for f in frames)
    i, runs = 0, 0
    while i < len(frames):
        if frames[i]["phase"] != "transition":
            i += 1; continue
        j = i
        while j + 1 < len(frames) and frames[j + 1]["phase"] == "transition":
            j += 1
        seq = np.array([fade_span(f) for f in frames[max(0, i - 1):j + 2]])
        steps = np.abs(np.diff(seq, axis=0)).max(1)
        if steps.sum() > 0:
            runs += 1
            assert steps.max() <= 3 * steps.mean(), (frames[i]["part"], steps.max() / steps.mean())
        i = j + 1
    assert runs >= 6  # in, out, into a hold, into the path, into the last close-up, back
    first = next(f for f in frames if f["phase"] == "main")
    assert frames[-1]["dr"] == first["dr"]  # back at the overview: the overview's range


def test_depth_range_jumps_only_at_a_cut_a_blend_or_a_card(studio):
    ds = studio.ds; spec = every_kind_of_flight(ds)
    spec["shots"][2]["transition_in"] = dict(kind="cut"); spec["shots"][3]["transition_in"] = dict(kind="dissolve")
    spec["shots"].insert(4, sb.new_part("card", seconds=1))
    frames = sb.build_plan(spec, ds).frames
    kinds = {p["id"]: (p.get("transition_in") or {}).get("kind", "flight") for p in spec["shots"]}
    for i in range(1, len(frames)):
        a, b = frames[i - 1], frames[i]
        if any(f["phase"] == "transition" and not f.get("blend") for f in (a, b)) or a["dr"] == b["dr"]:
            continue
        assert b.get("blend") or b.get("card") or a.get("card") or (b["part"] != a["part"] and kinds.get(b["part"]) == "cut"), i


def test_a_still_pause_with_depth_fade_and_an_arrow_is_drawn_once(studio):
    from tomoreveal import annotations as an
    ds = studio.ds; spec = tr.default_spec(ds); lk.apply_look(spec, "soft")
    p = sb.new_part("slide", seconds=1, pause_after=1.0)
    a = an.new_arrow([128.0, 160.0, 20.0]); a["show"].update(appear="pop", end=10.0)
    spec["shots"] = [p]; spec["annotations"] = [a]
    pause = [f for f in sb.build_plan(spec, ds).frames if f["phase"] == "pause" and f["part"] == p["id"]]
    assert len(pause) == 30 and pause[0].get("dr") and pause[0].get("ann")
    assert len({sb.state_key(f) for f in pause}) == 1
