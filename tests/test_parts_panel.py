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
    assert win.st.ren is not None, "the 3D renderer did not start after loading"
    win.tabs.setCurrentIndex(win.TAB_MOVIE); win.do_preview(); pump(app)
    return app, win


@pytest.fixture
def window(tmp_path, demo, needs_3d):
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
                                                               "Overview (still)"]
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



def field(panel, pid, label):
    form = panel.rows[pid]["form"]
    for i in range(form.rowCount()):
        lab = form.itemAt(i, QtWidgets.QFormLayout.LabelRole)
        if lab is not None and lab.widget().text() == label:
            return form.itemAt(i, QtWidgets.QFormLayout.FieldRole).widget()


def test_open_duration_box_follows_a_timeline_resize(window):
    """F-27: after a resize on the timeline, the open part's Duration box kept the old value; the next step wrote old + step."""
    app, win, errors = window
    panel = win.parts_panel; b = ids(win)[1]; panel.open(b); pump(app)
    before = field(panel, b, "Duration").value()
    win.editor.resized(b, before + 5.0); win.do_preview(); pump(app)
    box = field(panel, b, "Duration"); box.stepUp(); pump(app)
    assert win.spec["shots"][1]["seconds"] == pytest.approx(before + 5.0 + box.singleStep())
    assert not errors, errors



def test_notes_follow_the_new_plan(tmp_path, demo, needs_3d):
    """F-28: the parts list notes came from the previous plan, so after a Data change they said the opposite."""
    lab, vox = load_volume(demo["labels"])
    other = str(tmp_path / "other.mrc"); write_mrc(other, (lab == 2).astype("int8"), vox)
    errors = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errors.append(v)
    app, win = open_window(tmp_path, [demo["tomogram"], demo["labels"], other])
    try:
        key = next(c["key"] for c in win.info["structures"] if c["source_title"] == "other.mrc")
        win.editor.add_closeup(key, "other"); win.do_preview(); pump(app); pid = win.selected_part
        note = win.parts_panel.rows[pid]["note"]
        src = key.split("#")[0]
        for on, expect in ((False, "left out"), (True, "")):
            loop = QtCore.QEventLoop()
            win.data.switch(src, on); win.worker.finished.connect(loop.quit)
            if win.worker.isRunning():
                loop.exec()
            pump(app); win.do_preview(); pump(app)
            note = win.parts_panel.rows[pid]["note"]
            assert (expect in note.text()) if expect else note.text() == "", (on, note.text())
        win.parts_panel.open(ids(win)[1]); pump(app)
        box = field(win.parts_panel, ids(win)[1], "Duration")
        win.do_preview(); pump(app); win.do_preview(); pump(app)
        assert field(win.parts_panel, ids(win)[1], "Duration") is box  # a box being used is never replaced by a preview
        assert not errors, errors
    finally:
        sys.excepthook = old; win.close()



def test_note_of_a_closeup_of_an_unticked_structure(window):
    """F-35: the parts list gives the plan's reason, not always "not loaded"."""
    app, win, errors = window
    key = win.st.ds.key_of(2); win.editor.add_closeup(key, "vesicles"); win.do_preview(); pump(app); pid = win.selected_part
    win.spec["classes"][key]["show"] = False; win.do_preview(); pump(app)
    assert "switched off" in win.parts_panel.rows[pid]["note"].text()
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


def test_threshold_change_keeps_single_object_closeups(tmp_path, demo, needs_3d):
    lab, vox = load_volume(demo["labels"])
    feat = str(tmp_path / "demo_tomogram__vesicles.mrc"); write_mrc(feat, (lab == 2).astype("float32"), vox)
    errors = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errors.append(v)
    app, win = open_window(tmp_path, [demo["tomogram"], feat])
    try:
        c = win.info["structures"][0]; assert c["kind"] == "score"
        win.editor.insert(sb.focus_shot({"class": c["key"], "component": 1}, "one vesicle")); win.do_preview(); pump(app)
        pid = win.selected_part
        win._thr_changed(c["id"], 0.6); win.thr_timer.stop(); win.apply_thresholds(); win.do_preview(); pump(app)  # the app applies once, from this timer
        part = win.parts_panel.part(pid)
        assert part is not None and part["target"] == {"class": c["key"]}  # whole structure now, the part stays
        assert "whole structure" in win.msg_lbl.text()
        assert not errors, errors
    finally:
        sys.excepthook = old; win.close()
