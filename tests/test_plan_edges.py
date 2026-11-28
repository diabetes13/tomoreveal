"""Edge cases of the movie plan (batch 6: F-34, F-35, F-37, F-38, F-39)."""
import pytest

import tomoreveal as tr
from tomoreveal import storyboard as sb
from tomoreveal.common import UserError


@pytest.fixture(scope="module")
def ds(demo):
    st = tr.Studio(headless=True)
    st.add_files([demo["tomogram"], demo["labels"]], log=lambda *a: None, make_renderer=False)
    return st.ds


@pytest.mark.parametrize("bad", [{}, {"type": "zoom"}])
def test_part_without_a_known_type_is_a_clear_error(ds, bad):
    """F-39: a part without "type" gave a KeyError in every build_plan."""
    spec = tr.default_spec(ds); spec["shots"].insert(1, dict(bad, seconds=2))
    with pytest.raises(UserError, match="part 2"):
        sb.build_plan(spec, ds)


def test_return_flight_belongs_to_the_last_part_shown(ds):
    """F-34: the flight back was credited to a later part that was left out, so the timeline showed a block for it."""
    spec = tr.default_spec(ds)
    shown = tr.focus_shot({"class": ds.key_of(2)}, "vesicles", seconds=1)
    gone = tr.focus_shot({"class": "labels:not-loaded#1"}, "gone", seconds=1)
    spec["shots"] += [shown, gone]
    plan = sb.build_plan(spec, ds)
    assert gone["id"] in plan.problems
    assert {sg["part"] for sg in plan.segments} == {p["id"] for p in spec["shots"] if p is not gone}
    assert plan.segments[-2]["phase"] == "transition" and plan.segments[-2]["part"] == shown["id"]


def test_closeup_of_an_unticked_structure_is_left_out(ds):
    """F-35: a close-up of a structure unticked in the Structures tab showed an empty, greyed scene."""
    spec = tr.default_spec(ds); key = ds.key_of(2)
    part = tr.focus_shot({"class": key}, "vesicles", seconds=1); spec["shots"].append(part)
    spec["classes"][key]["show"] = False
    plan = sb.build_plan(spec, ds)
    assert "switched off" in plan.problems.get(part["id"], "")
    assert all(f["part"] != part["id"] for f in plan.frames)



def test_closeup_of_several_objects_is_left_out_only_when_all_are_switched_off(ds):
    """3.4 close-ups can hold several structures: one switched off still leaves the others to look at."""
    spec = tr.default_spec(ds); a, b = ds.key_of(2), ds.key_of(3)
    part = tr.focus_shot({"class": a}, "two", seconds=1); part["target"] = sb.make_target([(a, None), (b, None)])
    spec["shots"].append(part)
    spec["classes"][a]["show"] = False
    plan = sb.build_plan(spec, ds)
    assert part["id"] not in plan.problems and any(f["part"] == part["id"] for f in plan.frames)
    spec["classes"][b]["show"] = False
    assert "switched off" in sb.build_plan(spec, ds).problems.get(part["id"], "")

def test_one_voxel_clip_box_with_a_closeup(ds):
    """F-37: a zero-size box to frame gave a camera distance of 0, then ZeroDivisionError."""
    spec = tr.default_spec(ds); spec["clip"] = dict(x=[100, 100], y=[100, 100], z=[10, 10], fit=True)
    spec["shots"].append(tr.focus_shot({"class": ds.key_of(2)}, "vesicles", seconds=1))
    plan = sb.build_plan(spec, ds)
    assert all(f["pose"]["dist"] > 0 and f["pose"]["scale"] > 0 for f in plan.frames)



@pytest.mark.parametrize("kind", ["cut", "dissolve"])
def test_a_grow_close_up_entered_without_a_flight_leaves_nothing_behind(ds, kind):
    """Its last fly-in state was copied whole into what later parts start from: their frames kept the clip plane
    ("fx") of the grow effect."""
    spec = tr.default_spec(ds)
    a = sb.new_part("overview", seconds=1)
    b = sb.new_part("focus", seconds=1, target=sb.make_target([(ds.key_of(2), 1)]), effect=dict(kind="grow"))
    b["transition_in"] = dict(kind=kind)
    c = sb.new_part("overview", seconds=1)
    spec["shots"] = [a, b, c]
    frames = sb.build_plan(spec, ds).frames
    assert [f for f in frames if f["part"] == b["id"] and f.get("fx")]  # the close-up itself grows
    later = [f for f in frames if f["part"] == c["id"]]
    assert later and not [f for f in later if "fx" in f]
