"""Three rendered frames of the demo must not change unnoticed (pinned with the 3.3.0 code; plan 3.4 phase 0).

A slide frame, an overview frame and a close-up frame, 320 x 180, no anti-aliasing. Software and hardware rendering
differ slightly, so the check is a mean absolute difference below 2 (0-255 scale), not an exact match.

Windows has reference pictures of its own (golden_frames_win.npz): text and film grain are drawn differently there, by 3 to 5
on the same scale.

Regenerate the file of this system only on purpose, after checking that a change of the picture is wanted:
    python tests/test_golden_frames.py
"""
import os
import sys

import numpy as np

import tomoreveal as tr

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "data", "golden_frames_win.npz" if sys.platform == "win32" else "golden_frames.npz")
SIZE = (320, 180)
TOLERANCE = 2.0


def golden_frames(ds):
    """{name: (frame state, spec)}: the middle of the slide-up, the start of the overview, the middle of a close-up."""
    sys.path.insert(0, HERE)
    from test_golden_plan import plans
    sp = plans(ds)
    a, b = sp["default"], sp["closeups"]
    fa, _ = tr.build_timeline(a, ds); fb, _ = tr.build_timeline(b, ds)
    slide = [f for f in fa if f["type"] == "slide" and f["phase"] == "main" and f["shot"] == 0]
    over = [f for f in fa if f["type"] == "overview" and f["phase"] == "main"]
    close = [f for f in fb if f["type"] == "focus" and f["phase"] == "main" and f["shot"] == 4]
    return {"slide": (slide[len(slide) // 2], a), "overview": (over[0], a), "closeup": (close[len(close) // 2], b)}


def render_all(st):
    return {name: st.ren.draw(f, spec, SIZE, aa=False)[..., :3] for name, (f, spec) in golden_frames(st.ds).items()}


def mean_diff(a, b):
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def test_frames_match_the_pinned_ones(studio, demo):
    # A renderer of its own: after a frame drawn with SSAA, VTK draws later frames without anti-aliasing a little
    # smoother (seen in 3.3 too), so the shared one would make this test depend on the order of the tests.
    st = tr.Studio(headless=True); st.load([demo["tomogram"], demo["labels"]], log=lambda *a: None)
    golden = np.load(GOLDEN)
    for name, img in render_all(st).items():
        assert img.shape == golden[name].shape, name
        d = mean_diff(img, golden[name])
        assert d < TOLERANCE, f"{name}: mean difference {d:.2f}"
    st._drop_renderer()


if __name__ == "__main__":
    import tempfile
    st = tr.Studio(headless=True)
    demo = tr.make_demo(tempfile.mkdtemp())
    st.load([demo["tomogram"], demo["labels"]], log=lambda *a: None)
    imgs = render_all(st)
    np.savez_compressed(GOLDEN, **imgs)
    print({k: v.shape for k, v in imgs.items()}, "->", GOLDEN, file=sys.stderr)
