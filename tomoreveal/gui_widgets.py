"""Small widgets and helpers of the main window: the preview, the worker thread, the wheel filter, the range slider."""
import traceback

import numpy as np
from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from .common import UserError


def to_qimage(a):
    a = np.ascontiguousarray(a); h, w = a.shape[:2]; ch = a.shape[2] if a.ndim == 3 else 1
    fmt = QtG.QImage.Format_RGBA8888 if ch == 4 else QtG.QImage.Format_RGB888
    return QtG.QImage(a.tobytes(), w, h, ch * w, fmt).copy()


def fmt_time(t):
    t = int(round(t))
    return f"{t // 60} min {t % 60:02d} s" if t >= 90 else f"{t} s"


WHEEL_PROOF = (QtW.QComboBox, QtW.QAbstractSpinBox, QtW.QAbstractSlider)


class TipFilter(QtC.QObject):
    """The one application-wide filter: swallows tooltips while Help > Show tips on hover is off, and keeps the mouse
    wheel from changing drop-down lists, number boxes and sliders (3.5): the wheel scrolls the panel instead."""
    def eventFilter(self, obj, e):
        t = e.type()
        if t == QtC.QEvent.Wheel:
            return wheel_to_panel(obj, e)
        return t == QtC.QEvent.ToolTip and QtW.QApplication.instance().property("tomoreveal_tips") is False


def wheel_to_panel(obj, e):
    """A wheel turn over a setting scrolls the panel around it; the setting itself never changes. True = handled."""
    if not isinstance(obj, WHEEL_PROOF) or isinstance(obj, QtW.QScrollBar) or obj.property("tomoreveal_wheel"):
        return False
    w = obj.parentWidget()
    while w is not None and not isinstance(w, QtW.QAbstractScrollArea):
        w = w.parentWidget()
    if w is not None:
        bar = w.verticalScrollBar()
        if bar is not None and bar.maximum() > bar.minimum():
            bar.setValue(bar.value() - round(e.angleDelta().y() / 120 * bar.singleStep() * 3))
    e.accept(); return True


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
    boxed = QtC.Signal(float, float, float, float)  # a frame drawn with the mouse (fractions of the image)

    def __init__(self):
        super().__init__(); self.setAlignment(QtC.Qt.AlignCenter); self.setWordWrap(True)
        self.setMinimumSize(320, 180); self.setSizePolicy(QtW.QSizePolicy.Ignored, QtW.QSizePolicy.Ignored)
        self.setStyleSheet("background: palette(base); color: palette(placeholder-text); border-radius:8px; padding:6px")
        self._img = None; self._press = None; self._moved = False; self.overlay = None; self._ov = False
        self.box_mode = False; self._box = None  # drawing a frame for a close-up of a region
        self.nav = None; self._nav = False  # gui_navigate.Navigator: rotate, move, zoom, menu (3.5)
        self.setMouseTracking(True); self.setFocusPolicy(QtC.Qt.ClickFocus)
        self.help_btn = QtW.QToolButton(self); self.help_btn.setText("?"); self.help_btn.setAutoRaise(True)
        self.help_btn.setToolTip("How to move around in the preview")
        self.help_btn.setStyleSheet("QToolButton { background: rgba(0, 0, 0, 140); color: #ffffff; border-radius: 9px; "
                                    "min-width: 18px; min-height: 18px; font-weight: bold }")
        self.help_btn.clicked.connect(self.show_help)
        self.note = QtW.QLabel(self); self.note.setStyleSheet(
            "background: rgba(0, 0, 0, 160); color: #ffffff; border-radius: 4px; padding: 3px 8px")
        self.note.move(10, 10); self.note.setVisible(False)

    def set_note(self, text):
        """A line in the preview corner, e.g. which part's view dragging changes."""
        self.note.setText(text or ""); self.note.adjustSize(); self.note.setVisible(bool(text))

    def set_image(self, qimg):
        self._img = qimg; self._rescale()
        if self.nav is not None and not getattr(self, "_hinted", False):  # once: how to move around (3.5)
            self._hinted = True; st = self.nav.win.settings
            if not st.value("nav/hint_shown", False, type=bool):
                from .gui_navigate import HINT
                st.setValue("nav/hint_shown", True); self.set_note(HINT)
                QtC.QTimer.singleShot(10000, lambda: self.set_note("") if self.note.text() == HINT else None)

    def clear_image(self, text):
        self._img = None; self.clear(); self.setText(text)

    def _rescale(self):
        if self._img is not None:
            self.setPixmap(QtG.QPixmap.fromImage(self._img).scaled(self.size(), QtC.Qt.KeepAspectRatio, QtC.Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e); self._rescale(); self.help_btn.move(self.width() - 30, 8)

    def show_help(self):
        from .gui_navigate import HELP
        QtW.QToolTip.showText(self.help_btn.mapToGlobal(QtC.QPoint(-380, 24)), HELP, self.help_btn)

    def frac(self, pos, free=False):
        """Position as fractions of the shown image (None outside it unless free)."""
        pm = self.pixmap()
        if pm is None or pm.isNull():
            return None
        w, h = pm.width(), pm.height(); x0, y0 = (self.width() - w) / 2, (self.height() - h) / 2
        fx, fy = (pos.x() - x0) / w, (pos.y() - y0) / h
        return (fx, fy) if free or (0 <= fx <= 1 and 0 <= fy <= 1) else None

    def image_px(self):
        """Size of the shown image on screen (pixels)."""
        pm = self.pixmap()
        return None if pm is None or pm.isNull() else (pm.width(), pm.height())

    def to_widget(self, fx, fy):
        pm = self.pixmap(); w, h = pm.width(), pm.height()
        return QtC.QPointF((self.width() - w) / 2 + fx * w, (self.height() - h) / 2 + fy * h)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.overlay is not None and self.image_px():
            p = QtG.QPainter(self); self.overlay.paint(p, self.to_widget); p.end()
        if self._box is not None and self.image_px():
            p = QtG.QPainter(self); r = QtC.QRectF(self.to_widget(*self._box[0]), self.to_widget(*self._box[1])).normalized()
            p.fillRect(r, QtG.QColor(255, 210, 63, 50))
            for col, dash in ((QtG.QColor(0, 0, 0), QtC.Qt.SolidLine), (QtG.QColor(255, 210, 63), QtC.Qt.DashLine)):
                pen = QtG.QPen(col, 2 if dash == QtC.Qt.SolidLine else 1.5, dash); p.setPen(pen); p.drawRect(r)
            p.end()
        if self.nav is not None and self.image_px():
            w = self.nav.win; ren = w.st.ren
            if ren is not None and getattr(ren, "path_view", None) and w.selected_part:
                from .gui_paths import paint_path
                sel = w.path_point[1] if w.path_point and w.path_point[0] == w.selected_part else None
                hn = getattr(self.overlay, "hover_name", None) if self.overlay is not None else None
                hov = int(hn.split(":")[1]) if hn and hn.startswith("marker:") else None
                p = QtG.QPainter(self); paint_path(p, self, ren.path_view, sel, hov); p.end()
            from .gui_annotations import paint_selection
            p = QtG.QPainter(self); paint_selection(p, self, w); p.end()
            from .gui_navigate import paint_marker
            p = QtG.QPainter(self); p.setRenderHint(QtG.QPainter.Antialiasing); paint_marker(p, self, self.nav); p.end()

    def mousePressEvent(self, e):
        if self.box_mode and e.button() == QtC.Qt.LeftButton:
            f = self.frac(e.position().toPoint())
            if f:
                self._box = [f, f]; self.update()
            return
        pos = e.position().toPoint()
        if e.button() == QtC.Qt.LeftButton:
            self._ov = bool(self.overlay and self.overlay.press(self.frac(pos), e.modifiers()))
            self._press = pos; self._moved = False
            if self._ov:
                return
        if self.nav is not None and e.button() in (QtC.Qt.LeftButton, QtC.Qt.RightButton, QtC.Qt.MiddleButton):
            self._nav = self.nav.press(self.frac(pos), e.button(), e.modifiers(), pos)
            if self._nav:
                self._press = None

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._box is not None:
            f = self.frac(pos, free=True); self._box[1] = (min(max(f[0], 0.0), 1.0), min(max(f[1], 0.0), 1.0)); self.update()
            return
        if self._nav:
            self.nav.move(self.frac(pos, free=True), pos); self.update(); return
        if self._press is None:
            if self.overlay is not None:
                self.overlay.hover(self.frac(pos))
            return
        d = pos - self._press
        if self._moved or abs(d.x()) + abs(d.y()) > 4:
            self._moved = True
            if self._ov:
                self.overlay.move(self.frac(pos, free=True), e.modifiers())
            else:
                self.dragged.emit(d.x(), d.y())

    def contextMenuEvent(self, e):
        """Mouse: the menu opens on release of a right click that did not move (a right drag moves the view)."""
        if e.reason() == QtG.QContextMenuEvent.Keyboard and self.nav is not None:
            self.nav.menu((0.5, 0.5), e.globalPos())

    def open_menu(self, f, gpos):
        if self.overlay is not None and f is not None and self.overlay.context(f, gpos):
            return  # a legend, caption, path point ...: its own menu
        if self.nav is not None:
            self.nav.menu(f, gpos)

    def mouseDoubleClickEvent(self, e):
        f = self.frac(e.position().toPoint())
        if self.overlay is not None and f is not None and self.overlay.double(f):
            return
        if self.nav is not None and e.button() == QtC.Qt.LeftButton:
            self.nav.double(f)

    def keyPressEvent(self, e):
        if self.nav is not None and self.nav.key(e):
            return
        super().keyPressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._nav:
            self._nav = False; pos = e.position().toPoint()
            d = self.nav.drag
            if d is not None and not d["moved"] and d["button"] == QtC.Qt.RightButton:
                self.nav.drag = None; self.open_menu(self.frac(pos), e.globalPosition().toPoint()); return
            self.nav.release(self.frac(pos), e.globalPosition().toPoint()); self.update(); return
        if self._box is not None:
            (a, b), (c, d) = self._box; self._box = None; self.update()
            if abs(c - a) > 0.01 and abs(d - b) > 0.01:
                self.boxed.emit(min(a, c), min(b, d), max(a, c), max(b, d))
            return
        if self._press is None:
            return
        if self._ov:
            self._ov = False; self._press = None
            self.overlay.release()
            return
        if self._moved:
            self.released.emit()
        else:
            f = self.frac(e.position().toPoint())
            if f:
                self.clicked.emit(*f)
        self._press = None

    def wheelEvent(self, e):
        if self.nav is not None:
            self.nav.wheel(e.angleDelta().y(), self.frac(e.position().toPoint()), e.modifiers())
        else:
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
