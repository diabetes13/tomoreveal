"""3.3 phase 4: the Movie tab's parts list (one line per part that opens and closes) and the close-up drop-downs."""
import argparse
import sys

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6 import QtWidgets  # noqa: E402

from tomoreveal import storyboard as sb  # noqa: E402
from tomoreveal.volumes import load_volume, write_mrc  # noqa: E402


def pump(app, n=30):
    for _ in range(n):
        app.processEvents()


def open_window(tmp_path, paths):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    win.resize(1400, 900)
    loop = QtCore.QEventLoop(); win.load(paths=paths); win.worker.finished.connect(loop.quit); loop.exec()
    pump(app)
    if win.st.ren is None:
        pytest.skip("3D rendering is not available here")
    win.tabs.setCurrentIndex(win.TAB_MOVIE); win.do_preview(); pump(app)
    return app, win


@pytest.fixture
def window(tmp_path, demo):
    errors = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errors.append(v)
    app, win = open_window(tmp_path, [demo["tomogram"], demo["labels"]])
    yield app, win, errors
    sys.excepthook = old; win.close()


def ids(win):
    return [p["id"] for p in win.spec["shots"]]


def boxes(win):
    lay = win.parts_panel.lay
    return [w for w in (lay.itemAt(i).widget() for i in range(lay.count())) if isinstance(w, QtWidgets.QFrame)]


def test_one_line_per_part_and_one_open_at_a_time(window):
    app, win, errors = window
    panel = win.parts_panel; a, b, c = ids(win)
    assert list(panel.rows) == [a, b, c] and len(boxes(win)) == 3
    assert [panel.rows[p]["name"].text() for p in (a, b, c)] == ["Slide up through the slices", "Slide back, revealing the segmentation",
                                                               "Overview (rock)"]
    panel.rows[b]["head"].clicked.emit(); pump(app)
    assert panel.open_id == b and not panel.rows[b]["details"].isHidden()
    panel.rows[c]["head"].clicked.emit(); win.do_preview(); pump(app)
    assert panel.open_id == c and panel.rows[b]["details"].isHidden() and not panel.rows[c]["details"].isHidden()
    assert win.selected_part == c and win.timeline.canvas.selected == c
    assert win.timeline.canvas.plan.frames[win.timeline.frame]["part"] == c  # the playhead moved into it
    win.timeline.part_selected.emit(a); pump(app)   # selecting on the timeline opens the line
    assert panel.open_id == a and panel.rows[c]["details"].isHidden()
    panel.rows[a]["arrow"].click(); pump(app)        # the arrow closes it again
    assert panel.open_id is None and panel.rows[a]["details"].isHidden()
    assert not errors, errors


def test_settings_change_the_part_in_place(window):
    app, win, errors = window
    panel = win.parts_panel; a = ids(win)[0]; panel.open(a); pump(app)
    form = panel.rows[a]["form"]
    combos = form.parentWidget().findChildren(QtWidgets.QComboBox)
    to = combos[1]  # From, To, Structures, ...
    to.setCurrentIndex(to.findData("number")); to.activated.emit(to.currentIndex()); pump(app)
    assert isinstance(win.spec["shots"][0]["to"], int)
    st = combos[2]; st.setCurrentIndex(st.findData("all")); st.activated.emit(st.currentIndex()); pump(app)
    assert win.spec["shots"][0]["structures"] == "all" and "all structures" in panel.rows[a]["name"].text()
    assert panel.open_id == a and not panel.rows[a]["details"].isHidden()  # still open: only the line was updated
    assert not errors, errors


def test_closeup_structure_dropdown(window):
    app, win, errors = window
    ds = win.st.ds
    win.editor.add_closeup(ds.key_of(1), "membrane"); win.do_preview(); pump(app)
    pid = win.selected_part; panel = win.parts_panel
    assert panel.open_id == pid
    combo = panel.rows[pid]["form"].itemAt(0, QtWidgets.QFormLayout.FieldRole).widget()
    keys = [combo.itemData(i) for i in range(combo.count())]
    assert keys == [c["key"] for c in win.info["structures"] if c["mesh"]]
    combo.setCurrentIndex(keys.index(ds.key_of(2))); combo.activated.emit(combo.currentIndex()); win.do_preview(); pump(app)
    part = win.spec["shots"][[p["id"] for p in win.spec["shots"]].index(pid)]
    assert part["target"] == {"class": ds.key_of(2)} and part["caption"] == "vesicles"  # caption followed: it was the old name
    assert panel.rows[pid]["name"].text() == "Close-up: vesicles"
    assert any(f["target"] == {"class": ds.key_of(2)} for f in win.timeline.canvas.plan.frames if f["part"] == pid)
    part["caption"] = "my own text"; panel.retarget(pid, ds.key_of(3))
    assert part["caption"] == "my own text"  # text the user typed is kept
    assert not errors, errors


def test_rebuilding_three_times(window):
    app, win, errors = window
    for step in ("add", "delete", "undo", "add", "delete", "undo"):
        if step == "add":
            win.editor.add("hold")
        elif step == "delete":
            win.editor.delete(ids(win)[0])
        else:
            win.editor.undo()
        win.do_preview(); pump(app)
        assert len(boxes(win)) == len(ids(win)) and list(win.parts_panel.rows) == ids(win), step
    assert not errors, errors


def test_threshold_change_keeps_single_object_closeups(tmp_path, demo):
    lab, vox = load_volume(demo["labels"])
    feat = str(tmp_path / "demo_tomogram__vesicles.mrc"); write_mrc(feat, (lab == 2).astype("float32"), vox)
    errors = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errors.append(v)
    app, win = open_window(tmp_path, [demo["tomogram"], feat])
    try:
        c = win.info["structures"][0]; assert c["kind"] == "score"
        win.editor.insert(sb.focus_shot({"class": c["key"], "component": 1}, "one vesicle")); win.do_preview(); pump(app)
        pid = win.selected_part
        win._thr_changed(c["id"], 0.6); win.apply_thresholds(); win.do_preview(); pump(app)
        part = win.parts_panel.part(pid)
        assert part is not None and part["target"] == {"class": c["key"]}  # whole structure now, the part stays
        assert "whole structure" in win.msg_lbl.text()
        assert not errors, errors
    finally:
        sys.excepthook = old; win.close()
