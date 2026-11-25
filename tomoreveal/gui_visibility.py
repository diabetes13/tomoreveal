"""3.6 (PLAN-3.6 phase 4, R18): the Structures panel docked on the right of the preview, with an eye per structure that
hides it in the whole movie or in the selected part only (`part["each"][key] = "hide"`, faded by `storyboard.part_visibility`)."""
from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from . import edits, storyboard as sb
from .gui_theme import DIM, part_colour

SCOPES = (("the whole movie", "movie"), ("the selected part only", "part"))


class EyeButton(QtW.QToolButton):
    """Checked = shown. Draws an open or a closed eye, and a small dot when this part differs from the whole movie."""

    def __init__(self):
        super().__init__(); self.setCheckable(True); self.setAutoRaise(True); self.setFixedSize(26, 22)
        self.dot = False

    def set_state(self, shown, dot=False, enabled=True, tip=""):
        self.blockSignals(True); self.setChecked(bool(shown)); self.blockSignals(False)
        self.dot = bool(dot); self.setEnabled(enabled)
        if tip:
            self.setToolTip(tip)
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QtG.QPainter(self); p.setRenderHint(QtG.QPainter.Antialiasing)
        pal = self.palette(); col = pal.windowText().color()
        if not self.isEnabled():
            col = pal.placeholderText().color()
        w, h = self.width(), self.height(); cx, cy = w / 2, h / 2; rx, ry = 8.5, 5.0
        path = QtG.QPainterPath(); path.moveTo(cx - rx, cy)
        path.quadTo(cx, cy - 2 * ry, cx + rx, cy); path.quadTo(cx, cy + 2 * ry, cx - rx, cy)
        if self.isChecked():
            p.setPen(QtG.QPen(col, 1.4)); p.setBrush(QtC.Qt.NoBrush); p.drawPath(path)
            p.setBrush(col); p.setPen(QtC.Qt.NoPen); p.drawEllipse(QtC.QPointF(cx, cy), 2.6, 2.6)
        else:  # closed: the lower lid and lashes, faint
            faint = QtG.QColor(col); faint.setAlphaF(0.55)
            p.setPen(QtG.QPen(faint, 1.4)); p.setBrush(QtC.Qt.NoBrush)
            lid = QtG.QPainterPath(); lid.moveTo(cx - rx, cy - 1); lid.quadTo(cx, cy + 2 * ry - 2, cx + rx, cy - 1); p.drawPath(lid)
            for dx in (-5, 0, 5):
                p.drawLine(QtC.QPointF(cx + dx, cy + ry - 1.5), QtC.QPointF(cx + dx * 1.2, cy + ry + 1.5))
        if self.dot:
            p.setPen(QtC.Qt.NoPen); p.setBrush(QtG.QColor("#ff9f1c")); p.drawEllipse(QtC.QPointF(w - 4, 4), 3, 3)
        p.end()


class ScopeRow(QtW.QWidget):
    """'Hide or show in: [the whole movie / the selected part only]' and the selected part's name in its colour."""

    def __init__(self, win):
        super().__init__(); self.win = win
        v = QtW.QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(2)
        h = QtW.QHBoxLayout(); h.setContentsMargins(0, 0, 0, 0); v.addLayout(h)
        h.addWidget(QtW.QLabel("Eyes hide or show in"))
        self.combo = QtW.QComboBox(); self.combo.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        for t, d in SCOPES:
            self.combo.addItem(t, d)
        self.combo.setCurrentIndex(1)
        self.combo.setToolTip("The whole movie: an eye switches the structure off everywhere. The selected part only: it "
                              "is hidden in the part selected on the timeline (it fades out and back in at the part's edges); "
                              "a dot marks eyes that differ from the whole movie.")
        self.combo.activated.connect(lambda _: refresh_eyes(win)); h.addWidget(self.combo, 1)
        self.part_lbl = QtW.QLabel(""); self.part_lbl.setWordWrap(True); v.addWidget(self.part_lbl)

    def scope(self):
        """"part" only when a part is selected; otherwise the whole movie."""
        w = self.win
        if self.combo.currentData() == "part" and w.spec is not None and w.selected_part and w.parts_panel.part(w.selected_part):
            return "part"
        return "movie"


def _part(win):
    return win.parts_panel.part(win.selected_part) if win.spec is not None and win.selected_part else None


def refresh_eyes(win):
    """Set every eye from the settings (after any edit, undo, or a new selection)."""
    rows = getattr(win, "_grid_rows", None)
    if not rows or win.spec is None or not hasattr(win, "vis_scope"):
        return
    scope = win.vis_scope.scope(); part = _part(win) if scope == "part" else None
    if part is not None:
        win.vis_scope.part_lbl.setText(f"<span style='color:{part_colour(part)}'>■</span> part "
                                       f"{win.spec['shots'].index(part) + 1}: {sb.part_name(part, win.spec, win.st.ds)}")
        win.vis_scope.part_lbl.setStyleSheet("")
    else:
        win.vis_scope.part_lbl.setText("Select a part on the timeline to hide structures in that part only."
                                       if win.vis_scope.combo.currentData() == "part" else "")
        win.vis_scope.part_lbl.setStyleSheet(DIM)
    hiding = {p["id"]: sb.hidden_in(p) for p in win.spec["shots"]}
    for k, row in rows.items():
        on = bool(win.spec["classes"].get(k, {}).get("show", True))
        n = sum(1 for h in hiding.values() if k in h)
        if part is None:
            tip = ("Shown in the movie; click to hide it everywhere" if on else "Hidden in the whole movie; click to show it")
            if n:
                tip += f". Hidden in {n} part{'s' if n > 1 else ''} only (orange dot)"
            if row.get("why"):
                tip += f" ({row['why']})"
            row["cb"].set_state(on, dot=bool(n) and on, tip=tip)
        else:
            gone = k in hiding.get(part["id"], set())
            if not on:
                row["cb"].set_state(False, enabled=False, tip="Hidden in the whole movie: switch the eyes to 'the whole movie' to show it")
            else:
                row["cb"].set_state(not gone, dot=gone, tip=("Hidden in this part only; click to show it here" if gone else
                                                             "Shown in this part; click to hide it in this part only"))


def eye_toggled(win, c, on):
    """An eye was clicked: one undo step."""
    if win.spec is None:
        return
    part = _part(win) if win.vis_scope.scope() == "part" else None
    if part is None:
        win._show_set(c, on); win.editor.record()
    else:
        set_hidden(win, part["id"], c["key"], not on)
    refresh_eyes(win)


def set_hidden(win, pid, key, hide):
    """Hide a structure in one part only (or show it there again), as one edit."""
    p = win.parts_panel.part(pid)
    if p is None:
        return
    each = dict(p.get("each") or {})
    if hide:
        each[key] = "hide"
    elif each.get(key) == "hide":
        each.pop(key)
    if each:
        edits.set_part(win.spec, pid, each=each)
    else:
        p.pop("each", None)
    win.editor.commit()
