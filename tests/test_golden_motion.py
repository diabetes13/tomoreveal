"""Camera motion of a wiggle close-up, a follow path and a path through three points must not change unnoticed
(pinned with the 3.4.1 code; plan 3.5 phase 0). Later versions change these on purpose, but only for new settings:
parts saved before 3.5 (`sb.as_legacy`) must still move exactly like this.

Regenerate tests/data/golden_motion.json only on purpose:
    python tests/test_golden_motion.py
"""
import json
import os
import sys

import numpy as np

import tomoreveal as tr
from tomoreveal import storyboard as sb

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "data", "golden_motion.json")


def legacy(p):
    """A part as a project saved with 3.4 would have it."""
    f = getattr(sb, "as_legacy", None)
    return f(p) if f else p


def specs(ds):
    out = {}
    s = tr.default_spec(ds)
    s["shots"] = [legacy(sb.new_part("overview")), legacy(sb.new_part("focus", target={"class": ds.key_of(2), "component": 1},
                                                                       caption="one vesicle", motion="wiggle", seconds=4))]
    out["wiggle"] = s
    s = tr.default_spec(ds)
    s["shots"] = [legacy(sb.new_part("overview")), legacy(sb.new_part("focus", target={"class": ds.key_of(1)}, motion="orbit", seconds=4))]
    out["orbit"] = s
    s = tr.default_spec(ds)
    s["shots"] = [legacy(sb.new_part("path", seconds=4, source=dict(kind="follow", items=[{"structure": ds.key_of(1), "component": None}])))]
    out["follow"] = s
    s = tr.default_spec(ds)
    wps = [dict(pos=[40.0, 40.0, 150.0], focal=[128.0, 160.0, 24.0]), dict(pos=[200.0, 120.0, 120.0], focal=[128.0, 160.0, 24.0], pause=0.5),
           dict(pos=[60.0, 280.0, 90.0], focal=[128.0, 200.0, 24.0])]
    s["shots"] = [legacy(sb.new_part("path", seconds=4, source=dict(kind="points", items=[]), waypoints=wps))]
    out["points"] = s
    return out


def pose_row(f):
    p = f["pose"]
    row = [round(float(v), 3) for v in p["c"]] + [round(p["tilt"], 3), round(p["az"] % 360.0, 3), round(p["dist"], 3)]
    if p.get("pos") is not None:
        row += [round(float(v), 3) for v in p["pos"]] + [round(float(v), 4) for v in p["up"]]
    return row


def describe(spec, ds):
    frames, _ = tr.build_timeline(spec, ds)
    return [pose_row(f) for f in frames]


def load_demo(folder):
    st = tr.Studio(headless=True)
    demo = tr.make_demo(folder)
    st.add_files([demo["tomogram"], demo["labels"]], log=lambda *a: None, make_renderer=False)
    return st.ds


def test_motion_matches_the_pinned_one(demo):
    st = tr.Studio(headless=True)
    st.add_files([demo["tomogram"], demo["labels"]], log=lambda *a: None, make_renderer=False)
    golden = json.load(open(GOLDEN))
    for name, spec in specs(st.ds).items():
        got = describe(spec, st.ds)
        assert len(got) == len(golden[name]), name
        for i, (g, w) in enumerate(zip(got, golden[name])):
            assert np.allclose(g, w, atol=2e-3), f"{name}: frame {i} differs: {g} != {w}"


if __name__ == "__main__":
    import tempfile
    ds = load_demo(tempfile.mkdtemp())
    out = {name: describe(spec, ds) for name, spec in specs(ds).items()}
    json.dump(out, open(GOLDEN, "w"))
    print({k: len(v) for k, v in out.items()}, "->", GOLDEN, file=sys.stderr)
