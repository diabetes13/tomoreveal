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


def test_a_failed_replace_keeps_the_new_movie_as_part_files(studio, old, monkeypatch):
    def locked(src, dst):
        raise PermissionError(13, "the file is open in another program")
    monkeypatch.setattr(render.os, "replace", locked)
    with pytest.raises(UserError, match=r"movie\.part\.mp4"):
        tr.render_movie(studio.ren, short_spec(studio), old[0])
    monkeypatch.undo()
    assert unchanged(old) and sorted(os.path.basename(p) for p in leftovers(old)) == ["movie.part.mp4", "movie_poster.part.png"]


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to replace a file that is open")
def test_old_movie_open_in_a_player(studio, old):
    with open(old[0], "rb"):
        with pytest.raises(UserError, match="open in another program"):
            tr.render_movie(studio.ren, short_spec(studio), old[0])
    assert unchanged(old) and len(leftovers(old)) == 2


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
