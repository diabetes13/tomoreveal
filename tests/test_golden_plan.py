"""The movie plan of the demo data must not change unnoticed (pinned with the 3.2.0 code).

Regenerate tests/data/golden_plan.json only on purpose, after checking that a change of the movie is wanted:
    python tests/test_golden_plan.py
"""
import json
import os
import sys

import tomoreveal as tr
from tomoreveal.storyboard import state_key

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "data", "golden_plan.json")


def plans(ds):
    """(a) the default movie; (b) the default movie plus a close-up of a whole structure and one of a single object."""
    from tomoreveal.storyboard import as_legacy
    a = tr.default_spec(ds); a["shots"] = [as_legacy(p) for p in a["shots"]]
    b = tr.default_spec(ds); b["shots"] = [as_legacy(p) for p in b["shots"]]
    vesicles = ds.key_of(2)
    from tomoreveal.storyboard import as_legacy  # parts as saved before 3.5: they must keep moving exactly as pinned
    b["shots"].append(as_legacy(tr.focus_shot({"class": ds.key_of(1)}, "membrane")))
    b["shots"].append(as_legacy(tr.focus_shot({"class": vesicles, "component": 1}, "one vesicle", seconds=3, others="hide")))
    return {"default": a, "closeups": b}


def portable_key(f, ds):
    """state_key with the close-up target written without the data folder's path."""
    t = f["target"]
    if t is not None:
        t = dict(t, **{"class": f"label {ds.classes[ds.id_of(t['class'])]['label']}"})
    return list(state_key(dict(f, target=t)))


def describe(spec, ds):
    frames, marks = tr.build_timeline(spec, ds)
    keys = json.loads(json.dumps([portable_key(f, ds) for f in frames], default=float))
    return dict(frames=len(frames), marks=[[m["frame"], m["label"]] for m in marks], keys=keys)


def load_demo(folder):
    st = tr.Studio(headless=True)
    demo = tr.make_demo(folder)
    st.add_files([demo["tomogram"], demo["labels"]], log=lambda *a: None, make_renderer=False)
    return st.ds


def test_plans_match_the_pinned_ones(demo):
    st = tr.Studio(headless=True)
    st.add_files([demo["tomogram"], demo["labels"]], log=lambda *a: None, make_renderer=False)
    golden = json.load(open(GOLDEN))
    for name, spec in plans(st.ds).items():
        got = describe(spec, st.ds)
        assert got["frames"] == golden[name]["frames"], name
        assert got["marks"] == golden[name]["marks"], name
        for i, (g, w) in enumerate(zip(got["keys"], golden[name]["keys"])):
            assert g == w, f"{name}: frame {i} differs"


if __name__ == "__main__":
    import tempfile
    ds = load_demo(tempfile.mkdtemp())
    os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
    out = {name: describe(spec, ds) for name, spec in plans(ds).items()}
    json.dump(out, open(GOLDEN, "w"))
    print({k: v["frames"] for k, v in out.items()}, "->", GOLDEN, file=sys.stderr)
