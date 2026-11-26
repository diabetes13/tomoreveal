"""Making a movie in the window, from the press of Make movie to the last frame: first wait for what is still being made."""
import time
import traceback

from .common import UserError
from .gui_widgets import Worker
from .movie import movie_frames
from .paths import missing_paths
from .storyboard import build_plan, left_out


def movie_steps(win, spec, path, resume=False):
    """All of making a movie as one generator, which is win.gen from the press to the end. It yields None while it waits for
    something its settings need, then (frames done, total, image) after every frame; win.job is the MovieJob once there is
    one. close() cancels. What is missing is read from the data, because the timers put themselves off while win.gen is set."""
    todo = win.thresholds_wanted(spec)
    if todo:
        win.thr_timer.stop(); win.set_thresholds(todo); win.thresholds_set()
    yield from _surfaces(win, spec)
    yield from _paths(win, spec)
    plan = build_plan(spec, win.st.ds); gone = left_out(spec, plan)
    what = ", ".join(f"{i} ({why})" for i, why in gone)
    if not any(f["part"] for f in plan.frames):
        raise UserError("No part of this movie can be drawn" + (f": {what}." if what else "."))
    if gone and not win.ask_left_out(f"{len(gone)} part{'s' if len(gone) > 1 else ''} will be left out: {what}. Make the movie anyway?"):
        return
    job = win.job = movie_frames(win.st.ren, spec, path, resume=resume, plan=plan)
    win.t0 = time.time(); win.progress.setMaximum(len(job)); win.progress.setValue(job.start)
    win.status_lbl.setText(f"Continuing after frame {job.start} ..." if job.start else "Starting ...")
    try:
        yield from job
    except Exception as exc:  # the complete chunks stay: Make movie again offers to continue
        kept = job.close(keep=True)
        if not kept:
            raise
        text = str(exc) if isinstance(exc, UserError) else f"Making the movie failed: {exc}"
        if not isinstance(exc, UserError):
            traceback.print_exc()
        raise UserError(f"{text.rstrip('.')}. {kept} of {len(job)} frames are kept. Press Make movie to continue.") from exc
    finally:  # (a cancel or the end; nothing when it is closed already)
        job.close()


def _while_running(win, worker, text):
    while worker is not None and worker.isRunning():
        win.status_lbl.setText(text)
        yield None


def _surfaces(win, spec):
    """Wait until the surfaces of these settings are made, and swap them in."""
    asked = False
    while True:
        yield from _while_running(win, win._surf_worker, "Finishing the new surfaces ...")
        todo = surfaces_wanted(win, spec)
        if not todo:
            return win.swap_surfaces(spec)
        for key, _, ck in todo:
            made_for, why = win._surf_failed.get(key, (None, ""))
            if made_for == ck:  # it failed, now or before: it is made again only when its settings change
                name = (spec["classes"].get(key) or {}).get("name", key)
                raise UserError(f"The surface of {name} could not be made, so there is no movie: {why}")
        if asked:
            raise win._surf_worker.error or UserError("The new surfaces could not be made.")
        start_surfaces(win, todo); asked = True


def _paths(win, spec):
    """Wait until every path of these settings is worked out. (One that can't be is a part that is left out.)"""
    for asked in (False, True):
        yield from _while_running(win, win._path_worker, "Finishing the camera path ...")
        if not asked and missing_paths(spec, win.st.ds):
            win._compute_paths(spec)


def surfaces_wanted(win, spec):
    """(structure key, settings, cache key) of the surfaces that are drawn and have still to be made with these settings."""
    ds = win.st.ds; out = []
    for c in (win.info or {}).get("structures", []):
        k, want = c["id"], win._wanted_params(c, spec)
        if any(ds.param(k, n) != v for n, v in want.items()) and ds.needs_surface(k, want):
            out.append((c["key"], want, ds._cache_key(k, params=want)))
    return out


def start_surfaces(win, todo):
    """Make these surfaces in a worker thread; the window swaps them in when they are ready (unless a movie waits for them)."""
    ds = win.st.ds; win.say("Making the new surfaces ...")

    def work(log):
        why = ""
        for key, want, ck in todo:
            try:
                ds.make_surface(key, params=want)
            except Exception as exc:  # the others are made all the same; this one when its settings change
                traceback.print_exc(); why = f"{type(exc).__name__}: {exc}"; win._surf_failed[key] = (ck, why)
        return why

    def done(why):
        if win.gen is None or not why:  # Make movie says it itself
            win.say(why, "err" if why else None)
        win._params_ready()
    win._surf_worker = Worker(work); win._surf_worker.done.connect(done); win._surf_worker.start()
