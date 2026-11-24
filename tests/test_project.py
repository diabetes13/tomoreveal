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
    job.crash()
    rec = read_partial(out2)
    assert rec["chunks_done"] == 1 and rec["frames_done"] == 300 and not os.path.exists(out2)
    job = tr.movie_frames(studio.ren, spec, out2, poster=False, resume=True)
    assert job.start == 300
    for _ in job:
        pass
    assert count_frames(out2) == 700 and read_partial(out2) is None
    # other settings: the old chunks are not used
    job = tr.movie_frames(studio.ren, spec, out2, poster=False)
    next(job); job.crash()
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


def new_window(tmp_path):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path / "movies"), headless=True))
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
