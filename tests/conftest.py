import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    """A small made-up tomogram and label volume written once per test run."""
    from tomoreveal import make_demo
    return make_demo(str(tmp_path_factory.mktemp("demo")))


@pytest.fixture(scope="session")
def needs_3d():
    """The only place a test may skip for lack of 3D: draw one sphere with plain pyvista, using the same kind of
    window that Renderer(headless=True) would ask for. Errors in TomoReveal itself never reach this probe."""
    from tomoreveal.render import software_rendering_available
    try:
        if software_rendering_available():
            os.environ["VTK_DEFAULT_OPENGL_WINDOW"] = "vtkOSOpenGLRenderWindow"
        import pyvista as pv
        pl = pv.Plotter(off_screen=True, window_size=[64, 64])
        pl.add_mesh(pv.Sphere()); pl.screenshot(); pl.close()
    except Exception as exc:  # no OpenGL / OSMesa on this machine
        pytest.skip(f"3D rendering is not available here: {exc}")


def load_demo_studio(demo):
    """The demo data loaded and drawn once. Any error is raised, so a broken load fails the test."""
    import tomoreveal as tr
    st = tr.Studio(headless=True)
    st.load([demo["tomogram"], demo["labels"]], log=lambda *a: None)
    spec = tr.default_spec(st.ds)
    frames, _ = tr.build_timeline(spec, st.ds)
    st.ren.draw(frames[-1], spec, (320, 180))
    return st


@pytest.fixture(scope="session")
def studio(demo, needs_3d):
    return load_demo_studio(demo)


@pytest.fixture(scope="session", autouse=True)
def private_settings(tmp_path_factory):
    """Window settings (theme, folders) go to a temporary place, never to the user's own settings."""
    try:
        from PySide6 import QtCore
    except ImportError:
        return
    # On Windows the native format is the registry, and setPath cannot move it; so tests use INI files instead.
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
    QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, str(tmp_path_factory.mktemp("settings")))
