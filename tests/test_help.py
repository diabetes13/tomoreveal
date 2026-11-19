"""Plan 3.4 phase 10: a tooltip on every control, and short file names in the Data tab."""
import argparse
import os
import sys

import pytest

import tomoreveal as tr
from tomoreveal import storyboard as sb
from tomoreveal.names import ORIGINAL, short_names

BASE = "Montage_21-A_cropA_recon_rotx_ddw_ep139_bin2_bin3"


def test_short_names_of_the_users_files():
    pre, suf, out = short_names([BASE, BASE + "-HistEq", BASE + "-HistEq-Gauss", BASE + "-HistEq-Gauss-Unshp"])
    assert out == [ORIGINAL, "HistEq", "HistEq-Gauss", "HistEq-Gauss-Unshp"] and pre == BASE
    pre, suf, out = short_names([BASE + "__membrane.mrc", BASE + "__ribosome.mrc"])
    assert out == ["membrane", "ribosome"] and suf == ".mrc"
    assert short_names(["one_name.mrc"])[2] == ["one_name.mrc"]
    assert short_names(["a_b.mrc", "a_b.mrc"])[2] == ["a_b.mrc", "a_b.mrc"]  # nothing differs: unchanged
    assert all(short_names(["x_1", "x_2", "x_10"])[2])  # at least one token each


QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6 import QtWidgets  # noqa: E402

INTERACTIVE = (QtWidgets.QAbstractButton, QtWidgets.QComboBox, QtWidgets.QAbstractSlider, QtWidgets.QAbstractSpinBox,
               QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit)


@pytest.fixture
def window(tmp_path, demo):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    errs = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errs.append(v)
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(40):
        app.processEvents()
    if win.st.ren is None:
        pytest.skip("3D rendering is not available here")
    yield app, win, errs
    sys.excepthook = old; win.close()


def missing_tooltips(win):
    from tomoreveal.help_texts import _row_label
    out = []
    for w in win.findChildren(QtWidgets.QWidget):
        if isinstance(w, INTERACTIVE) and not isinstance(w, QtWidgets.QScrollBar) and w.isVisibleTo(win) and not w.toolTip().strip():
            t = getattr(w, "text", None); t = t() if callable(t) else ""
            extra = w.currentText() if isinstance(w, QtWidgets.QComboBox) else (f"{w.minimum()}-{w.maximum()}" if isinstance(w, QtWidgets.QAbstractSlider) else "")
            title = w.parentWidget().title() if isinstance(w.parentWidget(), QtWidgets.QGroupBox) else ""
            gp = w.parentWidget().parentWidget(); gt = gp.title() if isinstance(gp, QtWidgets.QGroupBox) else type(gp).__name__
            out.append(f"{type(w).__name__} '{t}' [{extra}] in {type(w.parentWidget()).__name__} '{title}' / {gt} row={_row_label(w)}")
    return out


def generic_tooltips(win):
    return sorted({w.toolTip() for w in win.findChildren(QtWidgets.QWidget)
                   if isinstance(w, INTERACTIVE) and w.isVisibleTo(win) and "change this setting" in w.toolTip()})


def test_every_control_has_a_tooltip(window):
    app, win, errs = window
    ds = win.st.ds
    for kind, extra in (("title_card", {}), ("path", dict(source=dict(kind="points", items=[]))),
                        ("focus", dict(target=sb.make_target([(ds.key_of(2), None), (ds.key_of(1), None)]), motion="wiggle",
                                       highlight=dict(kind="pulse"), effect=dict(kind="section"), vignette=dict(on=True)))):
        win.editor.insert(sb.new_part(kind, **extra))
    win._struct_open = {c["key"] for c in win.info["structures"]}; win.build_controls()
    seen_generic = set(); allmiss = set()
    for tab in range(win.tabs.count()):
        win.tabs.setCurrentIndex(tab)
        parts = win.spec["shots"] if tab == win.TAB_MOVIE else [None]
        for p in parts:
            if p is not None:
                win.parts_panel.open(p["id"])
            for _ in range(5):
                app.processEvents()
            allmiss.update(f"{win.tabs.tabText(tab)}: {m}" for m in missing_tooltips(win))
            seen_generic |= set(generic_tooltips(win))
    print("generic:", sorted(seen_generic))
    assert not allmiss, sorted(allmiss)
    assert len(seen_generic) <= 3, sorted(seen_generic)  # almost every control has a written explanation
    assert win.tips_act.isChecked() and win.go_btn.statusTip()
    assert not errs, errs


def test_rename_a_file(window, demo, tmp_path):
    app, win, errs = window
    key = "labels:" + win.st._norm(demo["labels"]); before = os.path.getmtime(demo["labels"])
    win.data.set_name(key, "my segmentation")
    texts = [w.text() for w in win.data.findChildren(QtWidgets.QLabel)]
    assert any("my segmentation" in t for t in texts)
    assert win.project.data()["spec"]["data"]["names"][key] == "my segmentation"
    path = str(tmp_path / "p.tomoreveal"); assert win.project.save_to(path)
    assert tr.project.read_project(path)["spec"]["data"]["names"][key] == "my segmentation"
    assert os.path.getmtime(demo["labels"]) == before and os.path.exists(demo["labels"])  # the file is not renamed
    win.data.set_name(key, None)
    assert not any("my segmentation" in w.text() for w in win.data.findChildren(QtWidgets.QLabel))
    assert not errs, errs
