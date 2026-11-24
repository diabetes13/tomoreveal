"""3.5: folding sections, the mouse wheel never changes a setting, still overviews by default."""
import argparse
import sys

import pytest

from tomoreveal import storyboard as sb


def test_new_overviews_are_still_but_the_default_movie_keeps_its_rock():
    assert sb.new_part("overview")["motion"] == "none"
    assert sb.default_parts()[-1]["motion"] == "rock"  # the command line and the golden plan
    assert sb.default_parts(rock=False)[-1]["motion"] == "none"


@pytest.fixture
def window(tmp_path, demo, needs_3d):
    QtCore = pytest.importorskip("PySide6.QtCore")
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True)); win.resize(1400, 900)
    errs = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errs.append(v)
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(30):
        app.processEvents()
    yield app, win, errs
    sys.excepthook = old; win.close()


def pump(app, n=10):
    for _ in range(n):
        app.processEvents()


def test_wheel_scrolls_the_panel_not_the_setting(window):
    from PySide6 import QtCore, QtGui, QtWidgets
    app, win, errs = window
    win.tabs.setCurrentIndex(win.TAB_LOOK); win.show(); pump(app)
    cb = win.look35.preset; before = cb.currentIndex()
    pos = QtCore.QPointF(5, 5)
    ev = QtGui.QWheelEvent(pos, QtCore.QPointF(cb.mapToGlobal(pos.toPoint())), QtCore.QPoint(0, 0), QtCore.QPoint(0, -120),
                           QtCore.Qt.NoButton, QtCore.Qt.NoModifier, QtCore.Qt.NoScrollPhase, False)
    QtWidgets.QApplication.sendEvent(cb, ev); pump(app)
    assert cb.currentIndex() == before
    cb.setProperty("tomoreveal_wheel", True)  # the same wheel turn without the filter does change it
    QtWidgets.QApplication.sendEvent(cb, QtGui.QWheelEvent(ev)); pump(app)
    assert cb.currentIndex() != before
    cb.setProperty("tomoreveal_wheel", False); cb.setCurrentIndex(before)
    assert not errs, errs


def test_new_overview_in_the_window_is_still_and_parts_fold(window):
    from PySide6.QtWidgets import QFormLayout
    from tomoreveal.gui_fold import Fold
    app, win, errs = window
    ov = [p for p in win.spec["shots"] if p["type"] == "overview"]
    assert ov and ov[-1]["motion"] == "none"
    part = sb.new_part("focus", target={"class": win.st.ds.key_of(2), "component": 1})
    win.editor.insert(part); pump(app)
    panel = win.parts_panel; panel.open(part["id"]); pump(app)
    form = panel.rows[part["id"]]["form"]
    folds = [w for w in form.parentWidget().findChildren(Fold)]
    assert len(folds) >= 2  # More settings, and the transition
    more = folds[0]
    labels = [more.form.itemAt(i, QFormLayout.LabelRole).widget().text() for i in range(more.form.rowCount())
              if more.form.itemAt(i, QFormLayout.LabelRole) is not None]
    assert "Caption" in labels and "Highlight" in labels
    more.button.setChecked(True); pump(app)
    assert not more.body.isHidden()
    panel.fill_details(part["id"]); pump(app)  # rebuilt: still open
    folds = [w for w in panel.rows[part["id"]]["form"].parentWidget().findChildren(Fold) if not w.isHidden()]
    assert any(f.is_open() for f in folds)
    # a Look-tab section folds and stays folded
    g = win.look_controls.light_group; was = g.is_open(); g.set_open(not was); assert g.is_open() != was; g.set_open(was)
    assert not errs, errs
