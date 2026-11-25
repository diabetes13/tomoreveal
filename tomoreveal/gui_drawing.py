"""Drawing arrows and shapes in the preview (3.6, R3 + R17): the toolbar above the preview (Select, Arrow, Curved
arrow, Freehand arrow, Line, Ellipse, Rectangle, and "Follows the structure"), and the pen that turns one mouse drag
into one arrow or shape (one undo step)."""
import math

import numpy as np
from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from . import annotations as an
from . import drawing
from .gui_theme import DIM

TOOLS = (("select", "Select", "⬚", "Select, move and change arrows, text and shapes (and move the camera)"),
         ("arrow", "Arrow", "→", "Arrow: press where its tail goes, drag, and let go where it points. Shift: steps of 15°"),
         ("curve", "Curved", "⤻", "Curved arrow: drag from the tail to the tip, then drag the yellow handle to bend it"),
         ("freehand", "Freehand", "✎", "Freehand arrow: draw it as with a pen; the arrowhead goes at the end"),
         ("line", "Line", "╱", "A straight line without arrowheads (add them with a right-click). Shift: steps of 15°"),
         ("ellipse", "Ellipse", "◯", "An ellipse: drag from one corner of its box to the other. Shift: a circle"),
         ("rect", "Rectangle", "▭", "A rectangle: drag from one corner to the other. Shift: a square"))
CLICK_PX = 6  # a press and release closer than this is a click, not a drawing


class DrawBar(QtW.QWidget):
    """'Draw: [Select] [Arrow] [Curved] [Freehand] [Line] [Ellipse] [Rectangle]   [Follows the structure]'."""

    def __init__(self, win):
        super().__init__(); self.win = win
        h = QtW.QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 2); h.setSpacing(3)
        lab = QtW.QLabel("Draw:"); lab.setStyleSheet(DIM); h.addWidget(lab)
        self.group = QtW.QButtonGroup(self); self.group.setExclusive(True); self.btn = {}
        for name, text, glyph, tip in TOOLS:
            b = QtW.QToolButton(); b.setText(f"{glyph} {text}"); b.setCheckable(True); b.setToolTip(tip)
            b.setToolButtonStyle(QtC.Qt.ToolButtonTextOnly); self.group.addButton(b); h.addWidget(b); self.btn[name] = b
        self.btn["select"].setChecked(True)
        self.group.buttonToggled.connect(self._tool_changed)
        h.addStretch(1)
        self.pin = QtW.QToolButton(); self.pin.setCheckable(True)
        self.pin.setChecked(bool(win.settings.value("draw/pin", True, type=bool)))
        self.pin.setToolTip("On: the tip of a new arrow (or shape) is pinned to the structure under it and moves with it when "
                            "the camera moves. Off: it stays where it is on the picture. With arrows selected, this switches "
                            "them.")
        self.pin.clicked.connect(self._pin_clicked); h.addWidget(self.pin)
        self._label()

    def tool(self):
        return next((n for n, b in self.btn.items() if b.isChecked()), "select")

    def set_tool(self, name):
        self.btn.get(name, self.btn["select"]).setChecked(True)

    def _tool_changed(self, b, on):
        if not on:
            return
        w = self.win; t = self.tool()
        w.prev.setCursor(QtC.Qt.CrossCursor if t != "select" else QtC.Qt.ArrowCursor)
        if t != "select":
            w.end_picks()
            w.say(dict((n, tip) for n, _, _, tip in TOOLS)[t] + ". Esc: back to Select.")

    def _label(self):
        self.pin.setText("📌 Follows the structure" if self.pin.isChecked() else "Stays on the picture")

    def selected_items(self):
        w = self.win
        if w.spec is None or not hasattr(w, "ann_mouse"):
            return []
        return [a for a in (an.find(w.spec, x) for x in w.ann_mouse.selected) if a is not None and a["kind"] != "text"]

    def _pin_clicked(self, on):
        """With arrows or shapes selected: pin them (3D) or put them on the picture (2D); otherwise the choice for new ones."""
        from .gui_annotations import set_place
        sel = self.selected_items(); self._label()
        if not sel:
            self.win.settings.setValue("draw/pin", bool(on)); return
        for a in sel:
            set_place(self.win, a["id"], "3d" if on else "2d")
        self.sync()

    def sync(self):
        """The pin button shows the selected arrows' place (or the choice for new ones)."""
        sel = self.selected_items()
        on = (not an.is_2d(sel[0])) if sel else bool(self.win.settings.value("draw/pin", True, type=bool))
        self.pin.blockSignals(True); self.pin.setChecked(on); self.pin.blockSignals(False); self._label()


class Pen:
    """One drag in the preview with a drawing tool: a new arrow or shape when the mouse is let go."""

    def __init__(self, win):
        self.win = win; self.stroke = None

    def tool(self):
        bar = getattr(self.win, "drawbar", None)
        return bar.tool() if bar is not None else "select"

    def active(self):
        w = self.win
        return self.tool() != "select" and w.spec is not None and w.st.ren is not None and w.gen is None and not w.busy

    def press(self, f, pos, mods):
        if not self.active() or f is None:
            return False
        self.stroke = dict(tool=self.tool(), pts=[tuple(f)], px=[(pos.x(), pos.y())], shift=bool(mods & QtC.Qt.ShiftModifier))
        return True

    def move(self, f, pos, mods):
        s = self.stroke
        if s is None or f is None:
            return
        s["shift"] = bool(mods & QtC.Qt.ShiftModifier)
        if s["tool"] == "freehand":
            s["pts"].append(tuple(f)); s["px"].append((pos.x(), pos.y()))
        else:
            s["pts"] = [s["pts"][0], tuple(f)]; s["px"] = [s["px"][0], (pos.x(), pos.y())]
        self.win.prev.update()

    def release(self, f, pos, mods):
        if self.stroke is None:
            return None
        if f is not None:
            self.move(f, pos, mods)
        s, self.stroke = self.stroke, None
        self.win.prev.update()
        return finish(self.win, s)

    def cancel(self):
        self.stroke = None; self.win.prev.update()

    def geometry(self, s):
        """The points of the shape being drawn (fractions), with Shift applied."""
        from .gui_annotations import _snap_frac, _square_frac
        pts = list(s["pts"]); t = s["tool"]
        if len(pts) >= 2 and s.get("shift"):
            if t in ("arrow", "line"):
                pts[-1] = _snap_frac(self.win, pts[0], pts[-1])
            elif t in ("ellipse", "rect"):
                pts[-1] = _square_frac(self.win, pts[0], pts[-1])
        return pts

    def paint(self, p, prev):
        s = self.stroke
        if s is None or len(s["pts"]) < 2:
            return
        pts = self.geometry(s); t = s["tool"]
        if t == "freehand":
            line = np.asarray(pts, float)
        elif t == "curve":
            line = drawing.bezier(*_curve_points(self.win, pts[0], pts[-1]))
        elif t == "ellipse":
            line = drawing.ellipse(pts[0], pts[-1])
        elif t == "rect":
            line = drawing.rect(pts[0], pts[-1])
        else:
            line = np.asarray([pts[0], pts[-1]], float)
        path = QtG.QPainterPath(); q = [prev.to_widget(float(x), float(y)) for x, y in line]
        path.moveTo(q[0])
        for c in q[1:]:
            path.lineTo(c)
        p.setRenderHint(QtG.QPainter.Antialiasing)
        for col, wd in ((QtG.QColor(0, 0, 0, 150), 4.0), (QtG.QColor(255, 210, 63), 2.0)):
            p.setPen(QtG.QPen(col, wd, QtC.Qt.SolidLine, QtC.Qt.RoundCap, QtC.Qt.RoundJoin)); p.setBrush(QtC.Qt.NoBrush); p.drawPath(path)


def _curve_points(win, tail, tip):
    """Tail, bend and tip of a new curved arrow: the bend a little to one side, so it is curved from the start."""
    from .gui_annotations import _px
    W, H = _px(win); a = np.array([tail[0] * W, tail[1] * H]); b = np.array([tip[0] * W, tip[1] * H])
    d = b - a; L = float(np.hypot(*d)) or 1.0; n = np.array([d[1], -d[0]]) / L
    if n[1] > 0:  # bend upwards on the screen
        n = -n
    m = (a + b) / 2 + n * 0.22 * L
    c = drawing.control_for_middle(a, m, b)
    return [tuple(tail), (c[0] / W, c[1] / H), tuple(tip)]


def finish(win, s):
    """Turn a finished drag into an arrow or shape (one undo step). Returns the new item, or None."""
    from .gui_annotations import _px, add_arrow_at, commit, pick_anchor
    pen = win.pen; t = s["tool"]; pts = pen.geometry(s)
    (x0, y0), (x1, y1) = s["px"][0], s["px"][-1]
    if math.hypot(x1 - x0, y1 - y0) < CLICK_PX and t != "freehand":
        if t == "arrow":  # a click with the arrow tool: an arrow pointing there, as "+ Add arrow" does
            return add_arrow_at(win, pts[0])
        return None
    pin = win.drawbar.pin.isChecked(); pid = win.selected_part or None; parts = [pid] if pid else "all"
    if t == "arrow":
        tail, tip = pts[0], pts[-1]; W, H = _px(win)
        anc = pick_anchor(win, tip) if pin else None
        a = an.new_arrow(anc["xyz"] if anc else [0.0, 0.0, 0.0], anc["structure"] if anc else None,
                         anc["component"] if anc else None, "", parts=parts)
        if anc is None:
            a["place"] = "2d"; a["tip2d"] = [round(tip[0], 4), round(tip[1], 4)]
        dx, dy = (tail[0] - tip[0]) * W, (tail[1] - tip[1]) * H
        a["tail"] = dict(angle=round(math.degrees(math.atan2(-dy, dx)) % 360, 1), length=round(max(0.02, math.hypot(dx, dy) / H), 4))
    else:
        if t == "freehand":
            kept = drawing.simplify(np.asarray(s["px"], float))  # 1.5 px on screen
            px = np.asarray(s["px"], float); P = np.asarray(pts, float)
            idx = [int(np.argmin(np.hypot(*(px - k).T))) for k in kept]
            pts = [tuple(P[i]) for i in idx]
            if len(pts) < 2:
                return None
        elif t == "curve":
            pts = _curve_points(win, pts[0], pts[-1])
        else:
            pts = [pts[0], pts[-1]]
        a = an.new_drawn(t, pts, parts=parts)
        if pin:
            anc = pick_anchor(win, tuple(float(v) for v in an.ref_point(a)))
            if anc is not None:
                a["anchor"] = anc; a["place"] = "3d"
    win.spec.setdefault("annotations", []).append(a)
    win.ann_mouse.selected = [a["id"]]; win.drawbar.set_tool("select")
    commit(win)
    pinned = not an.is_2d(a)
    win.say(("Pinned to the structure under its tip. " if pinned else "It stays where it is on the picture. ")
            + ("Drag the yellow handle to bend it. " if t == "curve" else "")
            + "Right-click it for arrowheads, colour and more.", "ok")
    return a
