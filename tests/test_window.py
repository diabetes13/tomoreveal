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


def row(win, i):
    """The Movie tab row of part number i (0-based)."""
    return win.parts_rows.rows[win.spec["shots"][i]["id"]]


def tabs_shown(win):
    return [win.tabs.isTabVisible(i) for i in range(win.tabs.count())]


def is_empty(win):
    return tabs_shown(win) == [True, False, False, False, False] and not win.data.empty_lbl.isHidden() and win.spec is None


def test_tomogram_first_then_segmentation(window, demo):
    app, win, errors = window
    assert is_empty(win)
    for _ in range(2):  # every rebuild of the lists, twice
        wait(app, win, lambda: win.data.add_tomogram([demo["tomogram"]]))
        if win.st.ren is None:
            pytest.skip("3D rendering is not available here")
        assert tabs_shown(win) == [True, True, True, True, True]
        assert structure_names(win) == [] and not win.struct_note.isHidden()
        assert "No segmentation loaded" in win.struct_note.text()
        assert row(win, 0)["on"].toolTip() == "" and row(win, 1)["on"].toolTip() == "needs a segmentation"  # slide up yes, reveal no
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
        assert row(win, 0)["on"].toolTip() == "needs a tomogram"
        assert row(win, 1)["on"].toolTip() == "" and win.pick_btn.isEnabled()
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


def test_tomogram_opacity_controls(window, demo):
    app, win, errors = window
    wait(app, win, lambda: win.load(paths=[demo["tomogram"], demo["labels"]]))
    if win.st.ren is None:
        pytest.skip("3D rendering is not available here")
    assert win.tslice_group.isEnabled() and win.tshow_cb.isChecked() and win.topac_s.value() == 100
    win.topac_s.setValue(40)
    assert win.spec["style"]["tomogram"]["opacity"] == pytest.approx(0.4)
    tomo = row(win, 2)["tomo"]  # part 3: overview
    combo = tomo.findChild(QtWidgets.QComboBox)
    combo.setCurrentIndex(combo.findData("hidden")); combo.activated.emit(combo.currentIndex())
    assert win.spec["shots"][2]["tomogram"]["mode"] == "hidden"
    assert win.sweep_hint.isHidden()
    row0 = row(win, 0)["tomo"].findChild(QtWidgets.QComboBox)
    row0.setCurrentIndex(row0.findData("hidden")); row0.activated.emit(row0.currentIndex())
    assert not win.sweep_hint.isHidden()  # the slide-up would show nothing
    frames, _ = __import__("tomoreveal").build_timeline(win.spec_for(), win.st.ds)
    s3 = [f["slice"] for f in frames if f["shot"] == 2]
    assert s3[0] > 0.3 and max(s3[15:]) == 0  # fades out over 0.5 s, then hidden
    wait(app, win, lambda: win.data.remove("file:" + win.st._norm(demo["tomogram"])))
    assert not win.tslice_group.isEnabled() and not row(win, 2)["tomo"].isEnabled()  # no tomogram
    assert win.spec["style"]["tomogram"]["opacity"] == pytest.approx(0.4)  # settings kept
    assert not errors, errors
