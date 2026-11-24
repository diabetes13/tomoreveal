"""3.3 phase 1: movie parts with ids, any order, edit functions, split, undo and redo."""
import copy
import itertools
import json

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import storyboard as sb
from tomoreveal import edits
from tomoreveal.common import UserError
from tomoreveal.volumes import load_volume, write_mrc


def quiet(*a):
    pass


@pytest.fixture(scope="module")
def st(demo):
    s = tr.Studio(headless=True)
    s.add_files([demo["tomogram"], demo["labels"]], log=quiet, make_renderer=False)
    return s


def spec_with(ds, parts):
    spec = tr.default_spec(ds); spec["shots"] = parts
    return spec


def check_segments(plan):
    pos = 0
    for sg in plan.segments:
        assert sg["start"] == pos and sg["count"] > 0
        for f in range(sg["start"], sg["start"] + sg["count"]):
            assert plan.frames[f]["part"] == sg["part"] and sb.part_at_frame(plan, f) == sg["part"]
        pos += sg["count"]
    assert pos == len(plan.frames)


def test_old_settings_are_migrated_once(st):
    old = tr.default_spec(st.ds)
    old["shots"] = [dict(type="sweep", on=True, seconds=7), dict(type="reveal", on=True, seconds=7),
                    dict(type="overview", on=True, motion="rock", seconds=6, angle=25),
                    dict(type="focus", on=True, target={"class": 2}, caption="v", others="grey", motion="orbit",
                         seconds=2, zoom=1.0, slice="hide", angle=30)]
    sb.migrate_spec(old)
    ids = [p["id"] for p in old["shots"]]
    assert len(set(ids)) == 4 and [p["type"] for p in old["shots"]] == ["slide", "slide", "overview", "focus"]
    assert old["shots"][3]["tomogram"]["mode"] == "hidden" and "slice" not in old["shots"][3]
    again = copy.deepcopy(old); sb.migrate_spec(again)
    assert again == old  # idempotent
    new = tr.default_spec(st.ds)
    a, _ = tr.build_timeline(old, st.ds)
    b_spec = copy.deepcopy(old); b_spec["shots"] = [sb.as_legacy(p) for p in new["shots"]] + [old["shots"][3]]
    b, _ = tr.build_timeline(b_spec, st.ds)
    assert [sb.state_key(f) for f in a] == [sb.state_key(f) for f in b]  # same movie as the new default parts
    dup = tr.default_spec(st.ds); dup["shots"][1]["id"] = dup["shots"][0]["id"]
    sb.migrate_spec(dup)
    assert len({p["id"] for p in dup["shots"]}) == 3  # repeated ids are replaced


def test_insert_remove_duplicate_move(st):
    spec = tr.default_spec(st.ds); a, b, c = (p["id"] for p in spec["shots"])
    h = sb.new_part("hold")
    edits.insert_part(spec, h, after=a)
    assert [p["id"] for p in spec["shots"]] == [a, h["id"], b, c]
    edits.insert_part(spec, sb.new_part("hold", seconds=1), after="start"); first = spec["shots"][0]["id"]
    edits.duplicate_part(spec, b)
    ids = [p["id"] for p in spec["shots"]]
    assert ids[:4] == [first, a, h["id"], b] and ids[5] == c and ids[4] not in (b, c)
    assert spec["shots"][4] == dict(spec["shots"][3], id=ids[4])
    edits.move_part(spec, c, 0)
    assert spec["shots"][0]["id"] == c
    edits.set_part(spec, c, seconds=3.5)
    assert spec["shots"][0]["seconds"] == 3.5
    for pid in [p["id"] for p in spec["shots"]]:
        edits.remove_part(spec, pid)
        plan = sb.build_plan(spec, st.ds); check_segments(plan)
    assert spec["shots"] == [] and len(plan.frames) == 1
    with pytest.raises(UserError):
        edits.remove_part(spec, a)


def test_any_order_gives_a_sensible_movie(st):
    ds = st.ds; nz = ds.shape[0]; size = max(ds.shape)
    kinds = ["slide_up", "reveal", "overview", "focus", "hold"]
    for order in itertools.permutations(kinds):
        parts = [sb.new_part(k, seconds=3, target={"class": ds.key_of(2)}, caption="vesicles") if k == "focus"  # an orbit needs time
                 else sb.new_part(k, seconds=1) for k in order]
        plan = sb.build_plan(spec_with(ds, parts), ds)
        check_segments(plan)
        assert {p["id"] for p in parts} >= {sg["part"] for sg in plan.segments}
        prev = None
        for f in plan.frames:
            assert 0 <= f["k"] < nz and np.all(np.isfinite(f["pose"]["c"])) and f["pose"]["dist"] > 0
            if prev is not None:
                p, q = prev["pose"], f["pose"]
                assert np.linalg.norm(np.asarray(p["c"]) - q["c"]) < 0.08 * size, order   # no teleport of the camera
                assert abs(p["dist"] - q["dist"]) < 0.15 * p["dist"], order
                assert abs(((p["az"] - q["az"] + 180) % 360) - 180) < 13, order
                assert abs(f["k"] - prev["k"]) <= 6, order                                    # nor of the slice
            prev = f


def test_hard_cut_is_allowed_to_jump(st):
    ds = st.ds
    parts = [sb.new_part("overview", seconds=1), sb.new_part("focus", seconds=1, target={"class": ds.key_of(2)}, transition=0)]
    plan = sb.build_plan(spec_with(ds, parts), ds)
    i = sb.main_range(plan, parts[1]["id"])[0]
    assert not any(sg["phase"] == "transition" and sg["part"] == parts[1]["id"] and sg["start"] < i for sg in plan.segments)
    assert np.linalg.norm(np.asarray(plan.frames[i]["pose"]["c"]) - plan.frames[i - 1]["pose"]["c"]) > 5


def test_split_a_slide_and_pause_in_the_middle(st):
    ds = st.ds; spec = tr.default_spec(ds)
    up = spec["shots"][0]["id"]
    plan = sb.build_plan(spec, ds); start, count = sb.main_range(plan, up)
    whole = [f["k"] for f in plan.frames if f["part"] == up and f["phase"] == "main"]
    mid = start + count // 2; k_mid = plan.frames[mid]["k"]
    edits.split_part(spec, ds, up, mid)
    a, b = spec["shots"][0], spec["shots"][1]
    plan2 = sb.build_plan(spec, ds)
    halves = [f["k"] for f in plan2.frames if f["part"] in (a["id"], b["id"]) and f["phase"] == "main"]
    assert set(halves) == set(whole) and halves[0] == whole[0] and halves[-1] == whole[-1]
    assert abs(len(plan2.frames) - len(plan.frames)) <= 1
    assert a["id"] == up and a["to"] == k_mid and b["from"] == "current"
    hold = sb.new_part("hold", seconds=2)
    edits.insert_part(spec, hold, after=a["id"])
    plan3 = sb.build_plan(spec, ds)
    ks = {f["k"] for f in plan3.frames if f["part"] == hold["id"]}
    assert ks == {k_mid}  # the movie pauses at the middle slice
    with pytest.raises(UserError):
        edits.split_part(spec, ds, spec["shots"][-1]["id"], len(plan3.frames) - 5)  # an overview cannot be split
    with pytest.raises(UserError):
        edits.split_part(spec, ds, hold["id"], 0)  # the playhead is not inside it


def test_closeup_of_unloaded_structure_is_skipped_and_comes_back(demo, tmp_path):
    lab, vox = load_volume(demo["labels"])
    other = str(tmp_path / "other.mrc"); write_mrc(other, (lab == 2).astype("int8"), vox)
    s = tr.Studio(headless=True)
    info = s.add_files([demo["tomogram"], demo["labels"], other], log=quiet, make_renderer=False)
    spec = info["spec"]; okey = [c["key"] for c in info["structures"] if c["key"].startswith("labels:" + s._norm(other))][0]
    part = tr.focus_shot({"class": okey}, "other"); spec["shots"].append(part)
    assert part["id"] not in sb.build_plan(spec, s.ds).problems
    info = s.set_segmentation_enabled("labels:" + s._norm(other), False, log=quiet, make_renderer=False)
    tr.merge_settings(spec, info["spec"])
    plan = sb.build_plan(spec, s.ds)
    assert spec["shots"][-1]["id"] == part["id"] and plan.problems[part["id"]] == "structure not loaded"
    assert all(f["part"] != part["id"] for f in plan.frames)
    info = s.set_segmentation_enabled("labels:" + s._norm(other), True, log=quiet, make_renderer=False)
    tr.merge_settings(spec, info["spec"])
    plan = sb.build_plan(spec, s.ds)
    assert part["id"] not in plan.problems and any(f["part"] == part["id"] for f in plan.frames)



def test_untick_and_tick_again_keeps_name_and_colour(demo, tmp_path):
    """F-23: the settings of a segmentation that is switched off are kept for when it is switched on again."""
    lab, vox = load_volume(demo["labels"])
    other = str(tmp_path / "other.mrc"); write_mrc(other, (lab == 2).astype("int8"), vox)
    s = tr.Studio(headless=True)
    info = s.add_files([demo["tomogram"], demo["labels"], other], log=quiet, make_renderer=False)
    spec = info["spec"]; okey = [c["key"] for c in info["structures"] if c["key"].startswith("labels:" + s._norm(other))][0]
    spec["classes"][okey].update(name="my vesicle", color="#123456")
    for on in (False, True):
        info = s.set_segmentation_enabled("labels:" + s._norm(other), on, log=quiet, make_renderer=False)
        tr.merge_settings(spec, info["spec"])
    assert spec["classes"][okey]["name"] == "my vesicle" and spec["classes"][okey]["color"] == "#123456"


def test_history(st):
    spec = tr.default_spec(st.ds); h = edits.History()
    h.push(spec); gone = spec["shots"][1]["id"]
    edits.remove_part(spec, gone); h.push(spec)
    assert h.can_undo and not h.can_redo
    back = h.undo()
    assert [p["id"] for p in back["shots"]][1] == gone and h.can_redo
    again = h.redo()
    assert gone not in [p["id"] for p in again["shots"]]
    h.push(again)  # pushing an unchanged state adds nothing
    assert h.pos == 1
    for i in range(150):
        again["view"]["tilt"] = i; h.push(again)
    assert len(h.states) == 100 and h.states[-1]["view"]["tilt"] == 149
    json.dumps(h.states[-1])


def test_names(st):
    d = tr.default_spec(st.ds)
    assert [sb.part_name(p) for p in d["shots"]] == ["Slide up through the slices", "Slide back, revealing the segmentation",
                                                       "Overview (rock)"]
    f = tr.focus_shot({"class": st.ds.key_of(2), "component": 1}, "x")
    assert sb.part_name(f, d, st.ds) == "Close-up: one object of vesicles"
    assert sb.part_name(sb.new_part("slide", **{"from": 3, "to": "last"}, structures="above")) == "Slide: slice 4 → last (structures above)"
