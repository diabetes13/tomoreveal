import json
import os
import subprocess
import sys

import pytest

from tomoreveal.dragonfly import OrsSession, bgr_hex, describe_session, parse_strings

from fake_session import write_session


def run(*args):
    return subprocess.run([sys.executable, "-m", "tomoreveal", *args], capture_output=True, text=True)


def test_string_and_colour_decoding():
    raw = b"".join(__import__("struct").pack("<Q", k) + s.encode("utf-16-be") + b"\0\0" for k, s in ((1, "ER"), (2, "int fila")))
    assert parse_strings(raw) == {1: "ER", 2: "int fila"}
    assert bgr_hex(0x00332211) == "#112233"


def test_describe_synthetic_session(tmp_path):
    p = write_session(str(tmp_path / "s.ORSSession"))
    d = describe_session(p)
    assert d["dragonfly_version"] == "2022.2.0.1409"
    (o,) = d["objects"]
    assert o["data_types"] == [4, 4, 9, 5, 5, 8, 4]
    assert o["decoded_voxels"] == o["total_label_size"] == 22
    assert [(r["label"], r["voxels"], r["name"], r["colour"], r["visible"]) for r in o["table"]] == [
        (1, 10, "ER", "#ff0000", 1), (2, 10, "Membrane", "#00ff00", 1), (3, 2, "background", "#0000ff", 0)]
    lab, classes = OrsSession(p).read_multiroi(OrsSession(p).objects()[0])
    assert int((lab == 1).sum()) == 10 and classes[2]["name"] == "Membrane"


def test_inspect_command(tmp_path, demo):
    p = write_session(str(tmp_path / "s.ORSSession"))
    r = run("inspect", p)
    assert r.returncode == 0 and "Membrane" in r.stdout and "matches TotalLabelSize" in r.stdout
    r = run("inspect", p, "--json")
    assert json.loads(r.stdout)["objects"][0]["table"][0]["name"] == "ER"
    r = run("inspect", os.path.dirname(demo["tomogram"]))
    assert r.returncode == 1 and "no Dragonfly session" in r.stderr and "Traceback" not in r.stderr


@pytest.mark.skipif(not os.environ.get("TOMOREVEAL_TEST_DATA"), reason="set TOMOREVEAL_TEST_DATA to a folder of real sessions")
def test_inspect_real_sessions():
    folder = os.environ["TOMOREVEAL_TEST_DATA"]
    files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".orssession")]
    assert files, f"TOMOREVEAL_TEST_DATA is set, but {folder} holds no .ORSSession files"
    for f in files:
        for o in describe_session(f)["objects"]:
            if o["kind"] == "multi-ROI" and not o["undo_copy"] and not o["single_slice"]:  # previews are never shown
                assert "error" not in o, (f, o["title"], o.get("error"))
                assert o["decoded_voxels"] == o["total_label_size"]
                assert all(r["name"] for r in o["table"])
