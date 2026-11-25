"""Make the movie: which files to save (formats), file size and codec, and the size estimates."""
import datetime
import json
import os

from PySide6 import QtCore as QtC, QtWidgets as QtW

from . import encode
from .estimate import FORMAT_NAMES, QUALITIES, Estimator, human
from .gui_theme import DIM

FORMAT_TIPS = {
    "mp4": "MP4 (plays everywhere): H.264 video.",
    "mov": "The same video in a QuickTime .mov file.",
    "mov-transparent": "A .mov whose background is see-through (ProRes 4444), to put the movie over a coloured slide or another "
                       "video. The tomogram slice stays opaque.",
    "mov-prores": "A large, high-quality .mov for video editing programs (ProRes 422 HQ).",
}
SIZES = (("Balanced", "balanced"), ("Larger, best quality", "larger"), ("Smaller", "smaller"))
CODECS = (("H.264 (plays everywhere)", "h264"), ("H.265 (about half the size; newer)", "h265"))
H265_NOTE = ("H.265: about half the file size of H.264 at the same quality. Plays on recent Macs, iPhones and in VLC. Windows may "
             "need the HEVC extension, and some PowerPoint versions cannot play it. Check before a talk.")
TRANSPARENT_NOTE = ("Transparent backgrounds work in Keynote, Final Cut, Premiere and DaVinci Resolve. Check your presentation "
                    "program before relying on it.")
PRORES_NOTE = "The two ProRes .mov files ignore the file size and codec choices."
LAYER_CHOICES = (("in the movie", "in"), ("as separate transparent movies", "separate"))
LAYERS_NOTE = ("Separate: the movie has the 3D picture only; arrows and text, and the legend, scale bar and captions, each "
               "go into a transparent .mov of their own (ProRes 4444, same length), listed in a _layers.json next to them. "
               "Lay them over the movie in a video editor, or with File > Merge layer movies. The scale bar drawn on the "
               "slice stays in the 3D movie.")


class ExportControls(QtC.QObject):
    def __init__(self, win, grid, row):
        super().__init__(win); self.win = win
        box = QtW.QWidget(); h = QtW.QGridLayout(box); h.setContentsMargins(0, 0, 0, 0); self.fmt = {}
        for i, f in enumerate(encode.FORMATS):  # two rows of two, so the box fits the left side
            cb = QtW.QCheckBox(FORMAT_NAMES[f]); cb.setToolTip(FORMAT_TIPS[f]); cb.toggled.connect(self.formats_changed)
            h.addWidget(cb, i // 2, i % 2); self.fmt[f] = cb
        h.setColumnStretch(2, 1)
        grid.addWidget(QtW.QLabel("Save as"), row, 0); grid.addWidget(box, row, 1, 1, 4)
        self.size_cb = QtW.QComboBox(); self.codec_cb = QtW.QComboBox()
        for t, d in SIZES:
            self.size_cb.addItem(t, d)
        for t, d in CODECS:
            self.codec_cb.addItem(t, d)
        self.size_cb.setToolTip("Never lowers the resolution. Smaller: about a quarter smaller than Balanced at nearly the same quality.")
        self.size_cb.activated.connect(lambda j: self.set_output(file_size=self.size_cb.itemData(j)))
        self.codec_cb.activated.connect(lambda j: self.set_output(codec=self.codec_cb.itemData(j)))
        grid.addWidget(QtW.QLabel("File size"), row + 1, 0); grid.addWidget(self.size_cb, row + 1, 1)
        grid.addWidget(QtW.QLabel("Codec"), row + 1, 2); grid.addWidget(self.codec_cb, row + 1, 3, 1, 2)
        self.note = QtW.QLabel(""); self.note.setStyleSheet(DIM); self.note.setWordWrap(True); grid.addWidget(self.note, row + 2, 0, 1, 5)
        self.est_lbl = QtW.QLabel(""); self.est_lbl.setWordWrap(True); self.est_lbl.setTextFormat(QtC.Qt.RichText)
        self.est_lbl.setToolTip("Estimated from a few frames of this movie; real sizes differ a little")
        grid.addWidget(self.est_lbl, row + 3, 0, 1, 5)
        self.cache = {}; self.est = None; self.est_gen = None; self.shown_key = None; self.last = None
        self.merge_job = None
        self.wait = QtC.QTimer(self); self.wait.setSingleShot(True); self.wait.setInterval(2000); self.wait.timeout.connect(self.start)
        self.tick = QtC.QTimer(self); self.tick.setInterval(0); self.tick.timeout.connect(self.step)

    def add_layers_row(self, grid, row):
        """3.6 (R12): 'Arrows and text: in the movie / as separate transparent movies' and Merge layers..."""
        self.layers_cb = QtW.QComboBox(); self.layers_cb.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        for t, d in LAYER_CHOICES:
            self.layers_cb.addItem(t, d)
        self.layers_cb.setToolTip(LAYERS_NOTE)
        self.layers_cb.activated.connect(lambda j: self.set_output(layers=self.layers_cb.itemData(j)))
        self.merge_btn = QtW.QPushButton("Merge layers...")
        self.merge_btn.setToolTip("Lay separate layer movies (a _layers.json and its files) back over their 3D movie, into one MP4. "
                                  "Nothing is drawn again, so it is quick. Choose which layers to include.")
        self.merge_btn.clicked.connect(lambda: self.merge_dialog())
        grid.addWidget(QtW.QLabel("Arrows and text"), row, 0); grid.addWidget(self.layers_cb, row, 1, 1, 2)
        grid.addWidget(self.merge_btn, row, 3, 1, 2)

    def merge_dialog(self, info=None):
        """Choose a _layers.json (the last layered movie by default) and the layers to include, then merge."""
        from . import layers
        w = self.win
        if info is None:
            start = layers.info_path(w.last_movie) if getattr(w, "last_movie", None) else (w.folder_le.text() if hasattr(w, "folder_le") else "")
            info, _ = QtW.QFileDialog.getOpenFileName(w, "Merge layer movies", start, "Layer list (*_layers.json)")
            if not info:
                return None
        try:
            rec = json.load(open(info))
        except (OSError, ValueError):
            w.say(f"Could not read {os.path.basename(info)}.", "err"); return None
        names = [L["name"] for L in rec.get("layers", [])]
        if len(names) > 1:
            pick = ask_layers(w, [(L["name"], L.get("title", L["name"])) for L in rec["layers"]])
            if pick is None:
                return None
            names = pick
        return self.merge(info, names)

    def merge(self, info, names):
        """Run the merge in a thread; the panel's status line says when it is done."""
        import threading
        from . import layers
        from .common import UserError
        w = self.win
        if self.merge_job is not None and self.merge_job["thread"].is_alive():
            w.say("A merge is still running.", "err"); return None
        job = dict(out=None, err=None)

        def run():
            try:
                job["out"] = layers.merge(info, layers=names)
            except UserError as exc:
                job["err"] = str(exc)
            except Exception as exc:  # FFmpeg missing or failing: shown in plain words, not as a crash
                job["err"] = f"merging failed: {exc}"
        job["thread"] = threading.Thread(target=run, daemon=True); self.merge_job = job
        self.merge_btn.setEnabled(False); w.say("Merging the layers ...")
        job["thread"].start()
        t = QtC.QTimer(self); t.setInterval(200); job["timer"] = t

        def check():
            if job["thread"].is_alive():
                return
            t.stop(); self.merge_btn.setEnabled(True)
            if job["err"]:
                w.say(job["err"][:1].upper() + job["err"][1:], "err")
            else:
                w.last_movie = job["out"]; w.open_btn.setEnabled(True)
                w.say(f"Merged into {os.path.basename(job['out'])}.", "ok")
        t.timeout.connect(check); t.start()
        return job

    # ---- settings ----
    def formats_changed(self, *_):
        w = self.win
        if w.spec is None:
            return
        on = [f for f, cb in self.fmt.items() if cb.isChecked()]
        if not on:  # at least one file
            self.fmt["mp4"].blockSignals(True); self.fmt["mp4"].setChecked(True); self.fmt["mp4"].blockSignals(False); on = ["mp4"]
        self.set_output(formats=on)

    def set_output(self, **kw):
        if self.win.spec is not None:
            self.win.spec["output"].update(kw); self.win.spec["output"].pop("crf", None) if "file_size" in kw else None
            self.sync(); self.win.schedule()

    def sync(self):
        w = self.win
        if w.spec is None:
            return
        o = w.spec["output"]; formats = o.get("formats") or ["mp4"]
        for f, cb in self.fmt.items():
            cb.blockSignals(True); cb.setChecked(f in formats); cb.blockSignals(False)
        self.size_cb.setCurrentIndex(max(0, self.size_cb.findData(o.get("file_size", "balanced"))))
        if hasattr(self, "layers_cb"):
            self.layers_cb.setCurrentIndex(max(0, self.layers_cb.findData(o.get("layers", "in"))))
        self.codec_cb.setCurrentIndex(max(0, self.codec_cb.findData(o.get("codec", "h264"))))
        h26x = any(f in ("mp4", "mov") for f in formats)
        self.size_cb.setEnabled(h26x); self.codec_cb.setEnabled(h26x)
        notes = []
        if o.get("codec") == "h265" and h26x:
            notes.append(H265_NOTE)
        if "mov-transparent" in formats:
            notes.append(TRANSPARENT_NOTE)
        if any(f in ("mov-transparent", "mov-prores") for f in formats):
            notes.append(PRORES_NOTE)
        if o.get("layers") == "separate":
            notes.append(LAYERS_NOTE)
        self.note.setText("\n".join(notes)); self.note.setVisible(bool(notes))

    # ---- estimates ----
    def key(self):
        sp = self.win.spec_for(); sp["output"]["size"] = [1920, 1080]
        return encode.settings_hash(sp, self.win.st.ds) + json.dumps(sp["output"], sort_keys=True)

    def changed(self):
        """Settings changed: stop an estimate that is running, and start again 2 s after things settle."""
        self.stop()
        if self.win.spec is not None:
            self.wait.start()

    def stop(self):
        self.tick.stop()
        if self.est is not None:
            self.est.cancelled = True
        self.est = self.est_gen = None

    def visible(self):
        w = self.win
        return w.tabs.currentIndex() == w.TAB_MOVIE and w.isVisible()

    def start(self):
        w = self.win
        if w.spec is None or w.st.ren is None or w.busy or w.gen is not None or w.playing:
            return
        k = self.key()
        if k in self.cache:
            return self.show(self.cache[k])
        if not self.visible():
            return
        self.est_lbl.setText("<span style='color:palette(placeholder-text)'>estimating file sizes ...</span>")
        sp = w.spec_for(); self.est = Estimator(w.st.ren, sp); self.est_gen = self.est.steps(); self._k = k
        self.tick.start()

    def step(self):
        w = self.win
        if self.est_gen is None or w.gen is not None or w.playing:
            return self.stop()
        try:
            next(self.est_gen)
        except StopIteration:
            res = (self.est.result, set(self.est.rough)); self.cache[self._k] = res; self.stop(); self.show(res)
            w.schedule(0)  # the shared renderer drew other sizes: draw the preview again
        except Exception as exc:
            self.stop(); self.est_lbl.setText(f"Could not estimate the file sizes: {exc}")

    def show(self, res):
        result, rough = res; self.last = res
        cur = self.win.quality_cb.currentIndex(); lines = []
        for i, (q, _) in enumerate(QUALITIES):
            parts = [f"{FORMAT_NAMES[f]} {human(b, q in rough)}" for f, b in result.get(q, {}).items()]
            line = f"{q}: " + " · ".join(parts)
            lines.append(f"<b>{line}</b>" if i == cur else line)
        secs = len(self.est.frames) / self.est.fps if self.est else None
        pm = [f for f in result.get("HD", {}) if f in ("mov-transparent", "mov-prores")]
        if pm and secs:
            lines.append(" · ".join(f"{FORMAT_NAMES[f]} per minute: {human(result['HD'][f] / secs * 60)}" for f in pm) + " (HD)")
        self.est_lbl.setText("<br>".join(lines))
        first = next(iter(result.get("HD", {})), None)
        for i, (q, _) in enumerate(QUALITIES):  # the drop-down shows the estimate too
            base = self.win.quality_cb.itemText(i).split("  · ")[0]
            b = result.get(q, {}).get(first)
            self.win.quality_cb.setItemText(i, base + (f"  · {FORMAT_NAMES[first]} {human(b, q in rough)}" if b else ""))

    def estimate_for(self, quality_index, fmt):
        if self.last is None:
            return None
        q = QUALITIES[quality_index][0]
        return self.last[0].get(q, {}).get(fmt)

    def log_true_size(self, quality_index, paths):
        """After a movie: the true sizes next to the estimates; the ratio is logged to improve the estimates later."""
        from .project import logs_dir
        out = []
        for f, p in paths.items():
            try:
                real = os.path.getsize(p)
            except OSError:
                continue
            est = self.estimate_for(quality_index, f)
            out.append(f"{FORMAT_NAMES[f]} {real / 1e6:.1f} MB" + (f" (estimated {human(est)})" if est else ""))
            if est:
                try:
                    with open(os.path.join(logs_dir(), "size-estimates.log"), "a") as fh:
                        fh.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}\t{QUALITIES[quality_index][0]}\t{f}\t"
                                 f"{real}\t{int(est)}\t{real / est:.3f}\n")
                except OSError:
                    pass
        return " · ".join(out)


def ask_layers(win, layers):
    """Which layers to lay over the movie (all ticked); None = cancelled. Tests replace this."""
    d = QtW.QDialog(win); d.setWindowTitle("Merge layer movies"); v = QtW.QVBoxLayout(d)
    v.addWidget(QtW.QLabel("Lay these layers over the 3D movie:")); boxes = []
    for name, title in layers:
        cb = QtW.QCheckBox(title); cb.setChecked(True); cb.setToolTip(f"Include the {title.lower()} layer"); v.addWidget(cb)
        boxes.append((name, cb))
    bb = QtW.QDialogButtonBox(QtW.QDialogButtonBox.Ok | QtW.QDialogButtonBox.Cancel); v.addWidget(bb)
    bb.accepted.connect(d.accept); bb.rejected.connect(d.reject)
    if d.exec() != QtW.QDialog.Accepted:
        return None
    return [n for n, cb in boxes if cb.isChecked()]
