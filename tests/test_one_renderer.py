"""One 3D renderer for the preview, playback, thumbnails, size estimates and Explore: every question about the picture
on screen (clicks, moving around, regions, arrows) is answered for that picture, whatever was drawn since."""
import argparse
import sys
import time

import numpy as np
import pytest

import tomoreveal as tr
from tomoreveal import storyboard as sb


@pytest.fixture
def ren(studio):
    """The shared renderer; what it showed is forgotten afterwards, so other tests ask about what they draw."""
    yield studio.ren
    studio.ren._shown_args = None; studio.ren.surfaces_pending = set()


def overview_frames(ds, spec):
    return [f for f in sb.build_plan(spec, ds).frames if f["phase"] == "main" and f.get("type") == "overview"]


def answers(r, c):
    return dict(point=r.pick_point(0.5, 0.5), box=r.region_box(0.35, 0.35, 0.65, 0.65), proj=r.project(c)[0],
                cam=np.concatenate(r.camera_frame()), ray=np.concatenate(r.ray(0.3, 0.6)), plane=r.to_plane(0.3, 0.6, 10.0),
                hit=r.pick(0.5, 0.5), wph=r.world_per_height(100.0))


def same(a, b):
    for k in a:
        x, y = a[k], b[k]
        if k == "hit":
            assert (x is None) == (y is None) and (x is None or (x[0] == y[0] and np.allclose(x[1], y[1], atol=1e-3))), k
        elif k == "box":
            assert (x is None) == (y is None) and (x is None or np.allclose(np.ravel(x), np.ravel(y), atol=1e-3)), k
        else:
            assert x is not None and np.allclose(np.asarray(x, float), np.asarray(y, float), atol=1e-4), k


def test_questions_are_answered_for_the_picture_on_screen(studio, ren):
    ds = studio.ds; spec = tr.default_spec(ds); fr = overview_frames(ds, spec)
    f, other = fr[0], dict(fr[-1], pose=dict(fr[-1]["pose"], az=fr[-1]["pose"]["az"] + 60, dist=1.5 * fr[-1]["pose"]["dist"]))
    c = np.array(ds.shape[::-1], float) / 2
    ren.show(f, spec, (480, 270)); rects = dict(ren.shown_rects); want = answers(ren, c)
    assert want["point"] is not None and want["box"] is not None and rects
    for size, s, sp in (((1920, 1080), other, spec), ((160, 90), f, dict(spec, style=dict(spec["style"], bg="#ffffff")))):
        ren.draw(s, sp, size)  # a size estimate in HD, a thumbnail with other settings
        same(want, answers(ren, c))
        assert ren.shown_rects == rects and ren.shown_size == (480, 270)
    changed = dict(spec, view=dict(spec["view"], az=spec["view"].get("az", 0) + 90))  # settings changed, not shown yet
    ren.draw(f, changed, (480, 270))
    same(want, answers(ren, c))


def test_a_card_on_screen_has_nothing_to_pick(studio, ren):
    ds = studio.ds; spec = tr.default_spec(ds)
    spec["shots"] = [sb.new_part("card", seconds=1)] + spec["shots"]
    card = next(f for f in sb.build_plan(spec, ds).frames if f.get("card"))
    ren.show(card, spec, (480, 270))
    ren.draw(overview_frames(ds, spec)[0], spec, (480, 270))  # a 3D frame drawn after it
    assert ren.pick_point(0.5, 0.5) is None and ren.pick(0.5, 0.5) is None and ren.region_box(0.3, 0.3, 0.7, 0.7) is None
    assert ren.project(np.array(ds.shape[::-1], float) / 2)[1] is False


def test_after_a_draw_that_failed_the_last_good_picture_is_asked(studio, ren, monkeypatch):
    ds = studio.ds; spec = tr.default_spec(ds); fr = overview_frames(ds, spec)
    c = np.array(ds.shape[::-1], float) / 2
    ren.show(fr[0], spec, (480, 270)); want = answers(ren, c)
    real = ren.draw
    def broken(s, *a, **k):
        if s is fr[-1]:
            raise RuntimeError("graphics problem")
        return real(s, *a, **k)
    monkeypatch.setattr(ren, "draw", broken)
    with pytest.raises(RuntimeError):
        ren.show(fr[-1], spec, (320, 180))
    monkeypatch.setattr(ren, "draw", real)
    same(want, answers(ren, c))


def test_asking_never_builds_a_surface_on_the_window_thread(studio, ren, monkeypatch):
    ds = studio.ds; spec = tr.default_spec(ds); f = overview_frames(ds, spec)[0]
    mem = next(k for k, c in ds.classes.items() if c["name"] == "membrane")
    level = ds.param(mem, "soften") + 3
    assert not ds.surface_ready(mem, level)
    built = []; real = ds.make_surface
    monkeypatch.setattr(ds, "make_surface", lambda k, lvl=None, *a, **kw: (lvl is not None and built.append((k, lvl))) or real(k, lvl, *a, **kw))
    g = dict(f, lk={"soften": level})  # a part that smooths membranes more: its surface is made in a worker
    ren.show(g, spec, (480, 270))
    assert (mem, level) in ren.surfaces_pending and not built
    ren.draw(f, spec, (320, 180))
    ren.pick_point(0.5, 0.5)  # drawn again for the question: still not built here
    assert not built


QtCore = pytest.importorskip("PySide6.QtCore")


@pytest.fixture
def window(tmp_path, demo, needs_3d):
    from tomoreveal.gui import build_gui
    app, win = build_gui(argparse.Namespace(files=[], outdir=str(tmp_path), headless=True)); win.resize(1400, 900)
    errs = []; old = sys.excepthook; sys.excepthook = lambda t, v, tb: errs.append(v)
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]]); win.worker.finished.connect(loop.quit); loop.exec()
    for _ in range(30):
        app.processEvents()
    assert win.st.ren is not None
    win.prev_timer.stop(); win.export.wait.stop(); win.do_preview()
    yield app, win, errs
    win.stop_play(); sys.excepthook = old; win.close()


def pump(app, n=10):
    for _ in range(n):
        app.processEvents()


def start_estimate(win, steps=8, until_hd=False):
    win.tabs.setCurrentIndex(win.TAB_MOVIE); win.show(); win.export.wait.stop(); win.export.start()
    assert win.export.est_gen is not None
    t0 = time.monotonic(); i = 0
    while time.monotonic() - t0 < 120 and win.export.est_gen is not None:
        if (i >= steps and not until_hd) or (until_hd and win.st.ren.size == (1920, 1080)):
            break
        win.export.step(); i += 1; time.sleep(0.005)  # its encoder runs in a thread between the frames
    win.prev_timer.stop(); win.export.wait.stop()


def test_clicks_while_the_file_sizes_are_estimated(window, demo):
    app, win, errs = window; ren = win.st.ren
    point, rects = ren.pick_point(0.5, 0.5), win.prev.overlay.rects()
    box = ren.region_box(0.35, 0.35, 0.65, 0.65)
    n = ren._draws; start_estimate(win, until_hd=True)
    assert ren._draws > n and ren.size == (1920, 1080) != ren.shown_size and win.export.est_gen is not None  # running, in HD
    assert np.allclose(ren.pick_point(0.5, 0.5), point) and win.prev.overlay.rects() == rects
    from tomoreveal import gui_clicks as clk
    win.clicks.start(clk.Region(None, None)); win.clicks.boxed(0.35, 0.35, 0.65, 0.65)  # "Frame a region" while it runs
    part = next(p for p in win.spec["shots"] if p.get("region"))
    assert np.allclose(part["region"]["lo"], box[0], atol=0.01) and np.allclose(part["region"]["hi"], box[1], atol=0.01)
    # painting the preview never draws (the rotation marker, the path, the selection)
    win.do_preview(); ren.draw(ren._shown_args[0], win.spec_for(), (160, 90))  # then something else is drawn
    win.prev_timer.stop(); win.nav.marker_until = time.monotonic() + 5; n = ren._draws; win.prev.repaint()
    assert ren._draws == n
    # a load stops it: no error, and it starts again once things settle
    start_estimate(win, 3)
    win.set_busy(True); assert win.export.est_gen is None and not win.export.tick.isActive()
    win.set_busy(False)
    start_estimate(win, 3)
    loop = QtCore.QEventLoop(); win.load(paths=[demo["tomogram"], demo["labels"]])
    if win.worker is not None and win.worker.isRunning():
        win.worker.finished.connect(loop.quit); loop.exec()
    pump(app, 40)
    assert "Could not" not in win.export.est_lbl.text() and not errs, (win.export.est_lbl.text(), errs)


def test_playback_shows_what_is_asked_about(window):
    app, win, errs = window
    win.play_btn.click(); loop = QtCore.QEventLoop(); QtCore.QTimer.singleShot(400, loop.quit); loop.exec()
    p = win.playing; assert p and p["last"] is not None
    ren = win.st.ren; assert ren._shown_args[0] is p["frames"][p["last"]]
    win.stop_play(); assert not errs


def queue_thumbnails(win):
    win.presets.refresh(force=True); assert win.presets._todo


def test_thumbnails_wait_for_a_load_a_movie_and_playback(window, demo, monkeypatch):
    """They kept drawing in the shared renderer during a load and while a movie was made."""
    app, win, errs = window
    queue_thumbnails(win); left = len(win.presets._todo)
    win.set_busy(True); pump(app, 20)
    assert len(win.presets._todo) == left and not win.presets.timer.isActive()  # kept, waiting
    win.set_busy(False); assert win.presets.timer.isActive()
    win.gen = iter(()); win.presets.timer.start(); pump(app, 5)  # a movie is being made
    assert len(win.presets._todo) == left and not win.presets.timer.isActive()
    win.gen = None; win._rendering(False); assert win.presets.timer.isActive()
    win.play_btn.click(); pump(app, 5); assert win.playing
    win.presets.timer.start(); pump(app, 5)
    assert len(win.presets._todo) == left and not win.presets.timer.isActive()
    win.stop_play(); assert win.presets.timer.isActive()
    t0 = time.monotonic()
    while win.presets.timer.isActive() and time.monotonic() - t0 < 60:
        app.processEvents()
    assert not win.presets._todo and all(not b.icon().isNull() for b in win.presets.cards.values()) and not errs


def test_a_thumbnail_that_fails_last_leaves_the_picture_on_screen_to_ask(window, monkeypatch):
    app, win, errs = window; ren = win.st.ren
    point = ren.pick_point(0.5, 0.5)
    real = ren.draw
    from tomoreveal.gui_presets import THUMB
    def broken(s, spec, size, *a, **k):
        img = real(s, spec, size, *a, **k)
        if tuple(size) == tuple(THUMB) and not win.presets._todo:  # the last picture
            raise RuntimeError("thumbnail broke")
        return img
    monkeypatch.setattr(ren, "draw", broken)
    queue_thumbnails(win)
    t0 = time.monotonic()
    while win.presets.timer.isActive() and time.monotonic() - t0 < 60:
        app.processEvents()
    monkeypatch.setattr(ren, "draw", real)
    assert ren.size == tuple(THUMB)  # the last thing drawn was a thumbnail
    assert np.allclose(ren.pick_point(0.5, 0.5), point) and not errs


def test_a_flip_closes_the_renderer_on_the_window_thread_and_nothing_draws_meanwhile(window, monkeypatch, capfd):
    """It was closed inside the worker thread: OpenGL errors, and its graphics context could not be freed."""
    import threading
    from tomoreveal.render import Renderer
    app, win, errs = window
    closed, draws = [], []
    real_close, real_draw = Renderer.close, Renderer.draw
    monkeypatch.setattr(Renderer, "close", lambda self: closed.append(threading.current_thread() is threading.main_thread()) or real_close(self))
    monkeypatch.setattr(Renderer, "draw", lambda self, *a, **k: draws.append(win.busy) or real_draw(self, *a, **k))
    for axes in (("x",), ()):
        queue_thumbnails(win); start_estimate(win, 3)
        loop = QtCore.QEventLoop(); win.flip_volume(axes); win.worker.finished.connect(loop.quit)
        if win.worker.isRunning():
            loop.exec()
        pump(app, 40)
    assert closed and all(closed), closed  # every close on the window's thread
    assert not any(draws)  # nothing drew while the worker had the data
    assert "OpenGL" not in capfd.readouterr().err and not errs
