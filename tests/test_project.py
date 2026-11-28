"""Plan 3.4 phase 1: project files, finding moved data, autosave and recovery, crash-safe chunked movies."""
import argparse
import json
import os
import shutil
import sys

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import project as pj
from tomoreveal.common import UserError


# ---------------------------------------------------------------------------------------------------------------
# without the window
# ---------------------------------------------------------------------------------------------------------------
def copy_demo(demo, folder):
    os.makedirs(folder, exist_ok=True)
    for p in (demo["tomogram"], demo["labels"], os.path.splitext(demo["labels"])[0] + ".json"):
        shutil.copy(p, folder)
    return {k: os.path.join(folder, os.path.basename(v)) for k, v in demo.items()}


def test_changed_file_is_reported(demo, tmp_path):
    d = copy_demo(demo, str(tmp_path / "data"))
    e = pj.file_entry("file:" + d["tomogram"], "tomogram", d["tomogram"], str(tmp_path / "p.tomoreveal"))
    assert pj.locate(e, str(tmp_path / "p.tomoreveal")) == (d["tomogram"], "ok")
    with open(d["tomogram"], "r+b") as f:  # change one byte near the end (the size stays the same)
        f.seek(-10, 2); b = f.read(1); f.seek(-10, 2); f.write(bytes([(b[0] + 1) % 256]))
    assert pj.locate(e, str(tmp_path / "p.tomoreveal"))[1] == "changed"
    os.remove(d["tomogram"])
    assert pj.locate(e, str(tmp_path / "p.tomoreveal")) == (None, "missing")


def test_a_file_found_by_its_name_alone_is_asked_about(demo, tmp_path):
    d = copy_demo(demo, str(tmp_path / "here" / "data")); project = str(tmp_path / "here" / "p.tomoreveal")
    e = dict(key="file:x", kind="tomogram", path=d["tomogram"])  # an autosave of an older version: nothing else is known
    assert pj.locate(e, project) == (d["tomogram"], "ok")  # in its own place
    gone = dict(e, path=str(tmp_path / "elsewhere" / os.path.basename(d["tomogram"])))
    assert pj.locate(gone, project) == (d["tomogram"], "changed")
    known = pj.file_entry("file:x", "tomogram", d["tomogram"], project)
    assert pj.locate(dict(known, path=gone["path"], relpath=None), project) == (d["tomogram"], "ok")
    os.utime(d["tomogram"], (1, 1))
    assert pj.locate(known, project)[1] == "ok"  # the fingerprint knows better than the time


def test_schema_0_is_migrated_and_newer_schemas_are_refused(demo, tmp_path):
    st = tr.Studio(headless=True)
    st.add_files([demo["tomogram"], demo["labels"]], log=lambda *a: None, make_renderer=False)
    old = tr.default_spec(st.ds)  # settings as 3.3 kept them
    p = tmp_path / "old.json"; p.write_text(json.dumps(old))
    data = pj.read_project(str(p))
    expect = json.loads(json.dumps(old)); expect.setdefault("look", {})["style35"] = {"preset": "classic"}  # 3.5: old = Classic
    assert data["schema"] == pj.SCHEMA and data["spec"] == expect and data["sources"] == []
    p.write_text(json.dumps(dict(format=pj.FORMAT, schema=99, spec=old)))
    with pytest.raises(UserError, match=r"newer TomoReveal \(schema 99\)\. Please update TomoReveal"):
        pj.read_project(str(p))
    p.write_text(json.dumps(dict(format=pj.FORMAT, schema=1, spec=old, sources=[], future_key={"x": 1})))
    assert pj.read_project(str(p))["future_key"] == {"x": 1}  # unknown keys are kept


def test_atomic_write_keeps_the_previous_file(tmp_path, monkeypatch):
    p = str(tmp_path / "a.tomoreveal")
    pj.write_atomic(p, {"v": 1}); pj.write_atomic(p, {"v": 2})
    assert json.load(open(p))["v"] == 2 and json.load(open(p + ".bak"))["v"] == 1

    def boom(fd):
        raise OSError("disk full")
    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        pj.write_atomic(p, {"v": 3})
    assert json.load(open(p))["v"] == 2 and os.path.exists(p + ".bak") and not os.path.exists(p + ".tmp")


def test_keys_follow_moved_files():
    m = {"/old/a.mrc": "/new/a.mrc"}
    spec = {"classes": {"labels:/old/a.mrc#2": {"name": "x"}, "labels:/old/ab.mrc#1": {}},
            "shots": [{"target": {"class": "labels:/old/a.mrc#2"}}], "tomo": "session:/old/a.mrc:0"}
    out = pj.remap(spec, m)
    assert set(out["classes"]) == {"labels:/new/a.mrc#2", "labels:/old/ab.mrc#1"}
    assert out["shots"][0]["target"]["class"] == "labels:/new/a.mrc#2" and out["tomo"] == "session:/new/a.mrc:0"


# ---------------------------------------------------------------------------------------------------------------
# chunked movies
# ---------------------------------------------------------------------------------------------------------------
def long_spec(st, n=700, fps=30):
    spec = tr.default_spec(st.ds); spec["output"].update(size=[320, 180], fps=fps, aa=False)
    frames, _ = tr.build_timeline(spec, st.ds)
    spec["shots"].append(tr.storyboard.new_part("hold", seconds=(n - len(frames)) / fps))
    assert len(tr.build_timeline(spec, st.ds)[0]) == n
    return spec


def test_chunked_movie_joins_and_resumes(studio, tmp_path):
    from tomoreveal.encode import count_frames, read_partial
    spec = long_spec(studio)
    out = str(tmp_path / "long.mp4")
    n, _ = tr.render_movie(studio.ren, spec, out, poster=False)
    assert n == 700 and count_frames(out) == 700
    assert sorted(os.listdir(tmp_path)) == ["long.mp4"]  # no partial files left
    # interrupted after the first chunk (as a crash would leave it), then continued
    out2 = str(tmp_path / "again.mp4")
    job = tr.movie_frames(studio.ren, spec, out2, poster=False)
    for i, total, _ in job:
        if i == 350:
            break
    job.close(keep=True)  # as a crash leaves it
    rec = read_partial(out2)
    assert rec["chunks_done"] == 1 and rec["frames_done"] == 300 and not os.path.exists(out2)
    job = tr.movie_frames(studio.ren, spec, out2, poster=False, resume=True)
    assert job.start == 300
    for _ in job:
        pass
    assert count_frames(out2) == 700 and read_partial(out2) is None
    # other settings: the old chunks are not used
    job = tr.movie_frames(studio.ren, spec, out2, poster=False)
    next(job); job.close(keep=True)
    spec["style"]["bg"] = "#ffffff"
    assert tr.movie_frames(studio.ren, spec, out2, poster=False, resume=True).start == 0


def test_cancel_deletes_partial_files(studio, tmp_path):
    spec = long_spec(studio)
    out = str(tmp_path / "c.mp4")
    job = tr.movie_frames(studio.ren, spec, out, poster=False)
    for i, _, _ in job:
        if i == 305:
            break
    job.close()
    assert os.listdir(tmp_path) == []


# ---------------------------------------------------------------------------------------------------------------
# the window
# ---------------------------------------------------------------------------------------------------------------
QtCore = pytest.importorskip("PySide6.QtCore")


def new_window(tmp_path, files=()):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=list(files), outdir=str(tmp_path / "movies"), headless=True))
    return app, win


def wait(app, win, start=None):
    if start:
        start()
    if win.worker is not None and win.worker.isRunning():
        loop = QtCore.QEventLoop(); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(40):
        app.processEvents()


def pause(app, ms):
    loop = QtCore.QEventLoop(); QtCore.QTimer.singleShot(ms, loop.quit); loop.exec()


def until(app, done, seconds=10.0):
    """Wait for something that comes after several timers (the threshold's, the surfaces', the undo history's)."""
    import time
    t = time.monotonic()
    while not done() and time.monotonic() - t < seconds:
        pause(app, 20)
    return done()


@pytest.fixture
def errors():
    errs = []; old = sys.excepthook
    sys.excepthook = lambda t, v, tb: errs.append(v)
    yield errs
    sys.excepthook = old


def change_things(win):
    k = next(iter(win.spec["classes"]))
    win.spec["classes"][k]["name"] = "outer membrane"; win.spec["classes"][k]["color"] = "#123456"
    win.topac_s.setValue(55)
    win.add_focus({"class": k}, "outer membrane")
    win.quality_cb.setCurrentIndex(0); win.name_le.setText("my movie")
    return k


def test_save_close_open(demo, tmp_path, errors):
    d = copy_demo(demo, str(tmp_path / "data"))
    app, win = new_window(tmp_path)
    wait(app, win, lambda: win.load(paths=[d["tomogram"], d["labels"]]))
    if win.st.ren is None:
        pytest.skip("3D rendering is not available here")
    assert win.windowTitle() == "TomoReveal – Untitled"
    change_things(win); wait(app, win); pause(app, 500)
    assert win.windowTitle().endswith(" *") and win.project.dirty
    path = str(tmp_path / "data" / "demo.tomoreveal")
    assert win.project.save_to(path)
    assert win.windowTitle() == "TomoReveal – demo.tomoreveal" and not win.project.dirty
    spec = json.loads(json.dumps(win.spec)); frame = win.st.plan_frame(win.spec_for(), {"t": 0.6})
    img = win.st.ren.draw(frame[0].frames[frame[1]], win.spec_for(), (320, 180))
    win.close()
    app, win = new_window(tmp_path)
    wait(app, win, lambda: win.project.open(path))
    assert win.spec == spec
    assert win.info["tomogram"] == "file:" + d["tomogram"] and [s["enabled"] for s in win.info["segmentation_sources"]] == [True]
    assert win.quality_cb.currentIndex() == 0 and win.name_le.text() == "my movie"
    frame = win.st.plan_frame(win.spec_for(), {"t": 0.6})
    assert np.array_equal(win.st.ren.draw(frame[0].frames[frame[1]], win.spec_for(), (320, 180)), img)
    assert not win.project.dirty and win.project.recent()[0] == path
    win.close()
    assert not errors, errors


def test_moved_folder_opens_without_asking(demo, tmp_path, errors, monkeypatch):
    from tomoreveal.gui_project import ProjectManager
    d = copy_demo(demo, str(tmp_path / "a" / "data"))
    app, win = new_window(tmp_path)
    wait(app, win, lambda: win.load(paths=[d["tomogram"], d["labels"]]))
    if win.st.ren is None:
        pytest.skip("3D rendering is not available here")
    k = change_things(win)
    assert win.project.save_to(str(tmp_path / "a" / "p.tomoreveal")); win.close()
    shutil.move(str(tmp_path / "a"), str(tmp_path / "b"))
    asked = []
    monkeypatch.setattr(ProjectManager, "ask_relink", lambda self, problems: asked.append(problems) or True)
    app, win = new_window(tmp_path)
    wait(app, win, lambda: win.project.open(str(tmp_path / "b" / "p.tomoreveal")))
    assert not asked
    new_k = k.replace(str(tmp_path / "a"), str(tmp_path / "b"))
    assert win.spec["classes"][new_k]["name"] == "outer membrane"
    assert win.spec["shots"][-1]["target"]["class"] == new_k
    assert len(win.info["structures"]) == 4
    win.close()
    # a missing file is reported; skipped, its settings stay
    os.remove(str(tmp_path / "b" / "data" / "demo_labels.mrc"))
    monkeypatch.setattr(ProjectManager, "ask_relink", lambda self, problems: asked.append(problems) or True)
    app, win = new_window(tmp_path)
    wait(app, win, lambda: win.project.open(str(tmp_path / "b" / "p.tomoreveal")))
    assert [r["status"] for r in asked[-1]] == ["missing"]
    # the skipped file was not found anywhere, so its structures keep the keys saved in the project
    assert win.info["structures"] == [] and k in win.spec["classes"] and win.spec["shots"][-1]["target"]["class"] == k
    assert win.spec["classes"][k]["name"] == "outer membrane"
    win.close()
    assert not errors, errors


@pytest.mark.skipif(os.name != "nt", reason="needs a disk that ignores letter case")
def test_a_folder_whose_letter_case_changed(work, tmp_path):
    app, win, d = work; path = str(tmp_path / "p.tomoreveal")
    old = next(iter(win.spec["classes"])); assert win.project.save_to(path); win.close()
    os.rename(tmp_path / "data", tmp_path / "Data_"); os.rename(tmp_path / "Data_", tmp_path / "DATA")
    app, win = new_window(tmp_path); wait(app, win, lambda: win.project.open(path))
    key = old.replace(str(tmp_path / "data"), str(tmp_path / "DATA"))
    assert key != old and [c["key"] for c in win.info["structures"]][0] == key and old not in win.spec["classes"]
    assert win.spec["classes"][key]["name"] == "outer membrane" and win.spec["classes"][key]["color"] == "#123456"
    assert win.spec["shots"][-1]["target"]["class"] == key
    win.close()


def test_an_autosave_knows_its_files(work, monkeypatch):
    app, win, d = work
    assert win.project.autosave_now()
    data = pj.read_project(win.project.autosave.path)
    assert [r["status"] for r in pj.resolve_sources(data)] == ["ok", "ok"] and all(e["size"] and e["mtime"] for e in data["sources"])
    assert not any(e["fingerprint"] for e in data["sources"])  # nothing is read for an autosave
    t = os.path.getmtime(d["tomogram"]); os.utime(d["tomogram"], (t + 60, t + 60))  # another file of the same name and size
    assert [r["status"] for r in pj.resolve_sources(data)] == ["changed", "ok"]

    def touched(*a, **k):
        raise AssertionError("a file was touched")
    with monkeypatch.context() as m:
        for name in ("getsize", "getmtime", "exists"):
            m.setattr(os.path, name, touched)
        m.setattr(pj, "fingerprint", touched)
        assert win.project.dirty  # asked after every change


def test_looking_changes_nothing(work, tmp_path):
    """An older project: opening every part and clicking the items on the picture is not a change."""
    from PySide6 import QtTest, QtWidgets
    app, win, d = work; path = str(tmp_path / "p.tomoreveal")
    win.editor.add("title_card"); win.title_le.setText("a title"); wait(app, win)
    assert win.project.save_to(path); win.close()
    data = pj.read_project(path); sp = data["spec"]
    sp["style"].pop("tomogram", None); card = None
    for p in sp["shots"]:
        if p["type"] == "card":
            p.pop("background"); p.pop("blocks"); card = p["id"]
        else:
            p["tomogram"] = {"mode": "default"}
    pj.write_atomic(path, data)
    app, win = new_window(tmp_path); wait(app, win, lambda: win.project.open(path)); pause(app, 800)
    win.tabs.setCurrentIndex(win.TAB_MOVIE)
    assert not win.project.dirty and len(win.editor.history.states) == 1
    for p in list(win.spec["shots"]):
        win.parts_panel.open(p["id"]); wait(app, win); pause(app, 700)
        assert not win.project.dirty, p["type"]
    over = next(p for p in win.spec["shots"] if p["type"] == "overview"); win.parts_panel.open(over["id"]); wait(app, win)
    win.prev_timer.stop(); win.do_preview()
    od = win.prev.overlay; rects = od.rects(); shown = [n for n in ("legend", "slice_label", "title") if rects.get(n, (0, False))[1]]
    assert shown == ["legend", "slice_label", "title"]
    for n in shown:
        b = rects[n][0]; at = win.prev.to_widget((b[0] + b[2]) / 2, (b[1] + b[3]) / 2).toPoint()
        QtTest.QTest.mouseClick(win.prev, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, at)
    pause(app, 800)
    assert not win.project.dirty and len(win.editor.history.states) == 1
    # changing still works, and is one undo step
    cb = win.parts_panel.rows[over["id"]]["tomo"].findChildren(QtWidgets.QComboBox)[0]
    cb.setCurrentIndex(cb.findData("hidden")); cb.activated.emit(cb.currentIndex()); pause(app, 800)
    assert over["tomogram"]["mode"] == "hidden" and "slice" not in over and len(win.editor.history.states) == 2
    hp = od.handle_point("title"); start = win.prev.to_widget(*hp).toPoint(); box = od.rects()["title"][0]
    QtTest.QTest.mousePress(win.prev, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
    for k in (10, 30, 60):
        QtTest.QTest.mouseMove(win.prev, start + QtCore.QPoint(k, -k))
    QtTest.QTest.mouseRelease(win.prev, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start + QtCore.QPoint(60, -60)); wait(app, win)
    t = win.spec["overlays"]["title"]
    assert t["size"] > 1.1 and t["x"] is not None and t["y"] is not None
    win.prev_timer.stop(); win.do_preview(); grown = od.rects()["title"][0]
    assert abs(grown[0] - box[0]) < 0.02 and abs(grown[3] - box[3]) < 0.02 and grown[2] - grown[0] > box[2] - box[0]  # away from its corner
    win.parts_panel.open(card); row = win.parts_panel.rows[card]["details"]
    next(b for b in row.findChildren(QtWidgets.QPushButton) if b.text() == "+ Text").click(); wait(app, win)
    assert len(win.parts_panel.part(card)["blocks"]) == 1
    win.close()


def test_autosave_and_restore(demo, tmp_path, errors, monkeypatch):
    from tomoreveal.gui_project import ProjectManager
    app, win = new_window(tmp_path)
    wait(app, win, lambda: win.load(paths=[demo["tomogram"], demo["labels"]]))
    if win.st.ren is None:
        pytest.skip("3D rendering is not available here")
    change_things(win); wait(app, win)
    pause(app, 3600)  # the autosave debounce is 3 s
    auto = win.project.autosave.path
    assert os.path.exists(auto) and json.load(open(auto))["spec"] == win.spec
    spec = json.loads(json.dumps(win.spec))
    # a crash: the window goes without a clean exit, and its process is gone
    win.project.clean_exit = lambda: None; win.close()
    lock = auto[:-len(pj.EXT)] + ".lock"
    json.dump({"pid": 2 ** 22 + 12345}, open(lock, "w"))
    assert not pj.pid_alive(2 ** 22 + 12345)
    offers = []
    monkeypatch.setattr(ProjectManager, "ask_restore", lambda self, when: offers.append(when) or "restore")
    app, win = new_window(tmp_path)
    for _ in range(5):
        app.processEvents()
    wait(app, win)
    assert len(offers) == 1 and win.spec == spec
    assert win.windowTitle() == "TomoReveal – Untitled *" and not os.path.exists(auto)
    win.close()
    assert pj.orphaned_autosaves() == []  # a clean exit leaves nothing behind
    assert not errors, errors


# ---------------------------------------------------------------------------------------------------------------
# no work is lost
# ---------------------------------------------------------------------------------------------------------------
@pytest.fixture
def work(demo, tmp_path, errors, needs_3d):
    """A window with the demo data of its own, and unsaved changes."""
    d = copy_demo(demo, str(tmp_path / "data"))
    app, win = new_window(tmp_path)
    wait(app, win, lambda: win.load(paths=[d["tomogram"], d["labels"]]))
    change_things(win); wait(app, win)
    yield app, win, d
    win.gen = None; win.close()
    assert not errors, errors


@pytest.fixture
def asked(monkeypatch):
    """Every question of the window, and the answers to give ({question: answer})."""
    from tomoreveal.gui_project import ProjectManager
    log, answers = [], {"save": "cancel"}

    def ask_save(self, what):
        log.append("save"); return answers["save"]
    monkeypatch.setattr(ProjectManager, "ask_save", ask_save)
    monkeypatch.setattr(ProjectManager, "ask_revert", lambda self: log.append("revert") or answers["revert"], raising=False)
    return log, answers


def test_the_start_over_button_asks_about_unsaved_changes(work, asked):
    app, win, d = work; log, answers = asked
    spec = win.spec
    win.data.over_btn.click()
    assert log == ["save"] and win.spec is spec and win.info["can_show"]
    answers["save"] = "discard"; win.data.over_btn.click()
    assert win.spec is None


def scores(d, *names):
    """Score files of the demo's size (a block of 0.9 in 0.1), one per name."""
    from tomoreveal.volumes import load_volume, write_mrc
    a, vox = load_volume(d["tomogram"]); out = []
    for i, n in enumerate(names):
        s = np.full(a.shape, 0.1, np.float32); s[5:15, 10 + 20 * i:25 + 20 * i, 10:40] = 0.9; s[7:13, 12 + 20 * i:23 + 20 * i, 12:38] = 0.97
        out.append(os.path.join(os.path.dirname(d["tomogram"]), f"demo__{n}.mrc")); write_mrc(out[-1], s, vox)
    return out


def busy_with_playback(app, win):
    win.start_play(); assert win.playing


def busy_with_a_threshold(app, win):
    key = next(c["key"] for c in win.info["structures"] if c["kind"] == "score")
    win._thr_changed(key, 0.95); assert win.thr_timer.isActive()


def busy_with_a_glide(app, win):
    win.nav.glide(dict(win.nav.view(), zoom=2.0), seconds=0.4); assert win.nav.anim.isActive()


def busy_with_typing(app, win):
    from PySide6 import QtWidgets
    win.editor.add("title_card"); wait(app, win)
    ed = win.parts_panel.rows[win.selected_part]["details"].findChildren(QtWidgets.QPlainTextEdit)[0]
    ed.setPlainText("a title")


def busy_with_a_path(app, win):
    import time
    import tomoreveal.paths as paths
    real = paths.centre_line
    paths.centre_line = lambda *a, **k: time.sleep(0.2) or real(*a, **k)
    try:
        win.editor.insert(tr.storyboard.new_part("path", source=dict(kind="follow", items=[{"structure": win.st.ds.key_of(2), "component": None}])))
        win.prev_timer.stop(); win.do_preview()
        assert win._path_worker.isRunning()
        pause(app, 20)
    finally:
        paths.centre_line = real


def busy_with_clicks(app, win):
    win.pick_btn.setChecked(True)
    pid = next(p["id"] for p in win.spec["shots"] if p["type"] == "focus")
    from tomoreveal import gui_clicks as clk
    win.pick_btn.setChecked(False); win.parts_panel.open(pid); win.clicks.start(clk.CloseupObjects(pid))


def busy_with_a_label(app, win):
    from tomoreveal.gui_annotations import add_text_at
    add_text_at(win, (0.4, 0.4)); pause(app, 50)
    assert win.label_editor is not None
    win.label_editor.setText("half a wo")


@pytest.mark.parametrize("busy", [busy_with_playback, busy_with_a_threshold, busy_with_a_glide, busy_with_typing, busy_with_a_path,
                                  busy_with_clicks, busy_with_a_label])
def test_new_ends_what_is_running(work, errors, busy):
    app, win, d = work
    p = scores(d, "a"); wait(app, win, lambda: win.load(paths=p, features=p))
    win.tabs.setCurrentIndex(win.TAB_MOVIE); win.prev_timer.stop(); win.do_preview()
    busy(app, win)
    win.project.new()
    assert win.spec is None and not win.playing and not win.editor.history.can_undo
    pause(app, 1000)
    assert not errors, errors
    assert not win.thr_timer.isActive() and not win.editor.history.states and win.clicks.mode is None
    assert win.label_editor is None
    assert win.prev.text().startswith("Drop files")  # nothing was drawn over the empty page


def test_a_threshold_never_reaches_another_structure(work):
    app, win, d = work
    a, b = scores(d, "a", "b")
    wait(app, win, lambda: win.load(paths=[a, b], features=[a, b]))

    def threshold(p):
        return win.st._source("scores:" + win.st._norm(p))["thr"]
    before = threshold(b)
    win._thr_changed("scores:" + win.st._norm(a) + "#1", 0.95)
    wait(app, win, lambda: win.data.remove("scores:" + win.st._norm(a))); pause(app, 600)
    assert threshold(b) == before and not win.thr_timer.isActive()


def test_revert_has_a_question_of_its_own(work, asked, tmp_path):
    app, win, d = work; log, answers = asked
    assert win.project.save_to(str(tmp_path / "p.tomoreveal"))
    saved = json.loads(json.dumps(win.spec))
    win.set_view(zoom=2.5); changed = json.loads(json.dumps(win.spec))
    answers["revert"] = False
    wait(app, win, win.project.revert)
    assert log == ["revert"] and win.spec == changed
    answers["revert"] = True
    wait(app, win, win.project.revert)
    assert log == ["revert", "revert"] and win.spec == saved and not win.project.dirty


def test_closing_during_a_movie_also_asks_about_unsaved_changes(work, asked, monkeypatch):
    from PySide6 import QtWidgets
    app, win, d = work; log, answers = asked
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: log.append("movie") or QtWidgets.QMessageBox.Yes)
    win.name_le.setText("half"); win.make_movie()
    assert win.gen is not None
    while win.gen is not None and win.progress.value() < 3:
        app.processEvents()
    auto = win.project.autosave.path
    assert win.project.dirty and os.path.exists(auto)
    win.close()
    assert log == ["movie", "save"] and win.isVisible()
    assert win.gen is not None and os.path.exists(auto)  # Cancel: the movie goes on
    n = win.progress.value()
    for _ in range(50):
        app.processEvents()
    assert win.progress.value() > n
    answers["save"] = "discard"; win.close()
    assert win.gen is None and not win.isVisible() and not os.path.exists(auto)


@pytest.fixture
def orphan(work, monkeypatch):
    """An autosave of unsaved work, as a crash leaves it: its window is gone."""
    from tomoreveal.gui_project import ProjectManager
    app, win, d = work
    path = os.path.join(pj.autosave_dir(), "dead.tomoreveal")
    pj.write_atomic(path, dict(win.project.data(), autosave_of=None, unsaved=True))
    spec = json.loads(json.dumps(win.spec)); win.close()
    monkeypatch.setattr(ProjectManager, "ask_restore", lambda self, when: "restore")
    yield path, spec, d
    pj.discard_autosave(path)


def autosaves():
    return sorted(f for f in os.listdir(pj.autosave_dir()) if f.endswith(pj.EXT))


def test_a_failed_restore_keeps_the_autosave(orphan, tmp_path, monkeypatch):
    path, spec, d = orphan

    def broken(*a, **k):
        raise OSError("the disk is gone")
    with monkeypatch.context() as m:
        m.setattr(pj, "load_into", broken)
        app, win = new_window(tmp_path)
        wait(app, win); wait(app, win)
        assert "the disk is gone" in win.msg_lbl.text() and os.path.exists(path)
        win.close()
    with monkeypatch.context() as m:  # it loads, but our own autosave can't be written
        m.setattr(pj.Autosave, "write", broken)
        app, win = new_window(tmp_path)
        wait(app, win); wait(app, win)
        assert win.spec == spec and os.path.exists(path)
        win.close()
    app, win = new_window(tmp_path)
    wait(app, win); wait(app, win)
    assert win.spec == spec and autosaves() == [os.path.basename(win.project.autosave.path)]
    win.close()


def test_files_of_the_command_line_wait_for_the_restore_question(orphan, demo, tmp_path, monkeypatch):
    from tomoreveal.gui_project import ProjectManager
    path, spec, d = orphan; seen = []

    def ask(answer):
        def ask_restore(self, when):
            loop = QtCore.QEventLoop(); QtCore.QTimer.singleShot(1000, loop.quit); loop.exec()  # the user thinks about it
            seen.append(self.win.worker); return answer
        return ask_restore
    monkeypatch.setattr(ProjectManager, "ask_restore", ask("restore"))
    app, win = new_window(tmp_path, [demo["tomogram"]])
    pause(app, 1500); wait(app, win); pause(app, 400); wait(app, win)
    assert seen == [None] and win.spec == spec
    assert list(win.st.tomo_files) == [win.st._norm(d["tomogram"])] and autosaves() == [os.path.basename(win.project.autosave.path)]
    win.close()
    pj.write_atomic(path, json.loads(json.dumps(dict(spec=spec, schema=pj.SCHEMA, sources=[]))))
    monkeypatch.setattr(ProjectManager, "ask_restore", ask("discard"))
    app, win = new_window(tmp_path, [demo["tomogram"]])
    pause(app, 1500); wait(app, win)
    assert seen == [None, None] and list(win.st.tomo_files) == [win.st._norm(demo["tomogram"])] and not os.path.exists(path)
    win.close()


def test_a_restored_untitled_project_stays_unsaved(orphan, asked, tmp_path):
    path, spec, d = orphan; log, answers = asked
    app, win = new_window(tmp_path)
    wait(app, win); wait(app, win)
    assert win.spec == spec and win.project.dirty
    wait(app, win, lambda: win.data.switch("labels:" + win.st._norm(d["labels"]), False))
    assert win.project.dirty and win.windowTitle().endswith("*")
    win.close()
    assert log == ["save"] and win.isVisible()
    answers["save"] = "discard"; win.close()


def test_nothing_opens_while_a_job_runs(work, tmp_path, monkeypatch):
    app, win, d = work
    data = win.project.data(); spec = win.spec
    win.busy = True
    assert win.project.open_data(data, None) is False and win.spec is spec
    win.busy = False


def test_a_project_that_cannot_be_opened(work, tmp_path, monkeypatch):
    app, win, d = work; here = str(tmp_path / "here.tomoreveal"); other = str(tmp_path / "other.tomoreveal")
    assert win.project.save_to(here) and win.project.save_to(other)
    gone = pj.read_project(other)
    for e in gone["sources"]:
        e.update(path=str(tmp_path / "nowhere" / "x.mrc"), relpath=None)
    gone["spec"]["flip"] = ["z"]
    pj.write_atomic(other, gone)
    assert win.project.save_to(here)
    spec, info, title = win.spec, win.info, win.windowTitle()
    assert win.project.open(other) is False  # no data file was found: what is open stays open
    assert win.spec is spec and win.info is info and win.project.path == here and win.windowTitle() == title
    assert "None of the project's data files" in win.msg_lbl.text()

    real = tr.Studio._update  # the files are found, but reading them fails
    good = pj.read_project(here); good["spec"]["flip"] = ["z"]; pj.write_atomic(other, good)

    def broken(self, *a, **k):
        raise OSError("bad sector")
    with monkeypatch.context() as m:
        m.setattr(tr.Studio, "_update", broken)
        wait(app, win, lambda: win.project.open(other))
    assert win.project.path is None and win.project.pending is None
    assert win.msg_lbl.text().startswith("other.tomoreveal could not be opened: ") and "bad sector" in win.msg_lbl.text()
    assert win.st.tomo_files == {} and win.st.seg_sources == [] and win.st.flip == () and not win.data.empty_lbl.isHidden()

    wait(app, win, lambda: win.load(paths=[d["tomogram"]]))  # Save can't overwrite the project that was not opened
    names = []
    monkeypatch.setattr(type(win.project), "save_as", lambda self: names.append("asked") or False)
    assert win.project.save() is False and names == ["asked"]
    assert tr.Studio._update is real


def test_a_failed_add_keeps_the_project(work, tmp_path):
    app, win, d = work; here = str(tmp_path / "here.tomoreveal")
    assert win.project.save_to(here)
    bad = tmp_path / "bad.mrc"; bad.write_bytes(b"not a volume")
    wait(app, win, lambda: win.load(paths=[str(bad)]))
    assert win.project.path == here and not win.project.dirty and win.spec is not None and "could not be opened: " not in win.msg_lbl.text()


# ---------------------------------------------------------------------------------------------------------------
# the movie is kept while nothing can be shown
# ---------------------------------------------------------------------------------------------------------------
def remove_all(app, win, d):
    for key in ("file:" + win.st._norm(d["tomogram"]), "labels:" + win.st._norm(d["labels"])):
        wait(app, win, lambda: win.data.remove(key))
    assert win.spec is None and not win.st.can_show


def test_the_movie_is_kept_when_its_data_is_removed(work, asked, tmp_path):
    app, win, d = work; log, answers = asked; path = str(tmp_path / "p.tomoreveal")
    spec = json.loads(json.dumps(win.spec))
    wait(app, win, lambda: win.data.remove("labels:" + win.st._norm(d["labels"])))
    before = [e["path"] for e in win.project.data()["sources"]]
    wait(app, win, lambda: win.data.remove("file:" + win.st._norm(d["tomogram"])))
    assert win.spec is None and win.prev.text().startswith("Your movie is kept")
    assert win.windowTitle() == "TomoReveal – Untitled *" and win.project.act_save.isEnabled()
    win.close(); assert log == ["save"] and win.isVisible()
    assert win.project.save_to(path) and [e["path"] for e in pj.read_project(path)["sources"]] == before
    assert all(e["fingerprint"] for e in pj.read_project(path)["sources"]) and not win.project.dirty
    wait(app, win, lambda: win.load(paths=[d["tomogram"], d["labels"]]))
    assert win.spec["shots"] == spec["shots"] and win.spec["classes"] == spec["classes"] and win.project.path == path
    assert win.project.dirty and win.name_le.text() == "my movie"


def test_undo_outlives_a_change_of_data(work, tmp_path):
    from tomoreveal.volumes import load_volume, write_mrc
    app, win, d = work; h = win.editor.history; key = "labels:" + win.st._norm(d["labels"]); pause(app, 800)
    first = float(h.states[0]["view"]["zoom"]); n = len(h.states)
    win.set_view(zoom=2.5); pause(app, 800)
    assert len(h.states) == n + 1 and h.can_undo
    for on in (False, True):  # a tick taken off and put back
        wait(app, win, lambda: win.data.switch(key, on))
    win.set_view(zoom=2.6)  # still waiting for the history when the next change of data comes
    bad = tmp_path / "bad.mrc"; bad.write_bytes(b"not a volume")
    wait(app, win, lambda: win.load(paths=[str(bad)])); pause(app, 800)
    assert len(h.states) == n + 2
    remove_all(app, win, d)
    win.editor.undo()  # nothing is shown: nothing to undo
    assert win.spec is None
    wait(app, win, lambda: win.load(paths=[d["tomogram"], d["labels"]])); pause(app, 800)
    assert len(h.states) == n + 2 and win.spec["view"]["zoom"] == pytest.approx(2.6)
    p = scores(d, "a"); wait(app, win, lambda: win.load(paths=p, features=p)); pause(app, 800)  # a structure the old steps don't know
    assert len(h.states) == n + 2 and not h.can_redo
    new = next(c["key"] for c in win.info["structures"] if c["kind"] == "score")
    while h.can_undo:
        win.editor.undo(); wait(app, win)
    assert win.spec["view"]["zoom"] == pytest.approx(first) and win.spec["classes"][new]["show"] and h.can_redo
    while h.can_redo:
        win.editor.redo(); wait(app, win)
    assert win.spec["view"]["zoom"] == pytest.approx(2.6) and win.spec["classes"][new]["show"]
    k = win.st.ds.id_of(new)
    assert win.st.ren.actors[k].GetVisibility() and len(h.states) == n + 2
    wait(app, win, lambda: win.flip_volume(("z",)))  # older steps hold object numbers that mean other objects now
    assert len(h.states) == 1 and not h.can_undo
    win.set_view(zoom=2.0); pause(app, 800); assert h.can_undo
    a, vox = load_volume(d["tomogram"]); other = str(tmp_path / "other.mrc"); write_mrc(other, a[:40, :100, :90], vox)
    wait(app, win, lambda: win.load(paths=[other]))  # another grid
    assert win.info["dims"] == [90, 100, 40] and not h.can_undo


def test_a_movie_without_work_is_not_kept(work, asked):
    app, win, d = work; log, answers = asked
    answers["save"] = "discard"; win.project.new()
    wait(app, win, lambda: win.load(paths=[d["tomogram"]]))
    ids = [p["id"] for p in win.spec["shots"]]
    wait(app, win, lambda: win.data.remove("file:" + win.st._norm(d["tomogram"])))
    assert win.prev.text().startswith("Drop files") and win.windowTitle() == "TomoReveal" and not win.project.act_save.isEnabled()
    wait(app, win, lambda: win.load(paths=[d["tomogram"]]))
    assert not set(ids) & {p["id"] for p in win.spec["shots"]} and not win.project.dirty


@pytest.mark.parametrize("how", ["saved", "autosaved"])
def test_a_project_saved_while_nothing_is_shown_opens_again(work, tmp_path, monkeypatch, how):
    from tomoreveal.gui_project import ProjectManager
    app, win, d = work; path = str(tmp_path / "p.tomoreveal"); spec = json.loads(json.dumps(win.spec))
    key = "labels:" + win.st._norm(d["labels"])
    wait(app, win, lambda: win.data.remove("file:" + win.st._norm(d["tomogram"])))
    last = win.project.data()
    wait(app, win, lambda: win.data.switch(key, False))
    assert win.spec is None
    if how == "saved":
        assert win.project.save_to(path); win.close()
        app, win = new_window(tmp_path); wait(app, win, lambda: win.project.open(path))
    else:
        dead = os.path.join(pj.autosave_dir(), "dead.tomoreveal")
        assert win.project.autosave_now(); shutil.copy(win.project.autosave.path, dead); win.close()
        monkeypatch.setattr(ProjectManager, "ask_restore", lambda self, when: "restore")
        app, win = new_window(tmp_path); wait(app, win); wait(app, win)
        assert win.project.dirty and not os.path.exists(dead)
    assert win.spec["shots"] == spec["shots"] and len(win.info["structures"]) == 4  # shown, as before it was switched off
    assert win.project.data()["segmentation_enabled"] == last["segmentation_enabled"] == {key: True}
    win.close()


@pytest.mark.parametrize("fails", [False, True])
def test_saving_while_a_job_runs_keeps_every_source(work, tmp_path, monkeypatch, fails):
    import threading
    app, win, d = work; path = str(tmp_path / "p.tomoreveal"); go = threading.Event(); real = win.st._update

    def slow(*a, **k):
        go.wait(10)
        if fails:
            raise OSError("bad sector")
        return real(*a, **k)
    monkeypatch.setattr(win.st, "_update", slow)
    win.data.remove("labels:" + win.st._norm(d["labels"])); pause(app, 200)  # the source is gone from the Studio already
    try:
        assert win.busy and len(win.st.seg_sources) == 0
        assert win.project.save_to(path) and len(pj.read_project(path)["sources"]) == 2
        assert not win.project.dirty
    finally:
        go.set(); wait(app, win)
    assert win.project.dirty is (not fails) and win.project.path == path
    assert len(win.project.data()["sources"]) == (2 if fails else 1)


def test_an_opened_project_that_cannot_be_shown_waits_for_its_data(work, tmp_path, monkeypatch):
    from tomoreveal.gui_project import ProjectManager
    app, win, d = work; path = str(tmp_path / "p.tomoreveal"); key = "labels:" + win.st._norm(d["labels"])
    wait(app, win, lambda: win.data.switch(key, False))
    assert win.project.save_to(path); spec = json.loads(json.dumps(win.spec)); win.close()
    os.remove(d["tomogram"]); monkeypatch.setattr(ProjectManager, "ask_relink", lambda self, problems: True)  # skipped
    before = pj.read_project(path)
    app, win = new_window(tmp_path); wait(app, win, lambda: win.project.open(path))
    assert win.spec is None and not win.project.dirty and win.project.path == path
    assert win.prev.text().startswith("Your movie is kept") and win.windowTitle() == "TomoReveal – p.tomoreveal"
    bad = tmp_path / "bad.mrc"; bad.write_bytes(b"not a volume")
    wait(app, win, lambda: win.load(paths=[str(bad)]))
    assert win.project.path == path and not win.project.dirty
    assert win.project.save()  # nothing is lost by saving now
    after = pj.read_project(path)
    assert {k: after[k] for k in ("sources", "spec", "segmentation_enabled", "tomogram_source")} == \
        {k: before[k] for k in ("sources", "spec", "segmentation_enabled", "tomogram_source")}
    wait(app, win, lambda: win.data.switch(key, True))
    assert win.spec["shots"] == spec["shots"] and win.spec["classes"] == spec["classes"] and win.project.path == path
    win.close()


def test_other_data_never_overwrites_the_kept_project(work, asked, tmp_path):
    from tomoreveal.volumes import load_volume, write_mrc
    app, win, d = work; log, answers = asked; path = str(tmp_path / "p.tomoreveal")
    assert win.project.save_to(path); spec = json.loads(json.dumps(win.spec))
    remove_all(app, win, d)
    a, vox = load_volume(d["tomogram"]); other = str(tmp_path / "other.mrc"); write_mrc(other, a[:40, :100, :90], vox)
    wait(app, win, lambda: win.load(paths=[other]))
    assert win.project.path is None and win.windowTitle() == "TomoReveal – Untitled *" and log == []
    assert [p["id"] for p in win.spec["shots"]] == [p["id"] for p in spec["shots"]]
    assert "nothing in common" in win.msg_lbl.text() and "p.tomoreveal" in win.msg_lbl.text()
    remove = "file:" + win.st._norm(other)  # the same grid later on: an ordinary change of data
    wait(app, win, lambda: win.data.remove(remove)); wait(app, win, lambda: win.load(paths=[other]))
    assert "nothing in common" not in win.msg_lbl.text()


# ---------------------------------------------------------------------------------------------------------------
# thresholds of score volumes belong to the movie settings (G-41)
# ---------------------------------------------------------------------------------------------------------------
def two_objects(d):
    """A score file with two objects; the first one is gone above 0.6, so the second one's number changes."""
    from tomoreveal.volumes import load_volume, write_mrc
    a, vox = load_volume(d["tomogram"]); s = np.full(a.shape, 0.1, np.float32)
    s[5:15, 10:25, 10:40] = 0.6; s[25:35, 60:80, 60:90] = 0.9; s[27:33, 63:77, 63:87] = 0.97
    p = os.path.join(os.path.dirname(d["tomogram"]), "demo__two.mrc"); write_mrc(p, s, vox)
    return p


class Score:
    """The score structure of a window."""
    def __init__(self, win, p):
        self.win, self.source = win, "scores:" + win.st._norm(p); self.key = self.source + "#1"

    def set(self, v):
        self.win._thr_changed(self.key, v)

    @property
    def wanted(self):
        return self.win.spec["classes"][self.key]["threshold"]

    @property
    def now(self):
        """The threshold: what the settings, the data, its source, the lists and the picture say is the same."""
        w = self.win; k = w.st.ds.id_of(self.key); thr = w.st.ds.classes[k]["threshold"]
        info = next(c for c in w.info["structures"] if c["key"] == self.key)
        assert thr == self.wanted == w.st._source(self.source)["thr"] == info["threshold"]
        assert info["voxels"] == w.st.ds.classes[k]["voxels"] == int(w.st.ds.mask(k).sum())
        b = np.array(w.st.ren.mesh_of(k).bounds); box = w.st.ds.bbox[k][::-1]
        assert np.allclose(b[::2], [s.start for s in box], atol=1.5) and np.allclose(b[1::2], [s.stop - 1 for s in box], atol=1.5)
        return thr


@pytest.fixture
def scored(work):
    app, win, d = work; p = two_objects(d)
    wait(app, win, lambda: win.load(paths=[p], features=[p])); pause(app, 800)
    return app, win, d, Score(win, p), p


def entry_of(data):
    return next(e for e in data["sources"] if e["kind"] == "scores")


def test_undo_of_a_threshold_brings_the_objects_back(scored):
    app, win, d, sc, p = scored
    win.editor.insert(tr.storyboard.focus_shot({"class": sc.key, "component": 2}, "one")); pause(app, 800)
    pid = win.selected_part
    def target():
        return next(s for s in win.spec["shots"] if s["id"] == pid)["target"]
    was = json.loads(json.dumps(target())); box = win.st.ds.target_box({"class": sc.key, "component": 2}); assert sc.now == 0.5
    for wait_ms in (1300, 50):  # also an undo before the surface has followed
        sc.set(0.95); assert sc.wanted == 0.95 and target() == {"class": sc.key}
        pause(app, wait_ms)
        win.editor.undo(); pause(app, 600)
        assert sc.now == 0.5 and target() == was
        assert np.array_equal(win.st.ds.target_box({"class": sc.key, "component": 2}), box)
        win.editor.redo(); assert sc.now == 0.95 and target() == {"class": sc.key}
        win.editor.undo(); assert sc.now == 0.5
    win.editor.reset_history(); sc.set(0.8)  # the very first change: its undo step comes 1.25 to 1.45 s later (N-22)
    assert until(app, lambda: win.editor.history.can_undo) and sc.now == 0.8
    win.editor.undo(); assert sc.now == 0.5


def test_a_drag_of_the_threshold_is_one_undo_step(scored):
    app, win, d, sc, p = scored; win.editor.reset_history()
    for v in (0.55, 0.7, 0.8, 0.95):
        sc.set(v); pause(app, 60)
    assert until(app, lambda: win.editor.history.can_undo); pause(app, 1000)  # (and no second step after it)
    assert sc.now == 0.95 and len(win.editor.history.states) == 2


@pytest.mark.parametrize("settled", [True, False])
def test_a_threshold_is_saved_and_opened(scored, asked, tmp_path, settled):
    app, win, d, sc, p = scored; path = str(tmp_path / "p.tomoreveal"); asked[1]["save"] = "discard"
    sc.set(0.95); pause(app, 1300 if settled else 1)
    assert win.project.save_to(path); data = pj.read_project(path)
    assert entry_of(data)["threshold"] == 0.95 == data["spec"]["classes"][sc.key]["threshold"]  # 3.5.0 reads the first one
    win.project.new(); wait(app, win, lambda: win.project.open(path)); pause(app, 300)
    assert sc.now == 0.95 and not win.project.dirty
    assert win.editor.history.states[0]["classes"][sc.key]["threshold"] == 0.95


def test_saved_while_a_job_runs_has_the_new_threshold(scored, tmp_path):
    app, win, d, sc, p = scored; path = str(tmp_path / "p.tomoreveal"); sc.set(0.95)
    win.data.switch("labels:" + win.st._norm(d["labels"]), False); assert win.busy
    assert win.project.save_to(path) and entry_of(pj.read_project(path))["threshold"] == 0.95
    wait(app, win); pause(app, 600); assert sc.now == 0.95


def test_opening_takes_the_threshold_of_the_source_entry(scored, asked, tmp_path):
    """3.5.0 knows no threshold in the settings: it changes the source entry and carries the other one along."""
    app, win, d, sc, p = scored; path = str(tmp_path / "p.tomoreveal"); asked[1]["save"] = "discard"
    sc.set(0.8); pause(app, 1300); assert win.project.save_to(path); saved = pj.read_project(path)
    for enabled in (True, False):
        data = json.loads(json.dumps(saved)); entry_of(data)["threshold"] = 0.95
        data["segmentation_enabled"][sc.source] = enabled; pj.write_atomic(path, data)
        wait(app, win, lambda: win.project.open(path)); pause(app, 300)
        assert sc.wanted == 0.95 and not win.project.dirty
        if not enabled:
            wait(app, win, lambda: win.data.switch(sc.source, True)); pause(app, 300)
        assert sc.now == 0.95
    data = json.loads(json.dumps(saved)); del data["spec"]["classes"][sc.key]["threshold"]  # as saved by 3.5.0
    entry_of(data)["threshold"] = 0.7; pj.write_atomic(path, data)
    wait(app, win, lambda: win.project.open(path)); pause(app, 300)
    assert sc.now == 0.7 and not win.project.dirty
    assert win.editor.history.states[0]["classes"][sc.key]["threshold"] == 0.7


def test_a_file_that_comes_back_gets_the_threshold_of_the_movie(scored):
    app, win, d, sc, p = scored; sc.set(0.95); pause(app, 1300)
    wait(app, win, lambda: win.data.remove(sc.source)); assert win.st.ds.id_of(sc.key) is None
    wait(app, win, lambda: win.load(paths=[p], features=[p])); pause(app, 300); assert sc.now == 0.95
    wait(app, win, lambda: win.data.switch(sc.source, False))
    wait(app, win, lambda: win.data.switch(sc.source, True)); pause(app, 300); assert sc.now == 0.95


def test_a_threshold_waits_while_a_movie_is_made(scored):
    app, win, d, sc, p = scored; k = win.st.ds.id_of(sc.key)
    win.gen = iter(())  # a movie is being made: its data must not change under it
    try:
        sc.set(0.95); pause(app, 900); assert win.st.ds.classes[k]["threshold"] == 0.5
        win.editor.undo(); pause(app, 600); assert sc.now == 0.5
        sc.set(0.8); pause(app, 600)
    finally:
        win.gen = None
    pause(app, 900); assert sc.now == 0.8


def test_a_look_preset_never_changes_a_threshold(scored):
    from tomoreveal.gui_presets import apply_preset, preset_of
    app, win, d, sc, p = scored; sc.set(0.95); pause(app, 1300); preset = preset_of(win.spec)
    sc.set(0.8); pause(app, 1300); apply_preset(win.spec, preset); win.build_controls(); pause(app, 800)
    assert sc.now == 0.8


def test_a_kept_movie_is_saved_with_its_threshold(scored, tmp_path):
    app, win, d, sc, p = scored; path = str(tmp_path / "p.tomoreveal")
    for key in ("file:" + win.st._norm(d["tomogram"]), "labels:" + win.st._norm(d["labels"])):
        wait(app, win, lambda: win.data.remove(key))
    sc.set(0.95); wait(app, win, lambda: win.data.remove(sc.source)); assert win.spec is None and win.project.loaded
    assert win.project.save_to(path); data = pj.read_project(path)
    assert entry_of(data)["threshold"] == 0.95 == data["spec"]["classes"][sc.key]["threshold"]
    wait(app, win, lambda: win.load(paths=[p], features=[p])); pause(app, 300); assert sc.now == 0.95
