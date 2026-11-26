"""Projects in the window: File menu, the window title, unsaved changes, autosave and recovery, the crash log."""
import datetime
import faulthandler
import os
import sys
import time
import traceback

from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from . import project as pj
from .common import UserError
from .encode import read_partial

RECENT = 10
_FAULT_FILE = None


def setup_crash_log():
    """Crashes of the program itself (segfaults) are written to logs/crash-<date>.log; the last 20 logs are kept."""
    global _FAULT_FILE
    if _FAULT_FILE is not None:
        return
    try:
        pj.keep_last_logs(20)
        _FAULT_FILE = open(os.path.join(pj.logs_dir(), f"crash-{datetime.date.today().isoformat()}.log"), "a")
        faulthandler.enable(file=_FAULT_FILE)
    except OSError:
        _FAULT_FILE = None


HANG_SECONDS = 45


def start_hang_watch(parent):
    """If the window stops answering for HANG_SECONDS (a freeze rather than a crash), write where every thread was to
    the crash log, so a window the user had to force quit leaves a trace. A timer on the window's thread re-arms it."""
    if _FAULT_FILE is None:
        return None

    def arm():
        try:
            faulthandler.dump_traceback_later(HANG_SECONDS, repeat=False, file=_FAULT_FILE, exit=False)
        except (OSError, ValueError):
            pass
    t = QtC.QTimer(parent); t.setInterval(5000); t.timeout.connect(arm); t.start(); arm()
    parent.destroyed.connect(lambda *_: faulthandler.cancel_dump_traceback_later())
    return t


def log_exception(exc_type, exc, tb):
    """Write an uncaught error with its traceback to logs/error-<date>.log; returns the log path."""
    path = os.path.join(pj.logs_dir(), f"error-{datetime.date.today().isoformat()}.log")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.datetime.now().isoformat(timespec='seconds')}\n")
            f.write("".join(traceback.format_exception(exc_type, exc, tb)))
    except OSError:
        pass
    return path


class RelinkDialog(QtW.QDialog):
    """One list of every data file of a project that is missing or has changed, with Locate / Skip / Use it anyway."""

    def __init__(self, parent, problems):
        super().__init__(parent); self.setWindowTitle("Some data files need your help"); self.problems = problems
        lay = QtW.QVBoxLayout(self)
        lay.addWidget(QtW.QLabel("TomoReveal could not use these files as they were when the project was saved."))
        grid = QtW.QGridLayout(); lay.addLayout(grid)
        for i, r in enumerate(problems):
            r.setdefault("choice", "skip" if r["status"] == "missing" else "use")
            name = QtW.QLabel(f"<b>{os.path.basename(r['entry']['path'])}</b>"); name.setToolTip(r["entry"]["path"])
            why = QtW.QLabel("not found" if r["status"] == "missing" else "The file has changed since the project was saved.")
            state = QtW.QLabel(""); r["_state"] = state
            loc = QtW.QPushButton("Locate..."); loc.clicked.connect(lambda _=False, r=r: self.locate(r))
            skip = QtW.QPushButton("Skip"); skip.clicked.connect(lambda _=False, r=r: self.set(r, "skip"))
            grid.addWidget(name, i, 0); grid.addWidget(why, i, 1); grid.addWidget(state, i, 2); grid.addWidget(loc, i, 3)
            grid.addWidget(skip, i, 4)
            if r["status"] == "changed":
                use = QtW.QPushButton("Use it anyway"); use.clicked.connect(lambda _=False, r=r: self.set(r, "use")); grid.addWidget(use, i, 5)
            self.set(r, r["choice"])
        lay.addWidget(QtW.QLabel("Skipped files are left out. Their structures and close-ups stay in the project and come back "
                                 "when you locate the file later."))
        bb = QtW.QDialogButtonBox(QtW.QDialogButtonBox.Ok | QtW.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def set(self, r, choice, path=None):
        r["choice"] = choice
        if path:
            r["located"] = path
        r["_state"].setText({"skip": "will be skipped", "use": "will be used", "located": f"use {os.path.basename(r.get('located', ''))}"}[choice])

    def locate(self, r):
        old = r["entry"]["path"]
        p, _ = QtW.QFileDialog.getOpenFileName(self, f"Where is {os.path.basename(old)}?", os.path.dirname(old))
        if p:
            self.set(r, "located", p)


class ProjectManager(QtC.QObject):
    """Owns the project of a window: path, saved state, autosave. Hooked into Main by gui.build_gui."""

    def __init__(self, win):
        super().__init__(win)
        self.win, self.path, self.saved_core, self.extra = win, None, None, {}
        self.pending = None          # (data, resolved, dirty) while a project is being loaded
        self.closed = False          # after a clean exit nothing is autosaved any more
        self.autosave = pj.Autosave(f"{os.getpid()}_{id(self) % 100000}")
        self.last_autosave, self.autosaved_core = 0.0, None
        self.timer = QtC.QTimer(self); self.timer.setSingleShot(True); self.timer.setInterval(400); self.timer.timeout.connect(self.refresh)
        self.auto_timer = QtC.QTimer(self); self.auto_timer.setSingleShot(True); self.auto_timer.timeout.connect(self.autosave_now)
        self._menus()
        self.refresh()
        QtC.QTimer.singleShot(0, self.startup_checks)

    # ---- menus ----
    def _menus(self):
        mb = self.win.menuBar(); first = mb.actions()[0] if mb.actions() else None
        m = QtW.QMenu("File", mb); mb.insertMenu(first, m) if first else mb.addMenu(m); self.file_menu = m
        self.act_new = m.addAction("New", self.new); self.act_new.setToolTip("Unload everything and start a new project")
        self.act_open = m.addAction("Open project...", self.open_dialog, QtG.QKeySequence.Open)
        self.recent_menu = m.addMenu("Open recent"); self.recent_menu.aboutToShow.connect(self.fill_recent)
        m.addSeparator()
        self.act_save = m.addAction("Save", self.save, QtG.QKeySequence("Ctrl+S"))
        self.act_save_as = m.addAction("Save as...", self.save_as, QtG.QKeySequence("Ctrl+Shift+S"))
        self.act_revert = m.addAction("Revert to saved", self.revert)
        m.addSeparator()
        self.act_merge = m.addAction("Merge layer movies...", lambda: self.win.export.merge_dialog())
        self.act_merge.setToolTip("Lay separate arrow and label movies back over their 3D movie, into one MP4 (no drawing again)")
        h = mb.addMenu("Help"); self.help_menu = h
        h.addAction("Show log folder", lambda: QtG.QDesktopServices.openUrl(QtC.QUrl.fromLocalFile(pj.logs_dir())))
        a = h.addAction("Performance...", self.show_performance)
        a.setToolTip("Whether the graphics card draws the pictures, memory used, and where the time of a frame goes")

    def show_performance(self):
        from .gui_performance import PerformanceDialog
        d = getattr(self, "_perf", None)
        if d is None:
            d = self._perf = PerformanceDialog(self.win)
        d.refresh(); d.show(); d.raise_()

    def fill_recent(self):
        self.recent_menu.clear()
        for p in self.recent():
            self.recent_menu.addAction(os.path.basename(p), lambda p=p: self.open(p)).setToolTip(p)
        if not self.recent():
            self.recent_menu.addAction("(none yet)").setEnabled(False)

    def recent(self):
        v = self.win.settings.value("recent_projects", []) or []
        return [p for p in ([v] if isinstance(v, str) else list(v)) if p]

    def _add_recent(self, path):
        lst = [path] + [p for p in self.recent() if p != path]
        self.win.settings.setValue("recent_projects", lst[:RECENT])

    # ---- state ----
    @property
    def loaded(self):
        return self.win.spec is not None

    def data(self, fingerprints=False, project_path=None):
        w = self.win
        return pj.make_project(w.st, w.spec, self.ui_state(), project_path or self.path, fingerprints, extra=self.extra)

    def ui_state(self):
        w = self.win
        return dict(tab=w.tabs.tabText(w.tabs.currentIndex()), playhead_frame=int(w.timeline.frame), selected_part=w.selected_part,
                    quality=w.quality_cb.currentIndex(), movie_name=w.name_le.text(), movie_folder=w.folder_le.text(),
                    timeline=w.timeline.canvas.view_state())

    def current_core(self):
        return pj.core(self.data()) if self.loaded else None

    @property
    def dirty(self):
        return self.loaded and self.current_core() != self.saved_core

    def touch(self):
        """Something may have changed: refresh the title soon and schedule an autosave."""
        self.timer.start()

    def refresh(self):
        name = os.path.basename(self.path) if self.path else "Untitled"
        dirty = self.dirty
        self.win.setWindowTitle("TomoReveal" if not self.loaded and not self.path else f"TomoReveal – {name}{' *' if dirty else ''}")
        self.act_save.setEnabled(self.loaded); self.act_save_as.setEnabled(self.loaded)
        self.act_revert.setEnabled(bool(self.path) and dirty)
        if self.loaded and not self.closed:
            c = self.current_core()
            if c != self.autosaved_core and not self.auto_timer.isActive():
                self.auto_timer.start(int(1000 * max(3.0, 30.0 - (time.monotonic() - self.last_autosave))))

    def mark_saved(self):
        self.saved_core = self.current_core(); self.refresh()

    # ---- autosave ----
    def autosave_now(self):
        """Write the autosave (small JSON, on this thread). Skipped when nothing is loaded."""
        self.auto_timer.stop()
        if not self.loaded or self.closed:
            return
        try:
            d = self.data(fingerprints=False); d["unsaved"] = self.dirty
            self.autosave.write(d, self.path)
            self.last_autosave, self.autosaved_core = time.monotonic(), pj.core(d)
        except Exception:
            traceback.print_exc()

    def clean_exit(self):
        self.closed = True; self.auto_timer.stop(); self.timer.stop(); self.autosave.clear()

    def startup_checks(self):
        """Offer to restore work after a crash, and mention movies that were interrupted."""
        found = pj.orphaned_autosaves()
        if found:
            path, t = found[0]
            when = datetime.datetime.fromtimestamp(t).strftime("%H:%M" if time.time() - t < 20 * 3600 else "%d %B, %H:%M")
            choice = self.ask_restore(when)
            if choice == "restore":
                self.restore(path)
            elif choice == "discard":
                pj.discard_autosave(path)
        self.note_partial_movies()

    def note_partial_movies(self):
        folder = self.win.folder_le.text().strip()
        try:
            names = sorted(f[:-len(".partial.json")] for f in os.listdir(folder) if f.endswith(".partial.json"))
        except OSError:
            return
        names = [n for n in names if (read_partial(os.path.join(folder, n + ".mp4")) or {}).get("chunks_done")]
        if names and not self.loaded:
            self.win.say(f"A movie was interrupted: {names[0]}. Load the same data (or restore the project) and press "
                         "Make movie with that name to continue it.")

    def restore(self, path):
        try:
            data = pj.read_project(path)
        except UserError as exc:
            self.win.say(str(exc), "err"); return
        if self.open_data(data, data.get("autosave_of"), dirty=True):
            pj.discard_autosave(path)

    # ---- asking (replaced in tests) ----
    def ask_save(self, what):
        """Save / Don't save / Cancel before `what` ("opening another project", ...): "save", "discard" or "cancel"."""
        box = QtW.QMessageBox(self.win); box.setWindowTitle("TomoReveal"); box.setIcon(QtW.QMessageBox.Question)
        box.setText(f"Save the changes to {os.path.basename(self.path) if self.path else 'this project'} before {what}?")
        b_save = box.addButton("Save", QtW.QMessageBox.AcceptRole); b_dis = box.addButton("Don't save", QtW.QMessageBox.DestructiveRole)
        box.addButton("Cancel", QtW.QMessageBox.RejectRole); box.exec()
        return "save" if box.clickedButton() is b_save else "discard" if box.clickedButton() is b_dis else "cancel"

    def ask_restore(self, when):
        box = QtW.QMessageBox(self.win); box.setWindowTitle("TomoReveal"); box.setIcon(QtW.QMessageBox.Question)
        box.setText(f"TomoReveal closed unexpectedly at {when}. Restore your work?")
        b_r = box.addButton("Restore", QtW.QMessageBox.AcceptRole); b_d = box.addButton("Discard", QtW.QMessageBox.DestructiveRole)
        box.addButton("Decide later", QtW.QMessageBox.RejectRole); box.exec()
        return "restore" if box.clickedButton() is b_r else "discard" if box.clickedButton() is b_d else "later"

    def ask_smoother_paths(self, n):
        """True: use the calmer camera path method for the n older path parts (asked once per project)."""
        box = QtW.QMessageBox(self.win); box.setWindowTitle("TomoReveal"); box.setIcon(QtW.QMessageBox.Question)
        box.setText(f"This project has {n} camera path{'s' if n > 1 else ''} made with an older version. Make "
                    f"{'them' if n > 1 else 'it'} smoother?")
        box.setInformativeText("Even speed, gentle starts and stops, and calm flights into and out of the path. The movie "
                               "changes a little; Undo brings back the old path.")
        yes = box.addButton("Make smoother", QtW.QMessageBox.AcceptRole); box.addButton("Keep as it is", QtW.QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def ask_relink(self, problems):
        """problems: resolved entries that are missing or changed. Sets r["choice"] (skip / use / located) on each;
        returns False when the user cancels opening."""
        dlg = RelinkDialog(self.win, problems)
        return dlg.exec() == QtW.QDialog.Accepted

    def confirm_discard(self, what):
        """True when it is fine to drop the current work (saved, or the user chose)."""
        if not self.dirty:
            return True
        c = self.ask_save(what)
        if c == "save":
            return self.save()
        return c == "discard"

    # ---- commands ----
    def new(self):
        self.win.start_over()  # asks about unsaved changes first

    def default_folder(self):
        d = self.win.settings.value("project_dir", "")
        if d and os.path.isdir(d):
            return d
        st = self.win.st
        first = next(iter(list(st.tomo_files) + list(st.sessions) + [s["path"] for s in st.seg_sources]), None)
        return os.path.dirname(first) if first else os.path.expanduser("~")

    def open_dialog(self):
        p, _ = QtW.QFileDialog.getOpenFileName(self.win, "Open project", self.default_folder(), f"TomoReveal projects (*{pj.EXT});;All files (*)")
        if p:
            self.open(p)

    def save(self):
        if not self.loaded:
            return False
        if not self.path:
            return self.save_as()
        return self.save_to(self.path)

    def save_as(self):
        if not self.loaded:
            return False
        name = (self.win.name_le.text().strip() or "project").removesuffix("_reveal") + pj.EXT
        p, _ = QtW.QFileDialog.getSaveFileName(self.win, "Save project", os.path.join(self.default_folder(), name),
                                               f"TomoReveal projects (*{pj.EXT})")
        if not p:
            return False
        if not p.endswith(pj.EXT):
            p += pj.EXT
        return self.save_to(p)

    def save_to(self, path):
        path = os.path.abspath(path)
        try:
            pj.write_atomic(path, self.data(fingerprints=True, project_path=path))
        except OSError as exc:
            self.win.say(f"Could not save the project: {exc}", "err"); return False
        self.path = path; self.win.settings.setValue("project_dir", os.path.dirname(path)); self._add_recent(path)
        self.mark_saved(); self.win.say(f"Saved {os.path.basename(path)}.", "ok")
        return True

    def revert(self):
        if self.path and (not self.dirty or self.ask_save("going back to the saved version") != "cancel"):
            self.open(self.path, ask=False)

    def open(self, path, ask=True):
        """Open a project file: ask about unsaved changes, find the data files, then load them."""
        if self.win.busy or self.win.gen is not None:
            return False
        if ask and not self.confirm_discard("opening another project"):
            return False
        try:
            data = pj.read_project(path)
        except UserError as exc:
            self.win.say(str(exc), "err"); return False
        self._add_recent(os.path.abspath(path))
        return self.open_data(data, os.path.abspath(path))

    def open_data(self, data, path, dirty=False):
        resolved = pj.resolve_sources(data, path)
        problems = [r for r in resolved if r["status"] != "ok"]
        if problems:
            if not self.ask_relink(problems):
                return False
            for r in problems:
                c = r.get("choice", "skip")
                r["path"] = r.get("located") if c == "located" else (r["path"] if c == "use" else None)
        known = {"format", "schema", "app_version", "saved", "sources", "tomogram_source", "tomogram_auto", "segmentation_enabled",
                 "spec", "ui", "autosave_of", "unsaved"}
        w = self.win
        w.stop_play(); w.st.reset(); w.info = w.spec = None; w.data.refresh(w.st.info()); w.show_empty()
        self.path, self.extra = path, {k: v for k, v in data.items() if k not in known}
        self.pending = (data, resolved, dirty)
        skipped = [os.path.basename(r["entry"]["path"]) for r in resolved if not r.get("path")]
        if not any(r.get("path") for r in resolved if r["entry"].get("kind") != "image"):
            self.pending = None; self.saved_core = None; self.refresh()
            w.say("None of the project's data files could be used." if resolved else "The project has no data files.", "err")
            return False
        self._skipped = skipped
        w.run_job(lambda log: pj.load_into(w.st, data, resolved, log), "Opening the project: reading the files ...")
        return True

    def after_apply(self):
        """Called by the window after new data was shown: put the project's settings in place."""
        if self.pending is None:
            if self.path is None and self.saved_core is None and self.loaded:
                self.saved_core = self.current_core()  # data just loaded into a new project: nothing to save yet
            self.refresh(); return
        data, resolved, dirty = self.pending; self.pending = None
        w = self.win; sp = pj.project_spec(data, resolved)
        if sp:
            from .storyboard import merge_settings
            w.spec = merge_settings(sp, w.info["spec"], same_grid=True)
        ui = data.get("ui") or {}
        for key, le in (("movie_name", w.name_le), ("movie_folder", w.folder_le)):
            if ui.get(key):
                le.setText(ui[key])
        if isinstance(ui.get("quality"), int) and 0 <= ui["quality"] < w.quality_cb.count():
            w.quality_cb.setCurrentIndex(ui["quality"])
        w.selected_part = ui.get("selected_part") if any(p["id"] == ui.get("selected_part") for p in w.spec["shots"]) else None
        w.editor.reset_history(); w.build_controls()
        for i in range(w.tabs.count()):
            if w.tabs.tabText(i) == ui.get("tab") and w.tabs.isTabVisible(i):
                w.tabs.setCurrentIndex(i)
        if ui.get("playhead_frame") is not None:
            w.req = {"frame": int(ui["playhead_frame"])}
        w.timeline.canvas.pending_view = ui.get("timeline") if isinstance(ui.get("timeline"), dict) else None
        w.schedule(0)
        self.saved_core = None if dirty else self.current_core()
        self.refresh()
        name = os.path.basename(self.path) if self.path else "the project"
        msg = f"Opened {name}." if not dirty else "Your work was restored. Save it to keep it."
        if self._skipped:
            msg += f" Left out: {', '.join(self._skipped)}."
        w.say(msg, "ok")
        self.offer_smoother_paths()

    def offer_smoother_paths(self):
        """3.6 (R9): once per project, offer the calmer camera path method for paths made with older versions."""
        from .gui_paths import make_paths_smoother, old_path_parts
        w = self.win; n = len(old_path_parts(w.spec)) if w.spec else 0
        key = "offers/smoother_paths/" + (os.path.abspath(self.path) if self.path else "unsaved").replace("/", "|")
        if not n or w.settings.value(key, False, type=bool):
            return
        w.settings.setValue(key, True)
        if self.ask_smoother_paths(n):
            make_paths_smoother(w.spec); w.editor.commit()
            w.say(f"Made {n} camera path{'s' if n > 1 else ''} smoother. Undo puts {'them' if n > 1 else 'it'} back as before.", "ok")

    def load_failed(self):
        self.pending = None; self.refresh()
