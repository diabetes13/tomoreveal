"""Phase 3: the Data tab and the four states of the window (nothing, tomogram only, segmentation only, both)."""
import argparse
import sys

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6 import QtWidgets  # noqa: E402


@pytest.fixture
def window(tmp_path):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    errors = []
    old = sys.excepthook
    sys.excepthook = lambda t, v, tb: errors.append(v)
    yield app, win, errors
    sys.excepthook = old
    win.close()


def wait(app, win, start):
    """Start a job and wait for the worker with an event loop (polling with qWait is far too slow)."""
    start()
    if win.worker is not None and win.worker.isRunning():
        loop = QtCore.QEventLoop(); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(40):
        app.processEvents()


def structure_names(win):
    g = win.struct_grid
    return [w.text() for w in (g.itemAt(i).widget() for i in range(g.count())) if isinstance(w, QtWidgets.QLineEdit)]


def seg_ticks(win):
    lay = win.data.seg_lay
    return [w for w in (lay.itemAt(i).widget() for i in range(lay.count())) if isinstance(w, QtWidgets.QCheckBox)]


def tabs_shown(win):
    return [win.tabs.isTabVisible(i) for i in range(win.tabs.count())]


def is_empty(win):
    return tabs_shown(win) == [True, False, False, False] and not win.data.empty_lbl.isHidden() and win.spec is None


def test_tomogram_first_then_segmentation(window, demo):
    app, win, errors = window
    assert is_empty(win)
    for _ in range(2):  # every rebuild of the lists, twice
        wait(app, win, lambda: win.data.add_tomogram([demo["tomogram"]]))
        if win.st.ren is None:
            pytest.skip("3D rendering is not available here")
        assert tabs_shown(win) == [True, True, True, True]
        assert structure_names(win) == [] and not win.struct_note.isHidden()
        assert "No segmentation loaded" in win.struct_note.text()
        assert win.shot_ctl[0][0].isEnabled() and not win.shot_ctl[1][0].isEnabled()  # sweep yes, reveal needs a segmentation
        assert not win.pick_btn.isEnabled()  # no close-ups without structures
        assert len(win.data.tomo_group.buttons()) == 2  # None + the file
        wait(app, win, lambda: win.data.add_segmentation([demo["labels"]]))
        assert structure_names(win) == ["membrane", "vesicles", "filaments", "particles"] and win.struct_note.isHidden()
        (tick,) = seg_ticks(win)
        assert tick.isChecked() and tick.isEnabled()
        key = "labels:" + win.st._norm(demo["labels"])
        wait(app, win, lambda: win.data.switch(key, False))
        assert structure_names(win) == [] and not win.struct_note.isHidden() and not seg_ticks(win)[0].isChecked()
        wait(app, win, lambda: win.data.switch(key, True))
        assert len(structure_names(win)) == 4
        wait(app, win, win.start_over)
        assert is_empty(win) and seg_ticks(win) == [] and len(win.data.tomo_group.buttons()) == 0
    assert not errors, errors


def test_segmentation_only_then_none(window, demo):
    app, win, errors = window
    for _ in range(2):
        wait(app, win, lambda: win.data.add_segmentation([demo["labels"]]))
        if win.st.ren is None:
            pytest.skip("3D rendering is not available here")
        assert structure_names(win) == ["membrane", "vesicles", "filaments", "particles"]
        assert not win.shot_ctl[0][0].isEnabled() and win.shot_ctl[0][0].toolTip() == "needs a tomogram"
        assert win.shot_ctl[1][0].isEnabled() and win.pick_btn.isEnabled()
        assert win.data.info["tomogram"] is None and win.data.tomo_group.buttons()[0].isChecked()  # "None"
        assert "drawn on its own" in win.data.dims_lbl.text()
        assert win.prev.pixmap() is not None and not win.prev.pixmap().isNull()  # a preview was drawn
        key = "labels:" + win.st._norm(demo["labels"])
        wait(app, win, lambda: win.data.remove(key))
        assert is_empty(win)
    assert not errors, errors


def test_a_file_can_be_used_as_the_other_kind(window, demo):
    app, win, errors = window
    wait(app, win, lambda: win.data.add_tomogram([demo["labels"]]))  # the label file, forced to be a tomogram
    if win.st.ren is None:
        pytest.skip("3D rendering is not available here")
    assert win.data.info["tomogram"] == "file:" + win.st._norm(demo["labels"]) and seg_ticks(win) == []
    wait(app, win, lambda: win.data.add_segmentation([demo["labels"]]))  # "Use as segmentation"
    assert win.data.info["tomogram"] is None and len(seg_ticks(win)) == 1 and len(structure_names(win)) == 4
    assert not errors, errors
