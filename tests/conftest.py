import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    """A small made-up tomogram and label volume written once per test run."""
    from tomoreveal import make_demo
    return make_demo(str(tmp_path_factory.mktemp("demo")))


@pytest.fixture(scope="session")
def studio(demo):
    """The demo data loaded with software rendering; skipped where no 3D renderer can start."""
    from tomoreveal import Studio
    st = Studio(headless=True)
    try:
        st.load([demo["tomogram"], demo["labels"]], log=lambda *a: None)
        spec = __import__("tomoreveal").default_spec(st.ds)
        frames, _ = __import__("tomoreveal").build_timeline(spec, st.ds)
        st.ren.draw(frames[-1], spec, (320, 180))
    except Exception as exc:  # no OpenGL / OSMesa on this machine
        pytest.skip(f"3D rendering is not available here: {exc}")
    return st
