"""The Explore tab: go through the slices by hand. It never changes the movie except through its hand-over buttons."""
import json
from collections import OrderedDict

import numpy as np
from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from .explore import readout, slice_image
from .storyboard import build_timeline, clip_box, default_opacity

from .gui_theme import DIM
CUTS = (("structures above the slice", "above"), ("all structures", "all"), ("no structures", "none"))
OVERLAYS = (("filled", "filled"), ("outlines", "outlines"), ("off", "off"))


def to_qimage(a):
    a = np.ascontiguousarray(a[..., :3]); h, w = a.shape[:2]
    return QtG.QImage(a.tobytes(), w, h, 3 * w, QtG.QImage.Format_RGB888).copy()


class SliceView(QtW.QGraphicsView):
    """The slice on screen: mouse wheel changes the slice, Ctrl + wheel zooms, dragging pans, double-click resets the zoom."""
    hovered = QtC.Signal(float, float)
    left = QtC.Signal()
    stepped = QtC.Signal(int)

    def __init__(self):
        super().__init__()
        self.setScene(QtW.QGraphicsScene(self)); self.pix = self.scene().addPixmap(QtG.QPixmap())
        pen = QtG.QPen(QtG.QColor("#ffd23f")); pen.setStyle(QtC.Qt.DashLine); pen.setCosmetic(True); pen.setWidth(2)
        self.clip_item = self.scene().addRect(QtC.QRectF(), pen); self.clip_item.setVisible(False)
        self.setDragMode(QtW.QGraphicsView.ScrollHandDrag); self.setMouseTracking(True)
        self.setTransformationAnchor(QtW.QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QtG.QColor("#000000")); self.setFocusPolicy(QtC.Qt.StrongFocus)
        self._fitted = False; self.coords = True

    def set_image(self, qimg, coords=True):
        """Show an image; coords=False for the 3D view (no voxel read-out, refit each time)."""
        same = not self.pix.pixmap().isNull() and self.pix.pixmap().size() == qimg.size() and self.coords == coords
        self.pix.setPixmap(QtG.QPixmap.fromImage(qimg)); self.coords = coords
        self.scene().setSceneRect(QtC.QRectF(qimg.rect()))
        if not same or not self._fitted:
            self.reset_zoom()

    def set_clip(self, rect):
        self.clip_item.setVisible(rect is not None and self.coords)
        if rect is not None:
            self.clip_item.setRect(rect)

    def reset_zoom(self):
        self.resetTransform(); self.fitInView(self.pix, QtC.Qt.KeepAspectRatio); self._fitted = True

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._fitted and self.transform().m11() <= self._fit_scale() * 1.001:
            self.reset_zoom()

    def _fit_scale(self):
        r = self.pix.boundingRect()
        if r.isEmpty():
            return 1.0
        return min(self.viewport().width() / r.width(), self.viewport().height() / r.height())

    def wheelEvent(self, e):
        n = e.angleDelta().y() / 120.0
        if e.modifiers() & QtC.Qt.ControlModifier:
            self.scale(1.15 ** n, 1.15 ** n)
        elif n:
            self.stepped.emit(-1 if n > 0 else 1)  # wheel down: next slice

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        p = self.pix.mapFromScene(self.mapToScene(e.position().toPoint()))
        if self.coords:
            self.hovered.emit(p.x(), p.y())

    def leaveEvent(self, e):
        super().leaveEvent(e); self.left.emit()

    def mouseDoubleClickEvent(self, e):
        self.reset_zoom()

    def keyPressEvent(self, e):
        e.ignore()  # slice keys are handled by the Explore panel


class ExplorePanel(QtW.QWidget):
    """Controls in the Explore tab; draws into `self.page` (the view and the read-out line), shown on the right."""
    KEYS = {QtC.Qt.Key_Down: 1, QtC.Qt.Key_Right: 1, QtC.Qt.Key_Up: -1, QtC.Qt.Key_Left: -1,  # as in Fiji: down = next slice
            QtC.Qt.Key_PageDown: 10, QtC.Qt.Key_PageUp: -10}

    def __init__(self, studio, get_spec, spec_for, on_range, on_focus, say):
        super().__init__()
        self.st, self.get_spec, self.spec_for, self.on_range, self.on_focus, self.say = studio, get_spec, spec_for, on_range, on_focus, say
        self.k = 0; self.nz = 1; self._cache = OrderedDict(); self._hover = None
        lay = QtW.QVBoxLayout(self)
        g = QtW.QGroupBox("Slice"); gl = QtW.QVBoxLayout(g); lay.addWidget(g)
        row = QtW.QHBoxLayout()
        self.prev_btn = QtW.QPushButton("Previous"); self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn = QtW.QPushButton("Next"); self.next_btn.clicked.connect(lambda: self.step(1))
        self.slider = QtW.QSlider(QtC.Qt.Horizontal); self.slider.valueChanged.connect(lambda v: self.go(v - 1))
        self.slider.setToolTip("Go through the slices (or use the arrow keys and the mouse wheel over the slice)")
        self.spin = QtW.QSpinBox(); self.spin.valueChanged.connect(lambda v: self.go(v - 1))
        row.addWidget(self.prev_btn); row.addWidget(self.slider, 1); row.addWidget(self.spin); row.addWidget(self.next_btn)
        gl.addLayout(row)
        tip = QtW.QLabel("Keys: Down/Right next slice, Up/Left previous, Page Down/Page Up ten slices, Home/End first/last. "
                         "Mouse wheel: slice; Ctrl + wheel: zoom; drag: move; double-click: fit.")
        tip.setWordWrap(True); tip.setStyleSheet(DIM); gl.addWidget(tip)

        g = QtW.QGroupBox("View"); gl = QtW.QVBoxLayout(g); lay.addWidget(g)
        self.mode_2d = QtW.QRadioButton("2D slice"); self.mode_3d = QtW.QRadioButton("3D at this slice")
        self.mode_2d.setChecked(True)
        for b in (self.mode_2d, self.mode_3d):
            b.toggled.connect(lambda _=False: self._mode_changed()); gl.addWidget(b)
        form = QtW.QFormLayout()
        self.overlay_cb = QtW.QComboBox()
        for t, d in OVERLAYS:
            self.overlay_cb.addItem(t, d)
        self.overlay_cb.currentIndexChanged.connect(lambda _: self.refresh())
        self.alpha_s = QtW.QSlider(QtC.Qt.Horizontal); self.alpha_s.setRange(0, 100); self.alpha_s.setValue(40)
        self.alpha_s.valueChanged.connect(lambda _: self.refresh())
        self.cut_cb = QtW.QComboBox()
        for t, d in CUTS:
            self.cut_cb.addItem(t, d)
        self.cut_cb.currentIndexChanged.connect(lambda _: self.refresh())
        form.addRow("Segmentation", self.overlay_cb); form.addRow("Overlay opacity", self.alpha_s); form.addRow("Show", self.cut_cb)
        gl.addLayout(form)
        self.form = form
        gl.addWidget(self._dim("Structure colours and which ones are shown come from the Structures tab. "
                               "The dashed box is the clip box; slices outside the movie's range are dimmed."))

        g = QtW.QGroupBox("Use in the movie"); gl = QtW.QVBoxLayout(g); lay.addWidget(g)
        row = QtW.QHBoxLayout()
        self.start_btn = QtW.QPushButton("Start the slice sweep here"); self.start_btn.clicked.connect(lambda: self.set_range(start=True))
        self.end_btn = QtW.QPushButton("End the slice sweep here"); self.end_btn.clicked.connect(lambda: self.set_range(start=False))
        row.addWidget(self.start_btn); row.addWidget(self.end_btn); gl.addLayout(row)
        self.range_lbl = self._dim(""); gl.addWidget(self.range_lbl)
        self.focus_btn = QtW.QPushButton("Add a close-up of the structure under the mouse")
        self.focus_btn.clicked.connect(self.add_focus); self.focus_btn.setEnabled(False); gl.addWidget(self.focus_btn)
        lay.addStretch(1)

        # the right-hand page
        self.page = QtW.QWidget(); pl = QtW.QVBoxLayout(self.page); pl.setContentsMargins(0, 0, 0, 0)
        self.view = SliceView(); pl.addWidget(self.view, 1)
        self.view.stepped.connect(self.step); self.view.hovered.connect(self._hovered); self.view.left.connect(self._left)
        self.read_lbl = self._dim(""); self.read_lbl.setWordWrap(False); pl.addWidget(self.read_lbl)
        for w in (self, self.page, self.view, self.slider, self.spin):
            w.installEventFilter(self)
        self._mode_changed(refresh=False)

    @staticmethod
    def _dim(text):
        lab = QtW.QLabel(text); lab.setWordWrap(True); lab.setStyleSheet(DIM)
        return lab

    # ---- slice navigation ----
    def set_data(self, info):
        """New data loaded: keep the current slice if it still exists."""
        nz = (info.get("dims") or [1, 1, 1])[2]; self.nz = nz; self.k = min(self.k, nz - 1); self._cache.clear()
        for w in (self.slider, self.spin):
            w.blockSignals(True); w.setRange(1, nz); w.setValue(self.k + 1); w.blockSignals(False)
        has_s = bool(info.get("has_segmentation"))
        self.overlay_cb.setEnabled(has_s); self.alpha_s.setEnabled(has_s); self.cut_cb.setEnabled(has_s)
        self._range_text()

    def go(self, k):
        k = int(min(self.nz - 1, max(0, k)))
        if k == self.k and self.slider.value() == k + 1:
            return
        self.k = k
        for w in (self.slider, self.spin):
            w.blockSignals(True); w.setValue(k + 1); w.blockSignals(False)
        self.refresh()
        if self._hover is not None:
            self._hovered(*self._hover)

    def step(self, n):
        self.go(self.k + n)

    def key(self, key):
        """Handle a slice key; returns True when it was one."""
        if key in self.KEYS:
            self.step(self.KEYS[key]); return True
        if key == QtC.Qt.Key_Home:
            self.go(0); return True
        if key == QtC.Qt.Key_End:
            self.go(self.nz - 1); return True
        return False

    def eventFilter(self, obj, e):
        if e.type() == QtC.QEvent.KeyPress and self.key(e.key()):
            return True
        return False

    # ---- drawing ----
    def _mode_changed(self, refresh=True):
        three = self.mode_3d.isChecked()
        for w in (self.overlay_cb, self.alpha_s):
            w.setVisible(not three); self.form.labelForField(w).setVisible(not three)
        self.cut_cb.setVisible(three); self.form.labelForField(self.cut_cb).setVisible(three)
        self.focus_btn.setVisible(not three)
        if refresh:
            self.refresh()

    def image_2d(self, spec):
        key = (self.k, self.overlay_cb.currentData(), self.alpha_s.value(),
               json.dumps([spec.get("classes"), spec.get("style"), spec.get("z_range"), spec.get("clip")], sort_keys=True),
               tuple((k, c.get("threshold")) for k, c in self.st.ds.classes.items()))
        if key not in self._cache:
            self._cache[key] = slice_image(self.st.ds, spec, self.k, self.overlay_cb.currentData(), self.alpha_s.value() / 100)
            while len(self._cache) > 48:
                self._cache.popitem(last=False)
        return self._cache[key]

    def state_3d(self, spec):
        """The movie renderer's frame state for 'this slice' seen from the overview position."""
        frames, _ = build_timeline(spec, self.st.ds)
        over = [f for f in frames if f["phase"] == "main" and f["reveal"]] or frames
        cut = self.cut_cb.currentData()
        return dict(over[0], k=self.k, reveal=cut != "none", cut=cut, e=0.0, target=None, caption="",
                    slice=default_opacity(spec, self.st.has_tomogram))

    def refresh(self):
        spec = self.get_spec()
        if spec is None or not self.st.can_show or not self.page.isEnabled():  # disabled while files are loading
            return
        if self.mode_3d.isChecked():
            if self.st.ren is None:
                return
            sp = self.spec_for(); w = max(320, self.view.viewport().width()); h = max(180, self.view.viewport().height())
            img = self.st.ren.draw(self.state_3d(sp), sp, (w, h), aa="fxaa")  # no smoothing setting here: Fast
            self.view.set_image(to_qimage(img), coords=False); self.view.set_clip(None)
            self.read_lbl.setText(f"slice {self.k + 1} of {self.nz}" + self._z_text())
            return
        self.view.set_image(to_qimage(self.image_2d(spec)), coords=True)
        cb = clip_box(spec, self.st.ds.shape)
        self.view.set_clip(QtC.QRectF(cb[0][0], cb[0][1], cb[1][0] - cb[0][0] + 1, cb[1][1] - cb[0][1] + 1) if cb else None)
        if self._hover is None:
            self.read_lbl.setText(f"slice {self.k + 1} of {self.nz}" + self._z_text())

    def _z_text(self):
        v = self.st.ds.vox
        return f" · z = {self.k * v / 10:.1f} nm" if v else ""

    def _hovered(self, x, y):
        self._hover = (x, y)
        r = readout(self.st.ds, self.get_spec() or {}, self.k, int(np.floor(x)), int(np.floor(y)))
        if r is None:
            return self._left()
        parts = [f"slice {self.k + 1} of {self.nz}" + self._z_text(), f"x {r['x']}, y {r['y']}"]
        if r["grey"] is not None:
            parts.append(f"grey value {r['grey']:.4g}")
        parts.append(r["name"] or "no structure")
        self.read_lbl.setText("  ·  ".join(parts))
        self.under = r
        self.focus_btn.setEnabled(bool(r["key"]))
        self.focus_btn.setText(f"Add a close-up of {r['name']}" if r["key"] else "Add a close-up of the structure under the mouse")

    def _left(self):
        self._hover = None
        self.read_lbl.setText(f"slice {self.k + 1} of {self.nz}" + self._z_text())

    # ---- hand-over to the movie ----
    def _range_text(self):
        spec = self.get_spec()
        zr = (spec or {}).get("z_range")
        self.range_lbl.setText(f"The slice sweep goes from slice {zr[0] + 1} to {zr[1] + 1}." if zr else
                               "The slice sweep goes through all slices.")

    def set_range(self, start):
        spec = self.get_spec()
        if spec is None:
            return
        lo, hi = spec.get("z_range") or [0, self.nz - 1]
        lo, hi = (self.k, max(self.k, hi)) if start else (min(lo, self.k), self.k)
        self.on_range(None if (lo, hi) == (0, self.nz - 1) else [int(lo), int(hi)])
        self._range_text(); self._cache.clear(); self.refresh()
        self.say(f"The slice sweep now goes from slice {lo + 1} to {hi + 1}.", "ok")

    def add_focus(self):
        r = getattr(self, "under", None)
        if r and r.get("key"):
            self.on_focus(r["key"], r["name"])
            self.say(f"Added a close-up of {r['name']} to the movie.", "ok")
