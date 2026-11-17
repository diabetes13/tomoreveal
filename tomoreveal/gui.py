"""The desktop window (Qt)."""
import json
import os
import re
import sys
import tempfile
import time
import traceback

import numpy as np

from .common import UserError, VIEWS
from .demo import make_demo
from .render import movie_frames
from .storyboard import focus_shot, merge_settings
from .studio import Studio
from .webapp import cmd_app


def make_icon(size=256):
    from PIL import Image, ImageDraw
    s = size; im = Image.new("RGBA", (s, s), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=round(s * 0.2), fill=(24, 28, 36, 255))
    d.polygon([(s * .08, s * .80), (s * .26, s * .54), (s * .92, s * .54), (s * .74, s * .80)], fill=(196, 199, 204, 255))
    lw = max(2, round(s * .04))
    for cx, cy, r, col in ((.36, .52, .14, (242, 142, 43)), (.63, .46, .10, (77, 163, 255)), (.78, .60, .07, (225, 87, 89))):
        d.ellipse([s * (cx - r), s * (cy - r * .55), s * (cx + r), s * (cy + r * .55)], outline=col + (255,), width=lw)
    d.line([(s * .14, s * .36), (s * .40, s * .26), (s * .66, s * .23), (s * .88, s * .28)], fill=(176, 122, 161, 255), width=lw)
    return im


def cmd_icon(args):
    im = make_icon(256)
    if args.path.lower().endswith(".ico"):
        im.save(args.path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    else:
        im.save(args.path)
    print(f"wrote {args.path}")


def build_gui(args):
    from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW
    DIM = "color:#80868f"
    QUALITY = [("Draft 960 x 540 (fast)", (960, 540)), ("HD 1920 x 1080", (1920, 1080)), ("4K 3840 x 2160 (slow)", (3840, 2160)),
               ("Square 1080 x 1080", (1080, 1080)), ("Portrait 1080 x 1920", (1080, 1920))]
    FILTER = ("Tomograms and segmentations (*.mrc *.MRC *.rec *.map *.mrcs *.st *.tif *.tiff *.TIF *.TIFF *.ORSSession *.orssession);;"
              "All files (*)")

    def to_qimage(a):
        a = np.ascontiguousarray(a); h, w = a.shape[:2]; ch = a.shape[2] if a.ndim == 3 else 1
        fmt = QtG.QImage.Format_RGBA8888 if ch == 4 else QtG.QImage.Format_RGB888
        return QtG.QImage(a.tobytes(), w, h, ch * w, fmt).copy()

    def fmt_time(t):
        t = int(round(t))
        return f"{t // 60} min {t % 60:02d} s" if t >= 90 else f"{t} s"

    class Worker(QtC.QThread):
        """Runs file reading and surface building off the window's thread."""
        done = QtC.Signal(object); failed = QtC.Signal(str); log = QtC.Signal(str)

        def __init__(self, fn):
            super().__init__(); self.fn = fn

        def run(self):
            try:
                self.done.emit(self.fn(self.log.emit))
            except UserError as exc:
                self.failed.emit(str(exc))
            except Exception as exc:
                traceback.print_exc(); self.failed.emit(f"{type(exc).__name__}: {exc}")

    class Preview(QtW.QLabel):
        clicked = QtC.Signal(float, float); dragged = QtC.Signal(int, int); released = QtC.Signal(); wheel = QtC.Signal(int)

        def __init__(self):
            super().__init__(); self.setAlignment(QtC.Qt.AlignCenter); self.setWordWrap(True)
            self.setMinimumSize(320, 180); self.setSizePolicy(QtW.QSizePolicy.Ignored, QtW.QSizePolicy.Ignored)
            self.setStyleSheet("background:#000; color:#9aa3af; border-radius:8px; padding:6px")
            self._img = None; self._press = None; self._moved = False

        def set_image(self, qimg):
            self._img = qimg; self._rescale()

        def clear_image(self, text):
            self._img = None; self.clear(); self.setText(text)

        def _rescale(self):
            if self._img is not None:
                self.setPixmap(QtG.QPixmap.fromImage(self._img).scaled(self.size(), QtC.Qt.KeepAspectRatio, QtC.Qt.SmoothTransformation))

        def resizeEvent(self, e):
            super().resizeEvent(e); self._rescale()

        def frac(self, pos):
            pm = self.pixmap()
            if pm is None or pm.isNull():
                return None
            w, h = pm.width(), pm.height(); x0, y0 = (self.width() - w) / 2, (self.height() - h) / 2
            fx, fy = (pos.x() - x0) / w, (pos.y() - y0) / h
            return (fx, fy) if 0 <= fx <= 1 and 0 <= fy <= 1 else None

        def mousePressEvent(self, e):
            if e.button() == QtC.Qt.LeftButton:
                self._press = e.position().toPoint(); self._moved = False

        def mouseMoveEvent(self, e):
            if self._press is None:
                return
            d = e.position().toPoint() - self._press
            if self._moved or abs(d.x()) + abs(d.y()) > 4:
                self._moved = True; self.dragged.emit(d.x(), d.y())

        def mouseReleaseEvent(self, e):
            if self._press is None:
                return
            if self._moved:
                self.released.emit()
            else:
                f = self.frac(e.position().toPoint())
                if f:
                    self.clicked.emit(*f)
            self._press = None

        def wheelEvent(self, e):
            self.wheel.emit(e.angleDelta().y())

    class RangeSlider(QtW.QWidget):
        """A bar with two handles: the kept range along one axis."""
        changed = QtC.Signal(int, int)

        def __init__(self):
            super().__init__(); self.setMinimumHeight(24); self.setSizePolicy(QtW.QSizePolicy.Expanding, QtW.QSizePolicy.Fixed)
            self.mn, self.mx, self.lo, self.hi = 0, 1, 0, 1; self._grab = None

        def set_range(self, mn, mx, lo=None, hi=None):
            self.mn, self.mx = int(mn), max(int(mn) + 1, int(mx))
            self.lo = self.mn if lo is None else max(self.mn, int(lo)); self.hi = self.mx if hi is None else min(self.mx, int(hi)); self.update()

        def _x(self, v):
            return 9 + (self.width() - 18) * (v - self.mn) / (self.mx - self.mn)

        def _v(self, x):
            return int(round(self.mn + (self.mx - self.mn) * min(1.0, max(0.0, (x - 9) / max(1, self.width() - 18)))))

        def paintEvent(self, e):
            p = QtG.QPainter(self); p.setRenderHint(QtG.QPainter.Antialiasing); h = self.height(); pal = self.palette()
            p.setPen(QtC.Qt.NoPen); p.setBrush(pal.mid()); p.drawRoundedRect(QtC.QRectF(9, h / 2 - 2, self.width() - 18, 4), 2, 2)
            p.setBrush(pal.highlight() if self.isEnabled() else pal.dark())
            p.drawRoundedRect(QtC.QRectF(self._x(self.lo), h / 2 - 2, self._x(self.hi) - self._x(self.lo), 4), 2, 2)
            p.setPen(QtG.QPen(pal.dark().color(), 1)); p.setBrush(pal.button())
            for v in (self.lo, self.hi):
                p.drawEllipse(QtC.QPointF(self._x(v), h / 2), 7, 7)

        def mousePressEvent(self, e):
            x = e.position().x(); dl, dh = abs(x - self._x(self.lo)), abs(x - self._x(self.hi))
            self._grab = "lo" if (dl < dh or (dl == dh and x < self._x(self.lo))) else "hi"
            self.mouseMoveEvent(e)

        def mouseMoveEvent(self, e):
            if self._grab is None:
                return
            v = self._v(e.position().x())
            if self._grab == "lo":
                self.lo = max(self.mn, min(v, self.hi - 1))
            else:
                self.hi = min(self.mx, max(v, self.lo + 1))
            self.update(); self.changed.emit(self.lo, self.hi)

        def mouseReleaseEvent(self, e):
            self._grab = None

    class Marks(QtW.QWidget):
        def __init__(self):
            super().__init__(); self.setFixedHeight(18); self.marks, self.total = [], 1

        def set(self, marks, total):
            self.marks, self.total = marks, max(1, total); self.update()

        def paintEvent(self, e):
            p = QtG.QPainter(self); p.setPen(QtG.QColor("#8b919a")); f = p.font(); f.setPointSizeF(max(7.0, f.pointSizeF() * 0.85)); p.setFont(f)
            w = self.width() - 14
            for m in self.marks:
                x = int(7 + w * m["frame"] / max(1, self.total - 1)); p.drawLine(x, 1, x, 16); p.drawText(x + 3, 13, m["label"])

    class Main(QtW.QMainWindow):
        def __init__(self):
            super().__init__()
            self.settings = QtC.QSettings("TomoReveal", "TomoReveal")
            outdir = args.outdir or self.settings.value("outdir", "") or os.path.join(os.path.expanduser("~"), "TomoReveal movies")
            self.st = Studio(headless=args.headless, outdir=outdir)
            self.spec = self.info = None; self.frame, self.total, self.cur_shot = 0, 1, 0; self.req = None
            self.busy = False; self.gen = None; self.pick_mode = False; self.worker = None; self._drag0 = None
            self._thr_pending = {}; self.vox_lbl = {}; self.last_movie = None
            self.setWindowTitle("TomoReveal"); self.setAcceptDrops(True); self.resize(1480, 940)
            self.prev_timer = QtC.QTimer(self); self.prev_timer.setSingleShot(True); self.prev_timer.timeout.connect(self.do_preview)
            self.thr_timer = QtC.QTimer(self); self.thr_timer.setSingleShot(True); self.thr_timer.setInterval(400)
            self.thr_timer.timeout.connect(self.apply_thresholds)
            self.render_timer = QtC.QTimer(self); self.render_timer.setInterval(0); self.render_timer.timeout.connect(self._render_step)
            self._build(); self._fill_files(self.st.info()); self.show_empty()

        # ---------- layout ----------
        def _group(self, title, parent):
            g = QtW.QGroupBox(title); lay = QtW.QVBoxLayout(g); parent.addWidget(g)
            return g, lay

        def _note(self, text, parent):
            lab = QtW.QLabel(text); lab.setWordWrap(True); lab.setStyleSheet(DIM); parent.addWidget(lab)
            return lab

        def _slider(self, parent, label, lo, hi, on_change, fmt):
            row = QtW.QHBoxLayout(); lab = QtW.QLabel(label); lab.setFixedWidth(52)
            s = QtW.QSlider(QtC.Qt.Horizontal); s.setRange(lo, hi); val = QtW.QLabel(""); val.setFixedWidth(52)
            s.valueChanged.connect(lambda v: (val.setText(fmt(v)), on_change(v)))
            row.addWidget(lab); row.addWidget(s, 1); row.addWidget(val); parent.addLayout(row)
            s._val, s._fmt = val, fmt
            return s

        def _setv(self, s, v):  # set a control without triggering its handler
            s.blockSignals(True)
            if isinstance(s, QtW.QAbstractButton):
                s.setChecked(bool(v))
            else:
                s.setValue(v if isinstance(s, QtW.QDoubleSpinBox) else int(round(v)))
            s.blockSignals(False)
            if hasattr(s, "_val"):
                s._val.setText(s._fmt(int(round(v))))

        def _build(self):
            split = QtW.QSplitter(); self.setCentralWidget(split)
            scroll = QtW.QScrollArea(); scroll.setWidgetResizable(True); scroll.setMinimumWidth(470)
            scroll.setHorizontalScrollBarPolicy(QtC.Qt.ScrollBarAlwaysOff)
            self.panel = QtW.QWidget(); lay = QtW.QVBoxLayout(self.panel); scroll.setWidget(self.panel)
            lay.setSizeConstraint(QtW.QLayout.SetMinimumSize)  # grow and scroll rather than squash rows together
            # files
            g, gl = self._group("Files", lay)
            self._note("Drop a tomogram and its segmentation anywhere on this window, or use the buttons. Segmentation: a Dragonfly "
                       ".ORSSession, a label volume, or easymode / Ais files (name__feature.mrc). A Dragonfly session alone is enough.", gl)
            row = QtW.QHBoxLayout()
            b = QtW.QPushButton("Open files..."); b.clicked.connect(lambda: self.open_files()); row.addWidget(b)
            b = QtW.QPushButton("Add easymode files..."); b.clicked.connect(lambda: self.open_files(features=True)); row.addWidget(b)
            row.addStretch(1); gl.addLayout(row)
            row = QtW.QHBoxLayout()
            b = QtW.QPushButton("Try demo data"); b.setToolTip("Load a small made-up tomogram to try the program"); b.clicked.connect(self.load_demo); row.addWidget(b)
            row.addStretch(1)
            b = QtW.QPushButton("Start over"); b.clicked.connect(self.start_over); row.addWidget(b)
            gl.addLayout(row)
            self.msg_lbl = QtW.QLabel(""); self.msg_lbl.setWordWrap(True); gl.addWidget(self.msg_lbl)
            self.files_lbl = self._note("No files loaded.", gl)
            form = QtW.QFormLayout(); self.image_cb = QtW.QComboBox()
            self.image_cb.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon); self.image_cb.setMinimumContentsLength(14)
            self.image_cb.activated.connect(lambda i: self.set_tomogram(self.image_cb.itemData(i)))
            form.addRow("Tomogram", self.image_cb); gl.addLayout(form)
            self.image_lab = form.labelForField(self.image_cb)
            self.seg_head = QtW.QLabel("<b>Segmentation</b>"); gl.addWidget(self.seg_head)
            self.seg_w = QtW.QWidget(); self.seg_lay = QtW.QVBoxLayout(self.seg_w); self.seg_lay.setContentsMargins(0, 0, 0, 0)
            gl.addWidget(self.seg_w)
            self.dims_lbl = self._note("", gl)
            self.match_lbl = self._note("", gl); self.match_lbl.setVisible(False)
            # structures
            self.struct_group, sl = self._group("Structures", lay)
            self.struct_box = QtW.QWidget(); self.struct_grid = QtW.QGridLayout(self.struct_box); self.struct_grid.setContentsMargins(0, 0, 0, 0)
            self.struct_grid.setColumnStretch(2, 1); sl.addWidget(self.struct_box)
            self.struct_note = QtW.QLabel("No segmentation loaded. Add one with Open files, or switch one on above."); sl.addWidget(self.struct_note)
            self._note("Untick to hide. Zoom adds a close-up of that structure to the movie. easymode structures have a threshold: "
                       "the surface is drawn where the network's score is above it.", sl)
            # view
            self.view_group, vl = self._group("Main view", lay)
            pg = QtW.QGridLayout()
            for i, (name, (tilt, az)) in enumerate(VIEWS.items()):
                b = QtW.QPushButton(name); b.clicked.connect(lambda _=False, t=tilt, a=az: self.set_view(tilt=t, az=a)); pg.addWidget(b, i // 4, i % 4)
            vl.addLayout(pg)
            self.tilt_s = self._slider(vl, "Tilt", 0, 85, lambda v: self.set_view(tilt=v), lambda v: f"{v}\u00b0")
            self.az_s = self._slider(vl, "Rotate", -180, 180, lambda v: self.set_view(az=v), lambda v: f"{v}\u00b0")
            self.zoom_s = self._slider(vl, "Zoom", 50, 300, lambda v: self.set_view(zoom=v / 100), lambda v: f"{v / 100:.2f}x")
            self._note("Tip: drag in the preview to rotate, scroll to zoom.", vl)
            # clip
            self.clip_group, cl = self._group("Clip: show only part of the tomogram", lay)
            self.clip_s = {}
            for ax, text in (("x", "X"), ("y", "Y"), ("z", "Z (slices)")):
                row = QtW.QHBoxLayout(); lab = QtW.QLabel(text); lab.setFixedWidth(64)
                bar = RangeSlider(); val = QtW.QLabel(""); val.setFixedWidth(84); bar._val = val
                bar.changed.connect(lambda lo, hi, ax=ax: self._clip_changed(ax, lo, hi))
                row.addWidget(lab); row.addWidget(bar, 1); row.addWidget(val); cl.addLayout(row); self.clip_s[ax] = bar
            row = QtW.QHBoxLayout(); self.clipfit_cb = QtW.QCheckBox("Fit the view to the kept part")
            self.clipfit_cb.toggled.connect(self._clipfit_changed)
            b = QtW.QPushButton("Reset"); b.clicked.connect(self.reset_clip); row.addWidget(self.clipfit_cb, 1); row.addWidget(b); cl.addLayout(row)
            self._note("Like the clip box in Dragonfly: drag the two handles of a bar to keep only that range. "
                       "Everything outside is left out of the preview and the movie.", cl)
            # movie
            self.movie_group, ml = self._group("Movie", lay)
            self.shot_ctl = []
            for i, text in enumerate(["1. Slide up through the slices", "2. Slide back, revealing the segmentation", "3. Overview"]):
                row = QtW.QHBoxLayout(); cb = QtW.QCheckBox(text); sp = QtW.QDoubleSpinBox(); sp.setRange(0.5, 120); sp.setSingleStep(0.5); sp.setSuffix(" s")
                cb.toggled.connect(lambda v, i=i: self._shot_set(i, "on", v)); sp.valueChanged.connect(lambda v, i=i: self._shot_set(i, "seconds", v))
                row.addWidget(cb, 1)
                if i == 2:
                    self.motion_cb = QtW.QComboBox()
                    for t, d in (("rock", "rock"), ("full spin", "spin"), ("still", "none")):
                        self.motion_cb.addItem(t, d)
                    self.motion_cb.activated.connect(lambda j: self._shot_set(2, "motion", self.motion_cb.itemData(j))); row.addWidget(self.motion_cb)
                row.addWidget(sp); ml.addLayout(row); self.shot_ctl.append((cb, sp))
            ml.addWidget(QtW.QLabel("<b>Close-ups</b> (played after the overview)"))
            self.shots_w = QtW.QWidget(); self.shots_lay = QtW.QVBoxLayout(self.shots_w); self.shots_lay.setContentsMargins(0, 0, 0, 0); ml.addWidget(self.shots_w)
            self.pick_btn = QtW.QPushButton("+ Click a structure in the preview"); self.pick_btn.setCheckable(True)
            self.pick_btn.toggled.connect(self.set_pick); ml.addWidget(self.pick_btn)
            self.rto_cb = QtW.QCheckBox("Return to the overview at the end"); self.rto_cb.toggled.connect(lambda v: self._spec_set("return_to_overview", v))
            ml.addWidget(self.rto_cb)
            # look
            self.look_group, ll = self._group("Look", lay)
            row = QtW.QHBoxLayout(); self.legend_cb = QtW.QCheckBox("Legend"); self.slabel_cb = QtW.QCheckBox("Slice number"); self.invert_cb = QtW.QCheckBox("Invert contrast")
            for cb, key in ((self.legend_cb, "legend"), (self.slabel_cb, "slice_label"), (self.invert_cb, "invert")):
                cb.toggled.connect(lambda v, k=key: self._style_set(k, v)); row.addWidget(cb)
            ll.addLayout(row)
            form = QtW.QFormLayout(); self.bg_cb = QtW.QComboBox()
            for t, c in (("black", "#000000"), ("white", "#ffffff"), ("dark grey", "#1c1f26")):
                self.bg_cb.addItem(t, c)
            self.bg_cb.activated.connect(lambda j: self._style_set("bg", self.bg_cb.itemData(j)))
            self.contrast_s = QtW.QSlider(QtC.Qt.Horizontal); self.contrast_s.setRange(0, 20)
            self.contrast_s.valueChanged.connect(lambda v: self._style_set("contrast", [v / 2, 100 - v / 2]))
            self.title_le = QtW.QLineEdit(); self.title_le.setPlaceholderText("optional text, bottom left")
            self.title_le.textChanged.connect(lambda t: self._style_set("title", t))
            form.addRow("Background", self.bg_cb); form.addRow("Contrast", self.contrast_s); form.addRow("Title", self.title_le); ll.addLayout(form)
            lay.addStretch(1)
            # preview side
            right = QtW.QWidget(); rl = QtW.QVBoxLayout(right)
            self.prev = Preview()
            self.prev.clicked.connect(self.on_click); self.prev.dragged.connect(self.on_drag)
            self.prev.released.connect(self.on_release); self.prev.wheel.connect(self.on_wheel)
            rl.addWidget(self.prev, 1)
            self.scrub = QtW.QSlider(QtC.Qt.Horizontal); self.scrub.valueChanged.connect(self.on_scrub); rl.addWidget(self.scrub)
            self.marks = Marks(); rl.addWidget(self.marks)
            self.frame_lbl = QtW.QLabel(""); self.frame_lbl.setStyleSheet(DIM); rl.addWidget(self.frame_lbl)
            g = QtW.QGroupBox("Make the movie"); grid = QtW.QGridLayout(g)
            self.quality_cb = QtW.QComboBox()
            for t, wh in QUALITY:
                self.quality_cb.addItem(t, wh)
            self.quality_cb.setCurrentIndex(1); self.quality_cb.currentIndexChanged.connect(lambda _: self.schedule())
            self.folder_le = QtW.QLineEdit(self.st.outdir); self.folder_le.setMinimumWidth(120); fb = QtW.QPushButton("Choose..."); fb.clicked.connect(self.choose_folder)
            self.name_le = QtW.QLineEdit("tomogram_reveal")
            grid.addWidget(QtW.QLabel("Quality"), 0, 0); grid.addWidget(self.quality_cb, 0, 1); grid.addWidget(QtW.QLabel("Name"), 0, 2); grid.addWidget(self.name_le, 0, 3, 1, 2)
            grid.addWidget(QtW.QLabel("Save in"), 1, 0); grid.addWidget(self.folder_le, 1, 1, 1, 3); grid.addWidget(fb, 1, 4)
            self.go_btn = QtW.QPushButton("Make movie"); self.go_btn.setStyleSheet("font-weight:600; padding:6px 18px"); self.go_btn.clicked.connect(self.make_movie)
            self.cancel_btn = QtW.QPushButton("Cancel"); self.cancel_btn.clicked.connect(self.cancel_render)
            self.open_btn = QtW.QPushButton("Play movie"); self.open_btn.clicked.connect(lambda: self._open(self.last_movie))
            self.dir_btn = QtW.QPushButton("Show folder"); self.dir_btn.clicked.connect(lambda: self._open(os.path.dirname(self.last_movie or self.folder_le.text())))
            row = QtW.QHBoxLayout()
            for w in (self.go_btn, self.cancel_btn, self.open_btn, self.dir_btn):
                row.addWidget(w)
            row.addStretch(1); grid.addLayout(row, 2, 0, 1, 5)
            self.progress = QtW.QProgressBar(); grid.addWidget(self.progress, 3, 0, 1, 5)
            self.status_lbl = QtW.QLabel(""); self.status_lbl.setStyleSheet(DIM); self.status_lbl.setWordWrap(True); grid.addWidget(self.status_lbl, 4, 0, 1, 5)
            rl.addWidget(g)
            split.addWidget(scroll); split.addWidget(right); split.setStretchFactor(1, 1); split.setSizes([500, 980])
            self.cancel_btn.hide(); self.progress.hide(); self.open_btn.setEnabled(False)

        # ---------- messages and state ----------
        def say(self, text, kind=None):
            col = {"err": "#e5534b", "ok": "#3fb950"}.get(kind, "#80868f")
            self.msg_lbl.setStyleSheet(f"color:{col}"); self.msg_lbl.setText(text or "")
            if text:
                self.statusBar().showMessage(text, 10000)

        def show_empty(self):
            for g in (self.struct_group, self.view_group, self.clip_group, self.movie_group, self.look_group):
                g.setVisible(False)
            self.scrub.setVisible(False); self.marks.set([], 1); self.frame_lbl.setText(""); self.go_btn.setEnabled(False)
            self.prev.clear_image("The preview appears here once a tomogram or a segmentation is loaded.\n\nDrop files onto this window to begin.")

        def set_busy(self, flag, msg=None):
            self.busy = flag; self.panel.setEnabled(not flag); self.go_btn.setEnabled(not flag and self.st.can_show)
            if flag:
                QtW.QApplication.setOverrideCursor(QtC.Qt.BusyCursor); self.say(msg)
            else:
                QtW.QApplication.restoreOverrideCursor()

        # ---------- loading ----------
        def open_files(self, features=False):
            start = self.settings.value("lastdir", "") or os.path.expanduser("~")
            paths, _ = QtW.QFileDialog.getOpenFileNames(self, "Add easymode files" if features else "Open tomogram and segmentation", start, FILTER)
            if paths:
                self.settings.setValue("lastdir", os.path.dirname(paths[0]))
                self.load(paths=paths, features=paths if features else ())

        def dragEnterEvent(self, e):
            if e.mimeData().hasUrls():
                e.acceptProposedAction()

        def dropEvent(self, e):
            paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
            if paths:
                e.acceptProposedAction(); self.load(paths=paths)

        def run_job(self, fn, msg="Reading the files and building 3D surfaces. This can take a minute for large tomograms ..."):
            """Run a Studio call that returns info() in the Worker thread; the renderer is made afterwards on this thread."""
            if self.busy or self.gen is not None:
                return
            self.set_busy(True, msg)
            self.worker = Worker(fn)
            self.worker.log.connect(lambda m: self.say(m)); self.worker.done.connect(self._loaded); self.worker.failed.connect(self._load_failed)
            self.worker.start()

        def load(self, paths=(), roi=None, image=None, features=()):
            st, paths, features = self.st, list(paths), list(features)
            self.run_job(lambda log: st.load(paths, roi=roi, image=image, log=log, as_features=features, make_renderer=False))

        def set_tomogram(self, key):
            self.run_job(lambda log: self.st.set_tomogram_source(key or None, log=log, make_renderer=False), "Changing the tomogram ...")

        def set_segmentation(self, key, on):
            self.run_job(lambda log: self.st.set_segmentation_enabled(key, on, log=log, make_renderer=False),
                         "Reading the segmentation and building 3D surfaces ..." if on else "Updating ...")

        def remove_source(self, key):
            self.run_job(lambda log: self.st.remove_source(key, log=log, make_renderer=False), "Updating ...")

        def _loaded(self, info):
            try:
                self.st.ensure_renderer()
            except Exception as exc:
                traceback.print_exc(); self.set_busy(False); self.say(f"Could not start the 3D renderer: {exc}", "err"); return
            self.set_busy(False); self.apply_info(info)

        def _load_failed(self, msg):
            self.set_busy(False)
            try:
                self.st.ensure_renderer()
                self.apply_info(self.st.info(), keep_message=True)
            except Exception:
                traceback.print_exc()
            self.say(msg, "err")

        def start_over(self):
            if self.busy or self.gen is not None:
                return
            self.st.reset(); self.info = self.spec = None; self._fill_files(self.st.info()); self.show_empty(); self.say("")

        def _fill_files(self, info):
            choices = info.get("tomogram_choices", []); sources = info.get("segmentation_sources", [])
            loaded = len(choices) > 1 or bool(sources)
            self.files_lbl.setVisible(not loaded); self.files_lbl.setText("No files loaded.")
            cb = self.image_cb; cb.blockSignals(True); cb.clear()
            for o in choices:
                cb.addItem(o["label"], o["key"] or "")
            cb.setCurrentIndex(max(0, cb.findData(info.get("tomogram") or ""))); cb.blockSignals(False)
            cb.setVisible(loaded); self.image_lab.setVisible(loaded)
            lay = self.seg_lay
            while lay.count():  # store each widget before setParent(None): afterwards item.widget() is None
                w = lay.takeAt(0).widget()
                if w:
                    w.setParent(None); w.deleteLater()
            self.seg_head.setVisible(bool(sources)); group = None
            hidden = {h["key"][8:]: h for h in info.get("sessions", [])}
            for s in sources:
                if s["group"] != group:
                    group = s["group"]; lab = QtW.QLabel(group); lab.setStyleSheet(DIM); lay.addWidget(lab)
                names = ", ".join(s["names"][:6]) + (", ..." if len(s["names"]) > 6 else "")
                n = s["count"]
                tick = QtW.QCheckBox(f"{s['label']} ({n} structure{'s' if n != 1 else ''})" if s["kind"] != "scores" else s["label"])
                tick.setChecked(bool(s["enabled"])); tick.setEnabled(bool(s["usable"]))
                tick.setToolTip(s["reason"] or names)
                tick.toggled.connect(lambda v, key=s["key"]: self.set_segmentation(key, v)); lay.addWidget(tick)
                note = QtW.QLabel(("    " + s["reason"]) if not s["usable"] else ("    " + names if s["kind"] != "scores" else ""))
                note.setStyleSheet(DIM); note.setWordWrap(True)
                if note.text().strip():
                    lay.addWidget(note)
                if s["session"] and s is [x for x in sources if x["session"] == s["session"]][-1]:
                    h = hidden.get(s["session"][8:], {})
                    if h.get("previews"):
                        lab = QtW.QLabel(f"    {h['previews']} single-slice previews from the Segmentation Wizard hidden"); lab.setStyleSheet(DIM)
                        lay.addWidget(lab)
            dims = info.get("dims")
            self.match_lbl.setText(info.get("match_note") or ""); self.match_lbl.setVisible(bool(info.get("match_note")))
            self.dims_lbl.setText((" x ".join(map(str, dims)) + " voxels" + (f" \u00b7 {info['voxel'] / 10:.2f} nm per voxel" if info.get("voxel") else "")) if dims else "")

        def apply_info(self, info, keep_message=False):
            prev = self.info
            same = bool(prev and prev.get("can_show") and info.get("can_show") and self.spec)
            self.info = info; self._fill_files(info)
            if not info.get("can_show"):
                if not keep_message:
                    self.say(info.get("message", ""))
                self.spec = None; self.show_empty(); return
            if not same:
                self.spec = info["spec"]; self.req = self._main_req()
            else:  # keep the user's settings of every structure that is still there
                merge_settings(self.spec, info["spec"], same_grid=prev.get("dims") == info.get("dims"))
            if not same or not self.name_le.text().strip():
                self.name_le.setText(re.sub(r"\.[^.]+$", "", info.get("tomo_name") or "tomogram")[:60] + "_reveal")
            for g in (self.struct_group, self.view_group, self.clip_group, self.movie_group, self.look_group):
                g.setVisible(True)
            self.scrub.setVisible(True); self.go_btn.setEnabled(True)
            self.build_controls()
            if not keep_message:
                self.say("Loaded. Change anything on the left; the preview follows.", "ok")
            self.schedule(0)

        # ---------- controls ----------
        def build_controls(self):
            sp = self.spec
            grid = self.struct_grid
            while grid.count():
                it = grid.takeAt(0); w = it.widget()  # store it first: after setParent(None), it.widget() is None
                if w:
                    w.setParent(None); w.deleteLater()
                elif it.layout():
                    lay = it.layout()
                    while lay.count():
                        w = lay.takeAt(0).widget()
                        if w:
                            w.setParent(None); w.deleteLater()
            self.vox_lbl = {}; r = 0
            structs = self.info.get("structures", [])
            self.struct_note.setVisible(not structs); self.pick_btn.setEnabled(bool(structs))
            several = len({c["source"] for c in structs}) > 1; src = None
            for c in structs:
                k = c["key"]; sc = sp["classes"][k]
                if several and c["source"] != src:
                    src = c["source"]; head = QtW.QLabel(f"<b>{c['source_title']}</b>"); grid.addWidget(head, r, 0, 1, 5); r += 1
                if c["background"]:
                    lab = QtW.QLabel(f"{sc['name']} (background, not drawn)"); lab.setStyleSheet(DIM); grid.addWidget(lab, r, 2)
                    v = QtW.QLabel(f"{c['voxels']:,}"); v.setStyleSheet(DIM); grid.addWidget(v, r, 3); r += 1
                    continue
                cb = QtW.QCheckBox(); cb.setChecked(bool(sc["show"])); cb.toggled.connect(lambda v, k=k: self._class_set(k, "show", v))
                colb = QtW.QPushButton(); colb.setFixedSize(30, 22); self._paint(colb, sc["color"])
                colb.clicked.connect(lambda _=False, k=k, b=colb: self.pick_color(k, b))
                ne = QtW.QLineEdit(sc["name"]); ne.setMinimumWidth(70); ne.textChanged.connect(lambda t, k=k: self._class_set(k, "name", t))
                v = QtW.QLabel(f"{c['voxels']:,}"); v.setStyleSheet(DIM); v.setAlignment(QtC.Qt.AlignRight | QtC.Qt.AlignVCenter); self.vox_lbl[c["id"]] = v
                zb = QtW.QPushButton("Zoom"); zb.setToolTip("Add a close-up of this structure to the movie")
                zb.clicked.connect(lambda _=False, k=k: self.add_focus({"class": k}, self.spec["classes"][k]["name"]))
                for col, w in enumerate((cb, colb, ne, v, zb)):
                    grid.addWidget(w, r, col)
                r += 1
                if c["kind"] == "score":
                    row = QtW.QHBoxLayout(); lab = QtW.QLabel("threshold"); lab.setStyleSheet(DIM)
                    s = QtW.QSlider(QtC.Qt.Horizontal); s.setRange(1, 19); s.setValue(int(round((c["threshold"] or 0.5) * 20)))
                    val = QtW.QLabel(f"{(c['threshold'] or 0.5):.2f}"); val.setFixedWidth(40)
                    s.valueChanged.connect(lambda x, cid=c["id"], val=val: (val.setText(f"{x / 20:.2f}"), self._thr_changed(cid, x / 20)))
                    row.addWidget(lab); row.addWidget(s, 1); row.addWidget(val); grid.addLayout(row, r, 2, 1, 3); r += 1
            v = sp["view"]
            self._setv(self.tilt_s, v["tilt"]); self._setv(self.az_s, v["az"]); self._setv(self.zoom_s, v["zoom"] * 100)
            self._sync_clip()
            for i, (cb, spin) in enumerate(self.shot_ctl):
                self._setv(cb, sp["shots"][i].get("on", True)); self._setv(spin, float(sp["shots"][i].get("seconds", 5)))
            self.motion_cb.setCurrentIndex(max(0, self.motion_cb.findData(sp["shots"][2].get("motion", "rock"))))
            self._setv(self.rto_cb, sp.get("return_to_overview", True))
            st = sp["style"]
            self._setv(self.legend_cb, st.get("legend", True)); self._setv(self.slabel_cb, st.get("slice_label", True)); self._setv(self.invert_cb, st.get("invert", False))
            self.bg_cb.setCurrentIndex(max(0, self.bg_cb.findData(st.get("bg", "#000000"))))
            self._setv(self.contrast_s, st.get("contrast", [1, 99])[0] * 2)
            self.title_le.blockSignals(True); self.title_le.setText(st.get("title", "")); self.title_le.blockSignals(False)
            self.build_shots()

        def build_shots(self):
            lay = self.shots_lay
            while lay.count():
                w = lay.takeAt(0).widget()
                if w:
                    w.setParent(None); w.deleteLater()
            n = 0
            for i, sh in enumerate(self.spec["shots"]):
                if sh["type"] != "focus":
                    continue
                n += 1; fr = QtW.QFrame(); fr.setFrameShape(QtW.QFrame.StyledPanel); g = QtW.QGridLayout(fr)
                cap = QtW.QLineEdit(sh.get("caption", "")); cap.setPlaceholderText("caption")
                cap.textChanged.connect(lambda t, sh=sh: self._shot_edit(sh, "caption", t))
                show = QtW.QPushButton("Show"); show.clicked.connect(lambda _=False, i=i: self.show_shot(i))
                rm = QtW.QPushButton("Remove"); rm.clicked.connect(lambda _=False, sh=sh: self.remove_shot(sh))
                g.addWidget(cap, 0, 0, 1, 2); g.addWidget(show, 0, 2); g.addWidget(rm, 0, 3)
                combos = (("others", (("grey out the rest", "grey"), ("hide the rest", "hide"), ("keep the rest", "keep"))),
                          ("motion", (("orbit around it", "orbit"), ("rock", "rock"), ("hold still", "none"))),
                          ("slice", (("keep slice", "keep"), ("hide slice", "hide"))))
                for j, (key, opts) in enumerate(combos):
                    cb = QtW.QComboBox(); vals = [d for _, d in opts]
                    for t, d in opts:
                        cb.addItem(t, d)
                    cb.setCurrentIndex(vals.index(sh.get(key)) if sh.get(key) in vals else 0)
                    cb.activated.connect(lambda j2, cb=cb, sh=sh, key=key: self._shot_edit(sh, key, cb.itemData(j2)))
                    g.addWidget(cb, 1, j, 1, 1 if j < 2 else 2)
                sec = QtW.QDoubleSpinBox(); sec.setRange(0.5, 120); sec.setSingleStep(0.5); sec.setSuffix(" s"); sec.setValue(float(sh.get("seconds", 5)))
                sec.valueChanged.connect(lambda x, sh=sh: self._shot_edit(sh, "seconds", x))
                zm = QtW.QDoubleSpinBox(); zm.setRange(0.3, 4); zm.setSingleStep(0.1); zm.setPrefix("zoom "); zm.setSuffix("x"); zm.setValue(float(sh.get("zoom", 1)))
                zm.valueChanged.connect(lambda x, sh=sh: self._shot_edit(sh, "zoom", x))
                what = QtW.QLabel("one object" if sh["target"].get("component") is not None else "whole structure"); what.setStyleSheet(DIM)
                g.addWidget(sec, 2, 0); g.addWidget(zm, 2, 1); g.addWidget(what, 2, 2, 1, 2)
                lay.addWidget(fr)
            if not n:
                self._note("None yet. Use a Zoom button above, or click a structure in the preview.", lay)

        def _paint(self, b, hexc):
            b.setStyleSheet(f"background:{hexc}; border:1px solid #555; border-radius:3px")

        def pick_color(self, k, b):
            c = QtW.QColorDialog.getColor(QtG.QColor(self.spec["classes"][k]["color"]), self, "Colour")
            if c.isValid():
                self.spec["classes"][k]["color"] = c.name(); self._paint(b, c.name()); self.schedule()

        def _class_set(self, k, key, v):
            self.spec["classes"][k][key] = v; self.schedule()

        def _shot_set(self, i, key, v):
            if self.spec:
                self.spec["shots"][i][key] = v; self.schedule()

        def _shot_edit(self, sh, key, v):
            sh[key] = v; self.schedule()

        def _style_set(self, key, v):
            if self.spec:
                self.spec["style"][key] = v; self.schedule()

        def _spec_set(self, key, v):
            if self.spec:
                self.spec[key] = v; self.schedule()

        def show_shot(self, i):
            self.req = {"at": {"shot": i, "u": 0.08}}; self.schedule(0)

        def remove_shot(self, sh):
            self.spec["shots"] = [s for s in self.spec["shots"] if s is not sh]; self.build_shots(); self.schedule()

        def add_focus(self, target, name):
            self.spec["shots"].append(focus_shot(target, name))
            self.req = {"at": {"shot": len(self.spec["shots"]) - 1, "u": 0.08}}; self.build_shots(); self.schedule(0)

        # ---------- clip box ----------
        def _sync_clip(self):
            dims = dict(zip("xyz", self.info["dims"])); c = self.spec.get("clip") or {}
            for a in "xyz":
                lo, hi = c.get(a) or (0, dims[a] - 1)
                self.clip_s[a].set_range(0, dims[a] - 1, lo, hi); self.clip_s[a]._val.setText(f"{self.clip_s[a].lo} - {self.clip_s[a].hi}")
            self._setv(self.clipfit_cb, c.get("fit", True))

        def _clip_changed(self, ax, lo, hi):
            if not self.spec:
                return
            self.clip_s[ax]._val.setText(f"{lo} - {hi}")
            dims = dict(zip("xyz", self.info["dims"]))
            c = dict(self.spec.get("clip") or {a: [0, dims[a] - 1] for a in "xyz"}); c[ax] = [int(lo), int(hi)]
            c["fit"] = self.clipfit_cb.isChecked()
            self.spec["clip"] = None if all(c[a] == [0, dims[a] - 1] for a in "xyz") else c
            if self._in_closeup() and self.req is None:
                self.req = self._main_req()
            self.schedule(40)

        def _clipfit_changed(self, v):
            if self.spec and self.spec.get("clip"):
                self.spec["clip"]["fit"] = bool(v); self.schedule()

        def reset_clip(self):
            if self.spec:
                self.spec["clip"] = None; self._sync_clip(); self.schedule(0)

        def load_demo(self):
            if self.busy or self.gen is not None:
                return
            try:
                p = make_demo(os.path.join(tempfile.gettempdir(), "tomoreveal_demo"))
            except Exception as exc:
                self.say(f"Could not write the demo data: {exc}", "err"); return
            self.load(paths=[p["tomogram"], p["labels"]])

        def _thr_changed(self, cid, v):
            self._thr_pending[cid] = v; self.thr_timer.start()

        def apply_thresholds(self):
            if self.busy or self.gen is not None:
                self.thr_timer.start(); return
            pend, self._thr_pending = self._thr_pending, {}
            QtW.QApplication.setOverrideCursor(QtC.Qt.WaitCursor)
            try:
                for cid, v in pend.items():
                    r = self.st.set_threshold(cid, v)
                    key = None
                    for c in self.info["structures"]:
                        if c["id"] == cid:
                            c.update(threshold=r["threshold"], voxels=r["voxels"], mesh=r["mesh"]); key = c["key"]
                    if cid in self.vox_lbl:
                        self.vox_lbl[cid].setText(f"{r['voxels']:,}")
                    self.spec["shots"] = [s for s in self.spec["shots"]
                                          if not (s["type"] == "focus" and s["target"]["class"] == key and s["target"].get("component") is not None)]
                self.say("Threshold updated.", "ok")
            except UserError as exc:
                self.say(str(exc), "err")
            finally:
                QtW.QApplication.restoreOverrideCursor()
            self.build_shots(); self.schedule(0)

        # ---------- view and preview ----------
        def _main_req(self):
            sh = self.spec["shots"]
            if sh[2].get("on", True):
                return {"at": {"shot": 2, "u": 0}}
            if sh[1].get("on", True):
                return {"at": {"shot": 1, "u": 0.999}}
            return {"t": 0.5}

        def _in_closeup(self):
            return self.spec is not None and self.cur_shot >= 3

        def set_view(self, tilt=None, az=None, zoom=None, quick=False):
            if not self.spec:
                return
            v = self.spec["view"]
            if tilt is not None:
                v["tilt"] = float(tilt)
            if az is not None:
                v["az"] = float(az)
            if zoom is not None:
                v["zoom"] = float(zoom)
            self._setv(self.tilt_s, v["tilt"]); self._setv(self.az_s, v["az"]); self._setv(self.zoom_s, v["zoom"] * 100)
            if self._in_closeup() and self.req is None:
                self.req = self._main_req()  # the main view is not visible during a close-up: jump to the overview
            if quick:
                self.do_preview(quick=True)
            else:
                self.schedule()

        def schedule(self, delay=120):
            self.prev_timer.start(delay)

        def spec_for(self):
            sp = json.loads(json.dumps(self.spec)); sp["output"]["size"] = list(self.quality_cb.currentData())
            return sp

        def do_preview(self, quick=False):
            if self.spec is None or self.busy or self.gen is not None or not self.st.can_show or self.st.ren is None:
                return
            spec = self.spec_for(); req = self.req or {"t": self.frame / max(1, self.total - 1)}; self.req = None
            frames, marks, i = self.st._frame(spec, req)
            w, h = self.st._psize(spec); size = (w * 2 // 3, h * 2 // 3) if quick else (w, h)
            try:
                img = self.st.ren.draw(frames[i], spec, size, aa=False)
            except Exception as exc:
                traceback.print_exc()
                if not self.st.headless:
                    self.st.headless = True; self.st._stale = True; self.st.ensure_renderer(); self.req = req
                    self.say("Graphics problem: switched to software rendering (slower).", "err")
                    return self.do_preview(quick)
                self.say(f"Preview failed: {exc}", "err"); return
            self.frame, self.total, self.cur_shot = i, len(frames), frames[i]["shot"]
            self.prev.set_image(to_qimage(img))
            self.scrub.blockSignals(True); self.scrub.setRange(0, len(frames) - 1); self.scrub.setValue(i); self.scrub.blockSignals(False)
            self.marks.set(marks, len(frames)); fps = int(spec["output"].get("fps", 30))
            self.frame_lbl.setText(f"{i / fps:.1f} s of {len(frames) / fps:.1f} s  \u00b7  drag the slider to scrub through the movie")

        def on_scrub(self, v):
            self.req = {"frame": int(v)}; self.schedule(40)

        def set_pick(self, on):
            self.pick_mode = bool(on); self.prev.setCursor(QtC.Qt.CrossCursor if on else QtC.Qt.ArrowCursor)
            if on and self.spec:
                self.req = self._main_req(); self.schedule(0)
                self.say("Click the structure you want a close-up of.")

        def on_click(self, fx, fy):
            if not self.pick_mode or not self.st.has_segmentation or self.gen is not None:
                return
            try:
                p = self.st.pick(self.spec_for(), {"frame": self.frame, "x": fx, "y": fy})
            except Exception as exc:
                self.say(f"Could not pick: {exc}", "err"); return
            if not p.get("found"):
                self.say("Nothing there. Click directly on a coloured structure.", "err"); return
            self.pick_btn.setChecked(False)
            extra = f" (one object, {p['voxels']:,} voxels)" if p.get("component") is not None else ""
            self.add_focus({"class": p["class"], "component": p["component"]} if p.get("component") is not None else {"class": p["class"]}, p["name"])
            self.say(f"Added a close-up of {p['name']}{extra}.", "ok")

        def on_drag(self, dx, dy):
            if self.pick_mode or not self.spec or self.gen is not None or self.busy:
                return
            if self._drag0 is None:
                self._drag0 = dict(self.spec["view"])
            v0 = self._drag0
            self.set_view(tilt=min(85.0, max(0.0, v0["tilt"] + dy * 0.25)), az=((v0["az"] - dx * 0.35 + 180.0) % 360.0) - 180.0, quick=True)

        def on_release(self):
            self._drag0 = None; self.schedule(0)

        def on_wheel(self, delta):
            if self.spec and self.gen is None:
                self.set_view(zoom=min(3.0, max(0.5, self.spec["view"]["zoom"] * 1.1 ** (delta / 120.0))))

        # ---------- making the movie ----------
        def choose_folder(self):
            d = QtW.QFileDialog.getExistingDirectory(self, "Save movies in", self.folder_le.text() or os.path.expanduser("~"))
            if d:
                self.folder_le.setText(d)

        def make_movie(self):
            if not self.st.can_show or self.gen is not None or self.busy:
                return
            folder = os.path.abspath(os.path.expanduser(self.folder_le.text().strip() or self.st.outdir))
            name = re.sub(r"[^\w\-. ]+", "_", self.name_le.text().strip()).strip() or "tomogram_reveal"
            if not name.lower().endswith(".mp4"):
                name += ".mp4"
            path = os.path.join(folder, name)
            if os.path.exists(path):
                r = QtW.QMessageBox.question(self, "TomoReveal", f"{name} already exists in that folder. Replace it?")
                if r != QtW.QMessageBox.Yes:
                    return
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as exc:
                self.say(f"Cannot use that folder: {exc}", "err"); return
            self.settings.setValue("outdir", folder)
            self.gen = movie_frames(self.st.ren, self.spec_for(), path); self.render_path = path; self.t0 = time.time()
            self._rendering(True); self.status_lbl.setText("Starting ..."); self.render_timer.start()

        def _rendering(self, on):
            self.panel.setEnabled(not on); self.scrub.setEnabled(not on)
            for w in (self.quality_cb, self.name_le, self.folder_le):
                w.setEnabled(not on)
            self.go_btn.setVisible(not on); self.cancel_btn.setVisible(on); self.progress.setVisible(on)
            if on:
                self.progress.setValue(0)

        def _render_step(self):
            try:
                i, n, img = next(self.gen)
            except StopIteration:
                return self._render_done()
            except Exception as exc:
                traceback.print_exc(); self._render_stop()
                self.say(f"Making the movie failed: {exc}", "err"); self.status_lbl.setText(""); return
            self.progress.setMaximum(n); self.progress.setValue(i)
            el = time.time() - self.t0
            self.status_lbl.setText(f"frame {i} of {n} \u00b7 about {fmt_time(el / i * (n - i))} left")
            if i % 6 == 1 or i == n:
                self.prev.set_image(to_qimage(img))

        def _render_stop(self):
            self.render_timer.stop()
            if self.gen is not None:
                self.gen.close(); self.gen = None
            self._rendering(False)

        def cancel_render(self):
            self._render_stop()
            for p in (self.render_path, os.path.splitext(self.render_path)[0] + "_poster.png"):
                try:
                    os.remove(p)
                except OSError:
                    pass
            self.status_lbl.setText("Cancelled."); self.schedule(0)

        def _render_done(self):
            self.render_timer.stop(); self.gen = None; self._rendering(False)
            el = time.time() - self.t0
            mb = os.path.getsize(self.render_path) / 1e6; self.last_movie = self.render_path; self.open_btn.setEnabled(True)
            n = self.progress.maximum(); secs = n / int(self.spec["output"].get("fps", 30))
            self.status_lbl.setText(f"Finished in {fmt_time(el)} \u00b7 {secs:.1f} s movie \u00b7 {mb:.1f} MB \u00b7 saved as {self.render_path}")
            self.say("Movie saved.", "ok"); self.schedule(0)

        def _open(self, path):
            if path and os.path.exists(path):
                QtG.QDesktopServices.openUrl(QtC.QUrl.fromLocalFile(path))

        def closeEvent(self, e):
            if self.gen is not None:
                r = QtW.QMessageBox.question(self, "TomoReveal", "A movie is still being made. Stop it and quit?")
                if r != QtW.QMessageBox.Yes:
                    e.ignore(); return
                self.cancel_render()
            if self.worker is not None and self.worker.isRunning():
                self.worker.wait(60000)
            self.st._drop_renderer(); e.accept()

    app = QtW.QApplication.instance() or QtW.QApplication(sys.argv[:1])
    app.setApplicationName("TomoReveal"); app.setOrganizationName("TomoReveal")
    app.setWindowIcon(QtG.QIcon(QtG.QPixmap.fromImage(to_qimage(np.asarray(make_icon(256))))))
    win = Main(); win.show()
    if getattr(args, "files", None):
        QtC.QTimer.singleShot(300, lambda: win.load(paths=args.files))
    return app, win


def cmd_demo(args):
    paths = make_demo(args.folder or os.path.join(tempfile.gettempdir(), "tomoreveal_demo"))
    print("demo files:", paths["tomogram"], paths["labels"])
    if not args.no_gui:
        args.files = [paths["tomogram"], paths["labels"]]
        cmd_gui(args)


def cmd_gui(args):
    try:
        import PySide6  # noqa: F401
    except ImportError:
        print("tomoreveal: the desktop window needs PySide6 (conda install -c conda-forge pyside6); opening the browser version instead.")
        args.port, args.no_browser, args.outdir = 8765, False, args.outdir or "tomoreveal_output"
        return cmd_app(args)
    app, _win = build_gui(args)
    sys.exit(app.exec())
