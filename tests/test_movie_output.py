"""Movie files: the old movie and poster are replaced only by a finished render (F-03); frames folders (F-10, F-24)."""
import glob
import os
import sys

import pytest

import tomoreveal as tr
from tomoreveal import render
from tomoreveal.common import UserError


def short_spec(studio):
    spec = tr.default_spec(studio.ds)
    for s in spec["shots"]:
        s["seconds"] = 0.3
    spec["output"].update(size=[160, 90], fps=10, aa=False)
    return spec


@pytest.fixture
def old(tmp_path):
    """An earlier movie and poster in the output folder."""
    out = str(tmp_path / "movie.mp4"); poster = str(tmp_path / "movie_poster.png")
    open(out, "wb").write(b"old movie"); open(poster, "wb").write(b"old poster")
    return out, poster


def unchanged(old):
    out, poster = old
    return open(out, "rb").read() == b"old movie" and open(poster, "rb").read() == b"old poster"


def leftovers(old):
    return glob.glob(os.path.join(os.path.dirname(old[0]), "*.part*"))


def test_failed_render_keeps_the_old_movie_and_poster(studio, old, monkeypatch):
    real, n = studio.ren.draw, [0]

    def fails_at_frame_5(*a, **k):
        n[0] += 1
        if n[0] == 5:
            raise RuntimeError("graphics problem at frame 5")
        return real(*a, **k)
    monkeypatch.setattr(studio.ren, "draw", fails_at_frame_5)
    with pytest.raises(RuntimeError, match="frame 5"):
        tr.render_movie(studio.ren, short_spec(studio), old[0])
    assert unchanged(old) and not leftovers(old)


def test_cancelled_render_keeps_the_old_movie_and_poster(studio, old):
    gen = tr.movie_frames(studio.ren, short_spec(studio), old[0])
    for _ in range(12):  # past the poster frame
        next(gen)
    gen.close()
    assert unchanged(old) and not leftovers(old)


def test_finished_render_replaces_both(studio, old):
    tr.render_movie(studio.ren, short_spec(studio), old[0])
    assert os.path.getsize(old[0]) > 2000 and open(old[1], "rb").read()[:4] == b"\x89PNG" and not leftovers(old)


def locked(monkeypatch, *paths):
    """os.replace fails onto these files, as on Windows while they are open in a player."""
    real = os.replace

    def replace(src, dst):
        if os.path.abspath(dst) in [os.path.abspath(p) for p in paths]:
            raise PermissionError(13, "the file is open in another program")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", replace)


def is_movie(path, n=None):
    from tomoreveal.encode import count_frames
    return os.path.isfile(path) and (n is None or count_frames(path) == n)


def test_a_failed_replace_keeps_the_old_movie_and_saves_the_new_one_next_to_it(studio, old, monkeypatch):
    """The old movie is open in a player (Windows): it stays, and the new one is saved as "movie (new).mp4". Before, the
    message named movie.partial.mp4, which the window then deleted (G-01)."""
    locked(monkeypatch, old[0])
    with pytest.raises(UserError, match=r"open in another program.*movie \(new\)\.mp4"):
        tr.render_movie(studio.ren, short_spec(studio), old[0])
    new = old[0][:-4] + " (new).mp4"; n = len(tr.build_timeline(short_spec(studio), studio.ds)[0])
    assert open(old[0], "rb").read() == b"old movie" and is_movie(new, n)
    assert open(old[1], "rb").read() == b"old poster" and open(new[:-4] + "_poster.png", "rb").read()[:4] == b"\x89PNG"
    assert not leftovers(old)  # the poster goes with the movie
    with pytest.raises(UserError, match=r"movie \(new 2\)\.mp4"):  # the next one does not overwrite it
        tr.render_movie(studio.ren, short_spec(studio), old[0])
    gen = tr.movie_frames(studio.ren, short_spec(studio), old[0]); next(gen); gen.close()  # nor does a cancel delete it
    assert is_movie(new, n) and is_movie(old[0][:-4] + " (new 2).mp4", n) and not leftovers(old)


def test_one_of_two_formats_open_in_a_player(studio, old, monkeypatch):
    spec = short_spec(studio); spec["output"]["formats"] = ["mp4", "mov"]
    mov = old[0][:-4] + ".mov"; open(mov, "wb").write(b"old mov")
    locked(monkeypatch, mov)
    job = tr.movie_frames(studio.ren, spec, old[0])
    for _ in job:
        pass
    assert job.paths == {"mp4": old[0], "mov": old[0][:-4] + " (new).mov"} and "(new).mov" in job.note
    assert is_movie(old[0], len(job)) and is_movie(job.paths["mov"], len(job)) and open(mov, "rb").read() == b"old mov"
    assert not leftovers(old)


def test_the_old_poster_open(studio, old, monkeypatch):
    locked(monkeypatch, old[1])
    job = tr.movie_frames(studio.ren, short_spec(studio), old[0])
    for _ in job:
        pass
    assert open(old[1], "rb").read() == b"old poster" and is_movie(old[0], len(job))
    assert job.poster == old[0][:-4] + " (new)_poster.png" and open(job.poster, "rb").read()[:4] == b"\x89PNG"
    assert "(new)_poster.png" in job.note and not leftovers(old)


def test_the_poster_goes_with_a_movie_saved_next_to_the_old_one(studio, old, monkeypatch):
    locked(monkeypatch, old[0])
    job = tr.movie_frames(studio.ren, short_spec(studio), old[0])
    for _ in job:
        pass
    assert job.poster == old[0][:-4] + " (new)_poster.png" and open(old[1], "rb").read() == b"old poster"


def test_a_movie_that_cannot_be_saved_at_all(studio, old, monkeypatch):
    locked(monkeypatch, old[0], old[0][:-4] + " (new).mp4")
    with pytest.raises(UserError, match="could not be saved"):
        tr.render_movie(studio.ren, short_spec(studio), old[0])
    assert unchanged(old)


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to replace a file that is open")
def test_old_movie_open_in_a_player(studio, old):
    with open(old[0], "rb"), open(old[1], "rb"):
        with pytest.raises(UserError, match="open in another program"):
            tr.render_movie(studio.ren, short_spec(studio), old[0])
    assert unchanged(old) and is_movie(old[0][:-4] + " (new).mp4") and os.path.isfile(old[0][:-4] + " (new)_poster.png")


def test_render_movie_cancelled_keeps_the_old_movie(studio, old):
    """The browser version cancels through render_movie(cancelled=...) and then deleted the movie it had overwritten."""
    done = []
    res = tr.render_movie(studio.ren, short_spec(studio), old[0], progress=lambda i, n, img: done.append(i),
                          cancelled=lambda: len(done) >= 12)
    assert res is None and unchanged(old) and not leftovers(old)


def test_replace_question_covers_an_existing_poster(tmp_path, demo, needs_3d, monkeypatch):
    import argparse
    from PySide6 import QtCore, QtWidgets
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True))
    try:
        loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
        for _ in range(30):
            app.processEvents()
        open(str(tmp_path / "movie_poster.png"), "wb").write(b"old poster")  # only the poster is there
        asked = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a: asked.append(a[2]) or QtWidgets.QMessageBox.No)
        win.folder_le.setText(str(tmp_path)); win.name_le.setText("movie")
        win.make_movie()
        assert asked and "movie_poster.png" in asked[0] and win.gen is None
    finally:
        win.close()


# ---- frames folders ----

def test_frames_dir_has_one_png_per_frame(studio, tmp_path):
    """F-10: a PNG was written only when the picture changed, so pauses and holds were missing from the sequence."""
    fdir = str(tmp_path / "frames")
    n, _ = tr.render_movie(studio.ren, short_spec(studio), str(tmp_path / "m.mp4"), frames_dir=fdir)
    assert sorted(os.listdir(fdir)) == [f"frame_{i:05d}.png" for i in range(n)]


def test_frames_dir_with_frames_beyond_the_new_movie_is_refused(studio, tmp_path):
    """F-24: old frames numbered beyond the new last frame stayed mixed in with the new sequence."""
    fdir = str(tmp_path / "frames"); out = str(tmp_path / "m.mp4")
    n, _ = tr.render_movie(studio.ren, short_spec(studio), out, frames_dir=fdir)
    shorter = short_spec(studio); shorter["shots"] = shorter["shots"][:2]
    with pytest.raises(UserError, match="frame_"):
        tr.render_movie(studio.ren, shorter, out, frames_dir=fdir)
    assert not leftovers((out,)) and len(os.listdir(fdir)) == n
    assert tr.render_movie(studio.ren, short_spec(studio), out, frames_dir=fdir)[0] == n  # the same movie again is fine


# ---- Make movie while something is still being made (G-09, G-10) ----

@pytest.fixture
def window(tmp_path, demo, needs_3d, monkeypatch):
    """The demo data in a window, with a short movie. A question nobody expects is answered No (win.asked)."""
    import argparse
    import shutil
    from PySide6 import QtCore, QtWidgets
    from tomoreveal.gui import build_gui
    data = {k: shutil.copy(demo[k], str(tmp_path / os.path.basename(demo[k]))) for k in ("tomogram", "labels")}
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path / "movies"), headless=True))
    errs = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errs.append(v)
    loop = QtCore.QEventLoop(); win.load(paths=[data["tomogram"], data["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    run_for(app, 0.3)
    assert win.st.ren is not None, "the 3D renderer did not start after loading"
    make_short(win); win.name_le.setText("m")
    win.prev_timer.stop(); win.do_preview()
    win.asked = []; monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: win.asked.append(a[2]) or QtWidgets.QMessageBox.No)
    yield app, win, errs
    sys.excepthook = old; win.render_timer.stop(); win.gen = None; win.close()


def make_short(win):
    for p in win.spec["shots"]:
        p["seconds"] = 0.3
    win.spec["output"].update(fps=10); win.quality_cb.setCurrentIndex(0); win.prev_timer.stop()


def run_for(app, seconds):
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents(); time.sleep(0.002)


def said_by(win):
    """Everything the window says from now on: (text, kind)."""
    said = []; real = type(win).say
    win.say = lambda text, kind=None: (said.append((text, kind)), real(win, text, kind))[1]
    return said


def problems_of(said):
    return [t for t, kind in said if kind == "err"]


def make(app, win):
    """Press Make movie and wait for the end; what the window said meanwhile."""
    said = said_by(win); win.make_movie(); finish(app, win)
    return said


def finish(app, win, seconds=120):
    import time
    end = time.monotonic() + seconds
    while win.gen is not None and time.monotonic() < end:
        app.processEvents()
    assert win.gen is None, "the movie did not end"
    run_for(app, 0.05)


def movie_file(win, name="m.mp4"):
    return os.path.join(win.st.outdir, name)


def drawn_with(win, monkeypatch, value):
    """The values of value() at every frame that is drawn while a movie is made."""
    got = []; ren = win.st.ren; real = ren.draw

    def draw(*a, **k):
        if win.gen is not None:
            got.append(value())
        return real(*a, **k)
    monkeypatch.setattr(ren, "draw", draw)
    return got


@pytest.mark.parametrize("name, v", [("smoothing", 3), ("min_object_voxels", 40), ("soften", 2)])
def test_make_movie_at_once_has_the_new_surfaces(window, monkeypatch, name, v):
    app, win, errs = window; ds = win.st.ds; key = ds.key_of(2); k = ds.id_of(key)
    got = drawn_with(win, monkeypatch, lambda: (ds.param(k, name), win.st.ren.mesh_of(k) is ds._mesh_cache.get(ds._cache_key(k, params={name: v}))))
    win._param_changed(key, name, v)
    said = make(app, win)
    assert got and set(got) == {(v, True)} and os.path.isfile(movie_file(win)), said[-1:]
    assert not win._surf_worker.isRunning() and not errs, errs


def test_make_movie_at_once_has_the_new_threshold(window, monkeypatch, tmp_path):
    import numpy as np
    from PySide6 import QtCore
    from tomoreveal.volumes import write_mrc
    app, win, errs = window
    z, y, x = np.mgrid[0:win.st.ds.shape[0], 0:win.st.ds.shape[1], 0:win.st.ds.shape[2]]
    ball = np.sqrt((z - 20.0) ** 2 + (y - 60.0) ** 2 + (x - 60.0) ** 2)
    p = str(tmp_path / "demo__ball.mrc"); write_mrc(p, np.clip(1 - ball / 15, 0, 1).astype(np.float32), 10.0)
    loop = QtCore.QEventLoop(); win.load(paths=[p], features=[p]); win.worker.finished.connect(loop.quit); loop.exec(); run_for(app, 0.3)
    make_short(win); ds = win.st.ds; k = next(iter(ds.scores)); key = ds.key_of(k)
    got = drawn_with(win, monkeypatch, lambda: ds.classes[k]["threshold"])
    win._thr_changed(key, 0.8)
    make(app, win)
    assert got and set(got) == {0.8}
    run_for(app, 0.6); assert ds.classes[k]["threshold"] == 0.8 and not errs, errs


def test_make_movie_at_once_has_the_text_of_a_card(window, monkeypatch):
    from PySide6 import QtWidgets
    app, win, errs = window
    win.editor.add("title_card"); run_for(app, 0.2); make_short(win); pid = win.spec["shots"][0]["id"]
    win.parts_panel.open(pid)
    ed = win.parts_panel.rows[pid]["details"].findChildren(QtWidgets.QPlainTextEdit)[0]
    got = []; ren = win.st.ren; real = ren.draw

    def draw(s, spec, *a, **k):
        if win.gen is not None:
            got.append(spec["shots"][0]["blocks"][0]["text"])
        return real(s, spec, *a, **k)
    monkeypatch.setattr(ren, "draw", draw)
    ed.setPlainText("typed just now")
    make(app, win)
    assert got and set(got) == {"typed just now"} and not errs, errs


def slow_surfaces(monkeypatch, seconds, fail=None):
    """New surfaces take this long in the worker thread; fail: an error they end with instead. Returns the calls."""
    import threading
    import time
    from tomoreveal import volumes
    calls = []; real = volumes.surface

    def slow(*a, **k):
        if threading.current_thread() is not threading.main_thread():
            calls.append(1); time.sleep(seconds)
            if fail is not None:
                raise fail
        return real(*a, **k)
    monkeypatch.setattr(volumes, "surface", slow)
    return calls


@pytest.mark.parametrize("how", ["cancel", "close"])
def test_cancel_and_close_while_make_movie_waits(window, monkeypatch, how):
    from PySide6 import QtWidgets
    app, win, errs = window; key = win.st.ds.key_of(2)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Yes)
    slow_surfaces(monkeypatch, 0.6)
    win._param_changed(key, "smoothing", 3); spec = win.spec_for(); win.make_movie(); run_for(app, 0.2)
    assert win.gen is not None and win.job is None and "surface" in win.status_lbl.text() and not win.pages[win.TAB_STRUCT].isEnabled()
    if how == "close":
        win.close()
    else:
        win.cancel_render(); assert not win.go_btn.isHidden() and win.pages[win.TAB_STRUCT].isEnabled()
    assert win.gen is None
    run_for(app, 1.5)  # no late start
    assert win.gen is None and win.job is None and not os.path.exists(movie_file(win))
    if how == "cancel":
        assert win.spec_for() == spec and win.st.ds.param(win.st.ds.id_of(key), "smoothing") == 3  # the surface came all the same
    assert not errs, errs


def test_a_surface_that_fails_ends_make_movie_with_one_message(window, monkeypatch):
    app, win, errs = window; key = win.st.ds.key_of(2)
    calls = slow_surfaces(monkeypatch, 0.1, MemoryError("not enough memory"))
    win._param_changed(key, "smoothing", 3); said = make(app, win); bad = problems_of(said)
    assert len(bad) == 1 and "not enough memory" in bad[0] and not os.path.exists(movie_file(win))
    run_for(app, 1.0); assert len(calls) == 1 and win.msg_lbl.text() == bad[0]
    assert problems_of(make(app, win)) == bad and len(calls) == 1  # said again, not made again
    assert not os.path.exists(movie_file(win))
    monkeypatch.undo(); win._param_changed(key, "smoothing", 4); make(app, win)
    assert os.path.isfile(movie_file(win)) and not errs, errs


def test_a_surface_that_failed_before_make_movie(window, monkeypatch):
    app, win, errs = window; key = win.st.ds.key_of(2)
    calls = slow_surfaces(monkeypatch, 0.0, MemoryError("not enough memory"))
    win._param_changed(key, "smoothing", 3); run_for(app, 1.0); assert len(calls) == 1
    bad = problems_of(make(app, win))
    assert len(bad) == 1 and "not enough memory" in bad[0] and len(calls) == 1 and not os.path.exists(movie_file(win))
    assert not errs, errs


def add_a_part_that_cannot_be_drawn(win):
    from tomoreveal import storyboard as sb
    win.editor.insert(sb.new_part("focus", target={"class": "labels:gone#9"}), win.spec["shots"][-1]["id"])
    make_short(win); win.do_preview()


@pytest.mark.parametrize("yes", [True, False])
@pytest.mark.parametrize("waits", [True, False])
def test_parts_that_are_left_out_are_asked_about_once(window, monkeypatch, yes, waits):
    from PySide6 import QtWidgets
    app, win, errs = window; asked = []
    add_a_part_that_cannot_be_drawn(win); n = len(win.spec["shots"])

    def question(parent, title, text, *a):
        asked.append(text); run_for(app, 0.3)  # a dialog has its own event loop: timers go on
        return QtWidgets.QMessageBox.Yes if yes else QtWidgets.QMessageBox.No
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", question)
    if waits:
        slow_surfaces(monkeypatch, 0.3); win._param_changed(win.st.ds.key_of(2), "smoothing", 3)
    said = make(app, win)
    assert asked == [f"1 part will be left out: {n} (structure not loaded). Make the movie anyway?"]
    assert os.path.isfile(movie_file(win)) == yes and not problems_of(said)
    assert not win.go_btn.isHidden() and win.pages[0].isEnabled() and not errs, errs


def test_a_movie_of_which_nothing_can_be_drawn(window, monkeypatch, capfd):
    from PySide6 import QtWidgets
    app, win, errs = window; asked = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: asked.append(a) or QtWidgets.QMessageBox.Yes)
    add_a_part_that_cannot_be_drawn(win)
    for p in win.spec["shots"][:-1]:
        p["on"] = False
    capfd.readouterr(); bad = problems_of(make(app, win))
    assert len(bad) == 1 and "structure not loaded" in bad[0] and not asked and not os.path.exists(movie_file(win))
    assert "Traceback" not in capfd.readouterr().err and not errs, errs


def test_cancel_can_be_clicked_while_a_movie_is_made(window, monkeypatch):
    """The page with Cancel was the one that was locked (Presets had been moved before Movie), and Look was not (N-17)."""
    app, win, errs = window; movie_page = win.pages[[win.tabs.indexOf(p.parentWidget().parentWidget()) for p in win.pages].index(win.TAB_MOVIE)]
    slow_surfaces(monkeypatch, 0.6)
    for wait in (False, True):
        if wait:
            win._param_changed(win.st.ds.key_of(2), "smoothing", 3)
        win.make_movie(); assert (win.job is None) == wait
        assert [p for p in win.pages if p.isEnabled()] == [movie_page] and not win.movie_group.isEnabled()
        assert not win.fps_cb.isEnabled() and not win.blur_cb.isEnabled()
        assert win.cancel_btn.isEnabled(); win.cancel_btn.click()
        assert win.gen is None and all(p.isEnabled() for p in win.pages) and not os.path.exists(movie_file(win))
    run_for(app, 1.0); assert not errs, errs


def test_no_undo_changes_the_settings_while_a_movie_is_made(window, monkeypatch):
    """N-18: View > Undo view change went on working while a movie was made (the parts' Undo, its keys and the
    preview's keys are locked then). It changed the window's settings, not the movie."""
    import json
    from PySide6 import QtCore, QtTest, QtWidgets
    app, win, errs = window; slow_surfaces(monkeypatch, 0.6)
    pid = win.spec["shots"][0]["id"]; win.editor.resized(pid, 0.5)  # one undo step of the parts
    win.nav.push(force=True); win.set_view(az=win.spec["view"]["az"] + 40.0)  # and one of the view
    menu = {a.text(): a for m in win.menuBar().findChildren(QtWidgets.QMenu) for a in m.actions()}
    nav_menu = next(m for m in win.menuBar().findChildren(QtWidgets.QMenu) if menu["Undo view change"] in m.actions())

    def greyed():
        nav_menu.aboutToShow.emit()
        return not menu["Undo view change"].isEnabled() and not menu["Redo view change"].isEnabled()
    assert not greyed()
    for wait in (False, True):
        if wait:
            win._param_changed(win.st.ds.key_of(2), "smoothing", 3)
        win.make_movie(); assert win.gen is not None and (win.job is None) == wait
        assert greyed()
        before = json.loads(json.dumps(win.spec))
        ways = {f"{how} on the {name}": (lambda w=w, m=m: QtTest.QTest.keyClick(w, QtCore.Qt.Key_Z, m))
                for name, w in (("timeline", win.timeline.canvas), ("preview", win.prev))
                for how, m in (("Ctrl+Z", QtCore.Qt.ControlModifier), ("Ctrl+Shift+Z", QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier))}
        ways.update({"Undo button": win.editor.undo_btn.click, "Redo button": win.editor.redo_btn.click,
                     "Undo view change": menu["Undo view change"].trigger, "Redo view change": menu["Redo view change"].trigger})
        changed = []
        for name, do in ways.items():  # one at a time: an undo and a redo could cancel out
            do(); app.processEvents()
            if json.loads(json.dumps(win.spec)) != before:
                changed.append(name); win.spec = json.loads(json.dumps(before))
        assert not changed, changed
        win.cancel_btn.click(); assert win.gen is None and not greyed()
    win.nav.undo(); assert win.spec["view"]["az"] != before["view"]["az"]  # and afterwards it works again
    run_for(app, 1.0); assert not errs, errs


# ---- camera paths that are still being worked out (G-10) ----

def add_path(win, key):
    from tomoreveal import storyboard as sb
    p = sb.new_part("path", source=dict(kind="follow", items=[{"structure": key, "component": None}]))
    win.editor.insert(p, win.spec["shots"][-1]["id"]); make_short(win)
    return p["id"]


def slow_paths(monkeypatch, seconds=0.6, fail=None):
    """Paths take this long to work out; the one along `fail` ends with an error. Returns the structures asked for."""
    import time
    from tomoreveal import paths
    calls = []; real = paths.centre_line

    def slow(ds, items, clip=None):
        calls.append(items[0][0]); time.sleep(seconds)
        if items[0][0] == fail:
            raise RuntimeError("skeleton trouble")
        return real(ds, items, clip)
    monkeypatch.setattr(paths, "centre_line", slow)
    return calls


def test_make_movie_waits_for_a_path(window, monkeypatch):
    from tomoreveal.encode import count_frames
    app, win, errs = window; slow_paths(monkeypatch)
    pid = add_path(win, win.st.ds.key_of(1)); win.do_preview()
    assert win._path_worker.isRunning()
    make(app, win)
    plan = tr.storyboard.build_plan(win.spec_for(), win.st.ds)
    assert not win.asked and any(f["part"] == pid for f in plan.frames) and count_frames(movie_file(win)) == len(plan.frames)
    assert not errs, errs


def test_make_movie_works_out_a_path_nobody_asked_for(window, monkeypatch):
    from tomoreveal.encode import count_frames
    app, win, errs = window; calls = slow_paths(monkeypatch, 0.3)
    pid = add_path(win, win.st.ds.key_of(1))  # Make movie before the preview was drawn again
    win.make_movie(); assert win.gen is not None and win.job is None and "path" in win.status_lbl.text()
    finish(app, win)
    plan = tr.storyboard.build_plan(win.spec_for(), win.st.ds)
    assert not win.asked and any(f["part"] == pid for f in plan.frames) and count_frames(movie_file(win)) == len(plan.frames)
    assert len(calls) == 1
    assert not errs, errs


def test_a_path_that_fails_is_left_out_with_its_reason(window, monkeypatch):
    app, win, errs = window; ds = win.st.ds; bad_key, good_key = ds.key_of(2), ds.key_of(1)
    calls = slow_paths(monkeypatch, 0.05, fail=bad_key)
    bad, good = add_path(win, bad_key), add_path(win, good_key); runs = []
    real = win._compute_paths
    monkeypatch.setattr(win, "_compute_paths", lambda spec: (runs.append(1), real(spec))[1])
    win.do_preview(); run_for(app, 1.0); win.do_preview(); run_for(app, 0.5)
    problems = win.timeline.canvas.plan.problems
    assert "skeleton trouble" in problems[bad] and good not in problems
    assert sorted(calls) == sorted([bad_key, good_key]) and len(runs) == 1  # neither is tried again
    assert not errs, errs


def test_a_path_part_that_is_off_is_not_waited_for(window, monkeypatch):
    app, win, errs = window; calls = slow_paths(monkeypatch)
    pid = add_path(win, win.st.ds.key_of(1)); win.parts_panel.part(pid)["on"] = False
    win.make_movie()
    assert win.job is not None and not calls  # started at once
    finish(app, win); assert os.path.isfile(movie_file(win)) and not errs, errs


def test_a_movie_without_the_window_works_out_every_path(studio, tmp_path):
    """The window plans without waiting for paths (path_async); a movie never leaves one out for that."""
    from tomoreveal import storyboard as sb
    ds = studio.ds; spec = short_spec(studio); ds.path_cache = {}
    spec["shots"].append(dict(sb.new_part("path", source=dict(kind="follow", items=[{"structure": ds.key_of(1), "component": None}])), seconds=0.3))
    n = len(tr.build_timeline(spec, ds)[0]); ds.path_cache = {}; ds.path_async = True
    try:
        job = tr.movie_frames(studio.ren, spec, str(tmp_path / "p.mp4")); assert len(job) == n
        job.close()
    finally:
        ds.path_async = False


def test_the_size_estimate_does_not_work_out_paths(window, monkeypatch):
    app, win, errs = window; calls = slow_paths(monkeypatch, 0.0)
    add_path(win, win.st.ds.key_of(1)); win.tabs.setCurrentIndex(win.TAB_MOVIE); win.prev_timer.stop(); win.export.wait.stop()
    win.export.start()
    assert win.export.est_gen is None and win.export.wait.isActive() and not calls  # tried again 2 s later
    win.do_preview(); run_for(app, 0.6); win.prev_timer.stop(); win.export.wait.stop(); win.export.start()
    assert win.export.est_gen is not None and len(calls) == 1
    win.export.stop(); assert not errs, errs


def test_the_window_plays_the_movie_saved_next_to_the_old_one(window, monkeypatch):
    from PySide6 import QtGui
    app, win, errs = window; out = movie_file(win)
    os.makedirs(os.path.dirname(out), exist_ok=True); open(out, "wb").write(b"old movie")
    from PySide6 import QtWidgets
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Yes)  # replace it
    locked(monkeypatch, out); said = make(app, win)
    new = out[:-4] + " (new).mp4"
    assert is_movie(new) and open(out, "rb").read() == b"old movie" and not problems_of(said)
    assert "(new)" in win.status_lbl.text() and "(new)" in win.msg_lbl.text() and win.last_movie == new
    opened = []; monkeypatch.setattr(QtGui.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    win.open_btn.click(); assert [os.path.normcase(os.path.abspath(p)) for p in opened] == [os.path.normcase(new)]
    assert not errs, errs



# ---- an error keeps the chunks that are complete (G-07) ----

def fails_from(monkeypatch, k, exc=RuntimeError("graphics problem")):
    """Making frame k raises (the writer gets every frame; a picture that holds still is not drawn again)."""
    from tomoreveal import encode
    real = encode.ChunkedWriter.write

    def write(self, img):
        if self.chunk * self.chunk_frames + self.n_in_chunk >= k:
            raise exc
        return real(self, img)
    monkeypatch.setattr(encode.ChunkedWriter, "write", write)
    return lambda: monkeypatch.setattr(encode.ChunkedWriter, "write", real)


def decoded(path):
    import imageio.v2 as imageio
    with imageio.get_reader(path) as r:
        return [f for f in r]


def test_an_error_keeps_the_complete_chunks_and_the_movie_can_be_continued(window, monkeypatch, tmp_path):
    from tomoreveal import encode
    from tomoreveal.movie import poster_partial
    app, win, errs = window; out = movie_file(win); monkeypatch.setattr(encode, "CHUNK_FRAMES", 2)
    n = len(tr.storyboard.build_plan(win.spec_for(), win.st.ds).frames); assert n > 6
    healed = fails_from(monkeypatch, 5); said = make(app, win)
    assert encode.read_partial(out)["chunks_done"] == 2 and not os.path.exists(out)
    assert problems_of(said) == [f"Making the movie failed: graphics problem. 4 of {n} frames are kept. Press Make movie to continue."]
    assert win.gen is None and not win.go_btn.isHidden() and win.cancel_btn.isHidden() and win.movie_group.isEnabled()  # unlocked
    kept = [f for c in sorted(glob.glob(out[:-4] + ".partial.0*.mp4"))[:2] for f in decoded(c)]
    healed(); asked = []
    monkeypatch.setattr(win, "ask_resume", lambda part, same: (asked.append((part["frames_done"], same)), True)[1])
    make(app, win)
    assert asked == [(4, True)] and encode.read_partial(out) is None and not os.path.exists(poster_partial(out))
    ref = str(tmp_path / "ref.mp4"); job = tr.movie_frames(win.st.ren, win.spec_for(), ref, chunk_frames=2)
    for _ in job:
        pass
    a, b = decoded(out), decoded(ref)
    # the kept frames of the first movie in this window are the same as those of a movie made in one go (N-19: the first
    # movie drew its first frames without depth fade, so this compared them with the first run's chunks only)
    assert len(a) == len(b) == n and all((x == y).all() for x, y in zip(a, b)) and all((x == y).all() for x, y in zip(kept, b))
    assert not errs, errs


def test_a_continued_movie_has_the_frames_of_one_made_in_one_go(studio, tmp_path, monkeypatch):
    spec = short_spec(studio); out, ref = str(tmp_path / "c.mp4"), str(tmp_path / "ref.mp4")
    for _ in tr.movie_frames(studio.ren, spec, ref, chunk_frames=2):
        pass
    job = tr.movie_frames(studio.ren, spec, out, chunk_frames=2); heal = fails_from(monkeypatch, 5)
    with pytest.raises(RuntimeError, match="graphics"):
        for _ in job:
            pass
    assert job.close(keep=True) == 4
    heal(); job = tr.movie_frames(studio.ren, spec, out, chunk_frames=2, resume=True); assert job.start == 4
    for _ in job:
        pass
    a, b = decoded(out), decoded(ref)
    assert len(a) == len(b) == len(job) and all((x == y).all() for x, y in zip(a, b))
    assert open(out[:-4] + "_poster.png", "rb").read() == open(ref[:-4] + "_poster.png", "rb").read()


def test_an_error_before_the_first_chunk_leaves_nothing(window, monkeypatch):
    from tomoreveal import encode
    app, win, errs = window; out = movie_file(win); monkeypatch.setattr(encode, "CHUNK_FRAMES", 4)
    fails_from(monkeypatch, 3); said = make(app, win)
    assert problems_of(said) == ["Making the movie failed: graphics problem"]
    assert encode.read_partial(out) is None and not glob.glob(os.path.join(os.path.dirname(out), "*.part*"))
    assert not errs, errs


def test_a_job_keeps_its_chunks_only_when_asked(studio, tmp_path, monkeypatch):
    from tomoreveal import encode
    spec = short_spec(studio); out = str(tmp_path / "k.mp4")
    for keep, k in ((True, 5), (False, 5), (True, 1)):
        job = tr.movie_frames(studio.ren, spec, out, chunk_frames=2); heal = fails_from(monkeypatch, k)
        with pytest.raises(RuntimeError, match="graphics"):  # the job itself leaves the files as they are
            for _ in job:
                pass
        assert job.close(keep=keep) == (4 if keep and k == 5 else 0)
        rec = encode.read_partial(out)
        assert (rec["chunks_done"] == 2) if keep and k == 5 else rec is None and not glob.glob(str(tmp_path / "*.part*"))
        heal()


def test_the_command_line_deletes_the_chunks_after_an_error(studio, tmp_path, monkeypatch):
    from tomoreveal import encode
    out = str(tmp_path / "c.mp4"); monkeypatch.setattr(encode, "CHUNK_FRAMES", 2)
    for exc in (RuntimeError("graphics problem"), KeyboardInterrupt()):
        heal = fails_from(monkeypatch, 5, exc)
        with pytest.raises(type(exc)):
            tr.render_movie(studio.ren, short_spec(studio), out)
        heal(); assert os.listdir(tmp_path) == []


def test_the_command_line_keeps_the_chunks_when_only_the_join_fails(studio, tmp_path, monkeypatch):
    from tomoreveal import encode
    out = str(tmp_path / "j.mp4"); monkeypatch.setattr(encode, "CHUNK_FRAMES", 2); spec = short_spec(studio)
    real = encode.join
    monkeypatch.setattr(encode, "join", lambda *a: (_ for _ in ()).throw(RuntimeError("joining the movie parts failed")))
    with pytest.raises(RuntimeError, match="joining"):
        tr.render_movie(studio.ren, spec, out)
    n = len(tr.build_timeline(spec, studio.ds)[0]); rec = encode.read_partial(out)
    assert rec["chunks_done"] == (n + 1) // 2 and not os.path.exists(out)
    monkeypatch.setattr(encode, "join", real)
    job = tr.movie_frames(studio.ren, spec, out, resume=True); assert job.start == n
    for _ in job:
        pass
    assert encode.count_frames(out) == n and encode.read_partial(out) is None



def test_motion_blur_stops_at_a_hard_cut(studio, tmp_path):
    """Before, the last frame before a cut was blurred towards the next part's camera (G-27); a flight is still blurred."""
    from tomoreveal.movie import moving
    from tomoreveal.storyboard import focus_shot
    spec = short_spec(studio); spec["output"]["motion_blur"] = True
    for p in spec["shots"]:
        p["seconds"] = 1.0
    spec["shots"].insert(2, dict(focus_shot({"class": studio.ds.key_of(2)}, "v", seconds=1.0), transition=0))  # a hard cut into it
    job = tr.movie_frames(studio.ren, spec, str(tmp_path / "b.mp4"), poster=False); fr = job.frames
    cut = [i for i in range(len(fr) - 1) if fr[i]["part"] != fr[i + 1]["part"] and fr[i + 1]["phase"] != "transition"]
    flight = [i for i in range(len(fr) - 1) if fr[i]["part"] == fr[i + 1]["part"] and moving(fr[i], fr[i + 1])]
    assert any(fr[i + 1]["part"] == spec["shots"][2]["id"] and moving(fr[i], fr[i + 1]) for i in cut) and flight
    imgs = [img.copy() for _, _, img in job]
    plain = lambda i: studio.ren.draw(fr[i], spec, job.size, aa=job.aa, transparent=False)
    assert all((imgs[i] == plain(i)).all() for i in cut)
    assert not (imgs[flight[0]] == plain(flight[0])).all()
