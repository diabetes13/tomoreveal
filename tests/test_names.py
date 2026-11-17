"""Phase 2: names, colours and Dragonfly's visible flag read by slot type; background and unnamed labels."""
import os

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal.dragonfly import OrsSession, describe_session

from fake_session import write_session

DIMS = (12, 10, 4)  # nx, ny, nz
RUNS = ((0, 9, 1), (20, 29, 2), (60, 61, 3), (100, 119, 4))


def quiet(*a):
    pass


def table(path):
    ses = OrsSession(path); obj = ses.objects()[0]
    return ses.class_table(obj["el"], 4)


def test_colours_come_from_slot_1_even_when_another_slot_holds_label_numbers(tmp_path):
    # sele7_training1 in the user's session: slot 0 held 0, 1, 2, ... and 3.1.0 took it for the colours (#010000, #020000, ...)
    p = write_session(str(tmp_path / "s.ORSSession"), dims=DIMS, runs=RUNS, names=("OM", "PG", "IM", "Background"),
                      colors=(0x7F0000, 0xFFFF55, 0x00FFAA, 0xFFFFFF), visible=(1, 1, 1, 0), slot0=range(5))
    classes, rules = table(p)
    assert [c["color"] for c in classes.values()] == ["#00007f", "#55ffff", "#aaff00", "#ffffff"]
    assert rules["colours"].startswith("slot ScalarValues_1") and "2022.2" in rules["layout"]
    assert [c["visible"] for c in classes.values()] == [True, True, True, False]


def test_other_layout_falls_back_and_says_so(tmp_path):
    n = 5
    strings = b"".join(__import__("struct").pack("<Q", k) + s.encode("utf-16-be") + b"\0\0"
                       for k, s in enumerate(("ER", "MT", "actin", "ribo"), 1))
    slots = [("CxvArrayString", strings, 4),
             ("CxvArrayUnsignedLong", np.arange(n, dtype="<u4").tobytes(), n),          # label numbers, not colours
             ("CxvArrayUnsignedLong", np.array([0, 0xFF, 0xFF00, 0xFF0000, 0x00FFFF], "<u4").tobytes(), n)]
    p = write_session(str(tmp_path / "s.ORSSession"), dims=DIMS, runs=RUNS, names=("x",) * 4, data_types=(9, 4, 4),
                      slots_override=slots)
    classes, rules = table(p)
    assert [c["name"] for c in classes.values()] == ["ER", "MT", "actin", "ribo"]
    assert [c["color"] for c in classes.values()] == ["#ff0000", "#00ff00", "#0000ff", "#ffff00"]
    assert "guessed" in rules["colours"] and "2022.2" not in rules["layout"]
    assert rules["visible"] == "no visible flags found" and all(c["visible"] is None for c in classes.values())
    out = describe_session(p)["objects"][0]
    assert out["rules"] == rules  # inspect prints the rule that was used


def test_missing_name_is_not_invented(tmp_path, capsys):
    p = write_session(str(tmp_path / "s.ORSSession"), dims=DIMS, runs=RUNS, names=("ER", "", "actin", "ribo"),
                      colors=(1, 2, 3, 4), visible=(1, 1, 1, 1))
    classes, _ = table(p)
    assert classes[2]["name"] == "label 2 (no name in the session)" and not classes[2]["named"] and classes[1]["named"]
    assert "label 2 has no name" in capsys.readouterr().out


def test_hidden_and_background_are_listed_but_off(tmp_path):
    p = write_session(str(tmp_path / "s.ORSSession"), dims=DIMS, runs=RUNS, names=("OM", "IM", "background", "EDR"),
                      colors=(1, 2, 3, 4), visible=(1, 0, 1, 1))
    st = tr.Studio(headless=True)
    info = st.add_files([p], log=quiet, make_renderer=False)
    s = {c["name"]: c for c in info["structures"]}
    assert set(s) == {"OM", "IM", "background", "EDR"}  # nothing is left out of the list
    assert s["IM"]["hidden"] and not s["IM"]["background"] and s["IM"]["mesh"]
    assert s["background"]["background"] and not s["background"]["mesh"]
    shown = {info["spec"]["classes"][c["key"]]["name"]: info["spec"]["classes"][c["key"]]["show"] for c in info["structures"]}
    assert shown == {"OM": True, "IM": False, "background": False, "EDR": True}
    info = st.build_structure(s["background"]["key"], log=quiet, make_renderer=False)  # the user ticks it
    assert [c["mesh"] for c in info["structures"] if c["name"] == "background"] == [True]


def test_second_source_after_first_has_its_own_names(tmp_path, demo):
    """H1/H3 regression: the second source's names are right, and nothing of the first remains once it is off."""
    a = write_session(str(tmp_path / "a.ORSSession"), dims=(256, 320, 48), runs=((0, 999, 1), (5000, 5999, 2)),
                      names=("Slayer", "PG"), colors=(1, 2), visible=(1, 1))
    st = tr.Studio(headless=True)
    info = st.add_files([a], log=quiet, make_renderer=False)
    assert [c["name"] for c in info["structures"]] == ["Slayer", "PG"]
    info = st.add_files([demo["labels"]], log=quiet, make_renderer=False)
    assert [c["name"] for c in info["structures"]] == ["Slayer", "PG", "membrane", "vesicles", "filaments", "particles"]
    for _ in range(2):
        info = st.set_segmentation_enabled(f"dragonfly:{st._norm(a)}:Segmentation SegWiz test", False, log=quiet, make_renderer=False)
        assert [c["name"] for c in info["structures"]] == ["membrane", "vesicles", "filaments", "particles"]
        assert all(c["key"].startswith("labels:") for c in info["structures"])
        info = st.set_segmentation_enabled(f"dragonfly:{st._norm(a)}:Segmentation SegWiz test", True, log=quiet, make_renderer=False)


@pytest.mark.skipif(not os.environ.get("TOMOREVEAL_TEST_DATA"), reason="set TOMOREVEAL_TEST_DATA to a folder of real sessions")
def test_real_sessions_use_the_typed_slots():
    folder = os.environ["TOMOREVEAL_TEST_DATA"]
    for f in sorted(os.listdir(folder)):
        if not f.lower().endswith(".orssession"):
            continue
        for o in describe_session(os.path.join(folder, f))["objects"]:
            if o["kind"] != "multi-ROI" or o["undo_copy"] or o["single_slice"]:
                continue
            assert o["rules"]["names"].startswith("text slot") and o["rules"]["colours"].startswith("slot ScalarValues_1"), (f, o["title"])
            cols = [r["colour"] for r in o["table"]]
            assert cols != ["#%02x0000" % k for k in range(1, len(cols) + 1)], (f, o["title"])  # not the label numbers
