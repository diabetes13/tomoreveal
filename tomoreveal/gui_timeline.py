"""The movie timeline under the preview: one block per part, a time ruler and a playhead."""
import math

from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from .storyboard import part_name

TYPE_COLOURS = {"slide": "#4e79a7", "overview": "#59a14f", "focus": "#d1495b", "hold": "#8a8f98"}  # readable in both themes
PLAYHEAD = "#ff9f1c"
RULER_H, ROW_TOP, ROW_H, MARGIN = 18, 22, 36, 8


class TimelineCanvas(QtW.QWidget):
    """The painted strip. Positions are in frames; `ppf` is pixels per frame and `offset` the scrolled pixels."""
    frame_changed = QtC.Signal(int)
    released = QtC.Signal()
    part_selected = QtC.Signal(str)
    part_activated = QtC.Signal(str)
    zoomed = QtC.Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(ROW_TOP + ROW_H + 6); self.setFocusPolicy(QtC.Qt.StrongFocus); self.setMouseTracking(True)
        self.plan = None; self.spec = None; self.frame = 0; self.selected = None
        self.ppf = 1.0; self.offset = 0.0; self.fit = True; self._drag = None
        self.parts = {}  # id -> dict(index, part, start, end, main=(start, count))

    # ---- data ----
    @property
    def total(self):
        return len(self.plan.frames) if self.plan else 0

    def set_plan(self, plan, spec):
        self.plan, self.spec = plan, spec; self.parts = {}
        idx = {p["id"]: i for i, p in enumerate(spec.get("shots", []))}
        for sg in plan.segments:
            pid = sg["part"]
            if pid is None:
                continue
            d = self.parts.setdefault(pid, dict(index=idx.get(pid, sg["index"]), start=sg["start"], end=sg["start"] + sg["count"], main=None))
            d["end"] = sg["start"] + sg["count"]
            if sg["phase"] == "main" and d["main"] is None:
                d["main"] = (sg["start"], sg["count"])
        self.frame = min(self.frame, max(0, self.total - 1))
        if self.fit:
            self.fit_all()
        self.update()

    def part(self, pid):
        for p in (self.spec or {}).get("shots", []):
            if p.get("id") == pid:
                return p
        return None

    # ---- geometry ----
    def fit_all(self):
        self.fit = True; self.offset = 0.0
        self.ppf = max(1e-3, (self.width() - 2 * MARGIN) / max(1, self.total)); self.zoomed.emit()

    def content_width(self):
        return 2 * MARGIN + self.total * self.ppf

    def x_of(self, f):
        return MARGIN + f * self.ppf - self.offset

    def frame_at(self, x):
        return int(min(max(0, self.total - 1), max(0, math.floor((x + self.offset - MARGIN) / self.ppf))))

    def segment_rects(self):
        """[(segment, QRectF)] of every segment, left to right; segments of 0 width get 3 px."""
        out = []
        for sg in (self.plan.segments if self.plan else []):
            x0, x1 = self.x_of(sg["start"]), self.x_of(sg["start"] + sg["count"])
            main = sg["phase"] == "main"
            top = ROW_TOP if main else ROW_TOP + ROW_H * 0.2
            out.append((sg, QtC.QRectF(x0, top, max(3.0, x1 - x0), ROW_H if main else ROW_H * 0.6)))
        return out

    def block_rect(self, pid):
        d = self.parts.get(pid)
        if not d:
            return None
        return QtC.QRectF(self.x_of(d["start"]), ROW_TOP, max(3.0, self.x_of(d["end"]) - self.x_of(d["start"])), ROW_H)

    def part_at(self, pos):
        if not (ROW_TOP <= pos.y() <= ROW_TOP + ROW_H):
            return None
        f = (pos.x() + self.offset - MARGIN) / self.ppf
        for pid, d in self.parts.items():
            if d["start"] <= f < d["end"]:
                return pid
        return None

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fit:
            self.fit_all()

    # ---- painting ----
    def paintEvent(self, e):
        p = QtG.QPainter(self); p.setRenderHint(QtG.QPainter.Antialiasing)
        pal = self.palette(); text = pal.color(QtG.QPalette.WindowText); dim = pal.color(QtG.QPalette.PlaceholderText)
        p.fillRect(self.rect(), pal.color(QtG.QPalette.Base))
        if not self.plan:
            return
        fps = self.plan.fps
        # ruler: a tick each second, a label every 1, 2, 5, 10, ... seconds depending on the zoom
        pps = self.ppf * fps
        step = next((s for s in (1, 2, 5, 10, 20, 30, 60, 120, 300) if s * pps >= 48), 600)
        f = p.font(); f.setPointSizeF(max(7.0, f.pointSizeF() * 0.85)); p.setFont(f)
        first = max(0, int(self.offset / pps) - 1); last = int((self.offset + self.width()) / pps) + 1
        for sec in range(first, min(last, int(self.total / fps) + 1) + 1):
            x = self.x_of(sec * fps)
            if sec % step == 0:
                p.setPen(text); p.drawLine(QtC.QPointF(x, RULER_H - 7), QtC.QPointF(x, RULER_H))
                p.drawText(QtC.QPointF(x + 3, RULER_H - 7), f"{sec} s")
            elif pps >= 4:
                p.setPen(dim); p.drawLine(QtC.QPointF(x, RULER_H - 4), QtC.QPointF(x, RULER_H))
        p.setPen(dim); p.drawLine(QtC.QPointF(0, RULER_H), QtC.QPointF(self.width(), RULER_H))
        # blocks
        for sg, r in self.segment_rects():
            col = QtG.QColor(TYPE_COLOURS.get(sg["type"], "#888888"))
            if sg["phase"] == "main":
                p.setPen(QtC.Qt.NoPen); p.setBrush(col); p.drawRoundedRect(r, 3, 3)
            else:  # transitions and pauses: lighter and hatched
                light = QtG.QColor(col); light.setAlpha(90)
                p.setPen(QtC.Qt.NoPen); p.setBrush(light); p.drawRect(r)
                hatch = QtG.QBrush(col, QtC.Qt.BDiagPattern); p.setBrush(hatch); p.drawRect(r)
        p.setPen(QtG.QColor("#ffffff"))
        for pid, d in self.parts.items():
            if not d["main"]:
                continue
            x0, x1 = self.x_of(d["main"][0]), self.x_of(d["main"][0] + d["main"][1])
            part = self.part(pid)
            label = f"{d['index'] + 1}  {part_name(part) if part else ''}"
            r = QtC.QRectF(x0 + 4, ROW_TOP, max(0.0, x1 - x0 - 8), ROW_H)
            if r.width() > 10:
                p.drawText(r, QtC.Qt.AlignVCenter | QtC.Qt.AlignLeft, p.fontMetrics().elidedText(label, QtC.Qt.ElideRight, int(r.width())))
        if self.selected in self.parts:
            pen = QtG.QPen(pal.color(QtG.QPalette.Highlight), 2.5); p.setPen(pen); p.setBrush(QtC.Qt.NoBrush)
            p.drawRoundedRect(self.block_rect(self.selected).adjusted(-1, -1, 1, 1), 4, 4)
        # playhead with a handle on the ruler
        x = self.x_of(self.frame + 0.5) if self.ppf > 2 else self.x_of(self.frame)
        p.setPen(QtG.QPen(QtG.QColor(PLAYHEAD), 2)); p.drawLine(QtC.QPointF(x, 2), QtC.QPointF(x, self.height()))
        p.setBrush(QtG.QColor(PLAYHEAD)); p.setPen(QtC.Qt.NoPen)
        p.drawPolygon(QtG.QPolygonF([QtC.QPointF(x - 6, 1), QtC.QPointF(x + 6, 1), QtC.QPointF(x, 9)]))

    # ---- mouse and keys ----
    def _move_playhead(self, f, emit=True):
        f = int(min(max(0, self.total - 1), max(0, f)))
        if f != self.frame or emit:
            self.frame = f; self.ensure_visible(); self.update()
            if emit:
                self.frame_changed.emit(f)

    def ensure_visible(self):
        if self.fit or not self.total:
            return
        x = self.x_of(self.frame)
        if x < MARGIN or x > self.width() - MARGIN:
            self.offset = max(0.0, min(self.content_width() - self.width(), self.offset + x - self.width() / 2)); self.zoomed.emit()

    def mousePressEvent(self, e):
        if e.button() != QtC.Qt.LeftButton or not self.total:
            return
        self.setFocus()
        pos = e.position(); pid = self.part_at(pos)
        if pid is not None:
            self.selected = pid; self.part_selected.emit(pid)
        self._drag = "playhead"; self._move_playhead(self.frame_at(pos.x()))

    def mouseMoveEvent(self, e):
        if self._drag == "playhead":
            self._move_playhead(self.frame_at(e.position().x()))

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = None; self.released.emit()

    def mouseDoubleClickEvent(self, e):
        pid = self.part_at(e.position())
        if pid is not None:
            self.selected = pid; self.part_selected.emit(pid); self.part_activated.emit(pid)

    def wheelEvent(self, e):
        n = e.angleDelta().y() / 120.0
        if e.modifiers() & QtC.Qt.ControlModifier and n:
            x = e.position().x(); f = (x + self.offset - MARGIN) / self.ppf
            fit_ppf = (self.width() - 2 * MARGIN) / max(1, self.total)
            self.ppf = min(40.0, max(fit_ppf, self.ppf * 1.25 ** n)); self.fit = self.ppf <= fit_ppf * 1.0001
            self.offset = max(0.0, min(self.content_width() - self.width(), MARGIN + f * self.ppf - x))
            self.zoomed.emit(); self.update()
        elif not self.fit:
            d = e.angleDelta().x() or e.angleDelta().y()
            self.offset = max(0.0, min(self.content_width() - self.width(), self.offset - d))
            self.zoomed.emit(); self.update()

    def keyPressEvent(self, e):
        k, shift = e.key(), bool(e.modifiers() & QtC.Qt.ShiftModifier)
        fps = self.plan.fps if self.plan else 30
        if k in (QtC.Qt.Key_Left, QtC.Qt.Key_Right):
            step = (fps if shift else 1) * (1 if k == QtC.Qt.Key_Right else -1)
            self._move_playhead(self.frame + step)
        elif k == QtC.Qt.Key_Home:
            self._move_playhead(0)
        elif k == QtC.Qt.Key_End:
            self._move_playhead(self.total - 1)
        elif k in (QtC.Qt.Key_Up, QtC.Qt.Key_Down):
            order = sorted(self.parts, key=lambda pid: self.parts[pid]["start"])
            if order:
                here = next((i for i, pid in enumerate(order) if self.parts[pid]["start"] <= self.frame < self.parts[pid]["end"]), 0)
                pid = order[max(0, min(len(order) - 1, here + (1 if k == QtC.Qt.Key_Down else -1)))]
                d = self.parts[pid]
                self._move_playhead((d["main"] or (d["start"],))[0])
                self.selected = pid; self.part_selected.emit(pid)
        else:
            return super().keyPressEvent(e)
        self.released.emit()

    def event(self, e):
        if e.type() == QtC.QEvent.ToolTip:
            pid = self.part_at(e.position() if hasattr(e, "position") else QtC.QPointF(e.pos()))
            part = self.part(pid) if pid else None
            if part:
                d = self.parts[pid]; fps = self.plan.fps
                lines = [f"{d['index'] + 1}. {part_name(part)}", f"{(d['main'][1] if d['main'] else 0) / fps:.1f} s"
                         + (f" (with transitions and pauses {(d['end'] - d['start']) / fps:.1f} s)" if d["end"] - d["start"] != (d["main"] or (0, 0))[1] else "")]
                if part["type"] == "slide" and d["main"]:
                    a = self.plan.frames[d["main"][0]]["k"]; b = self.plan.frames[d["main"][0] + d["main"][1] - 1]["k"]
                    lines.append(f"slices {a + 1} → {b + 1}")
                QtW.QToolTip.showText(e.globalPos(), "\n".join(lines), self)
            else:
                QtW.QToolTip.hideText()
            return True
        return super().event(e)


class Timeline(QtW.QWidget):
    """Timeline strip, a scroll bar when zoomed in, a row of buttons, and the time line of text below."""
    frame_changed = QtC.Signal(int)
    released = QtC.Signal()
    part_selected = QtC.Signal(str)
    part_activated = QtC.Signal(str)

    def __init__(self):
        super().__init__()
        lay = QtW.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(2)
        self.canvas = TimelineCanvas(); lay.addWidget(self.canvas)
        self.scroll = QtW.QScrollBar(QtC.Qt.Horizontal); self.scroll.setVisible(False); lay.addWidget(self.scroll)
        self.buttons = QtW.QHBoxLayout(); lay.addLayout(self.buttons)
        self.fit_btn = QtW.QPushButton("Fit"); self.fit_btn.setToolTip("Show the whole movie (Ctrl + mouse wheel zooms in time)")
        self.fit_btn.clicked.connect(lambda: (self.canvas.fit_all(), self.canvas.update()))
        self.buttons.addStretch(1); self.buttons.addWidget(self.fit_btn)
        self.info = QtW.QLabel(""); self.info.setStyleSheet("color: palette(placeholder-text)"); lay.addWidget(self.info)
        c = self.canvas
        c.frame_changed.connect(self._frame); c.released.connect(self.released)
        c.part_selected.connect(self.part_selected); c.part_activated.connect(self.part_activated)
        c.zoomed.connect(self._sync_scroll)
        self.scroll.valueChanged.connect(self._scrolled)

    def set_plan(self, plan, spec):
        self.canvas.set_plan(plan, spec); self._sync_scroll(); self._text()

    def set_frame(self, i):
        self.canvas.frame = int(min(max(0, self.canvas.total - 1), max(0, i))); self.canvas.ensure_visible(); self.canvas.update(); self._text()

    def set_selected(self, pid):
        self.canvas.selected = pid; self.canvas.update(); self._text()

    @property
    def frame(self):
        return self.canvas.frame

    def _frame(self, f):
        self._text(); self.frame_changed.emit(f)

    def _sync_scroll(self):
        c = self.canvas; extra = c.content_width() - c.width()
        self.scroll.setVisible(not c.fit and extra > 1)
        self.scroll.blockSignals(True); self.scroll.setRange(0, max(0, int(extra))); self.scroll.setPageStep(max(1, c.width()))
        self.scroll.setValue(int(c.offset)); self.scroll.blockSignals(False)

    def _scrolled(self, v):
        self.canvas.offset = float(v); self.canvas.update()

    def _text(self):
        c = self.canvas
        if not c.plan:
            self.info.setText(""); return
        fps = c.plan.fps; f = c.frame; pid = None
        for sg in c.plan.segments:
            if sg["start"] <= f < sg["start"] + sg["count"]:
                pid = sg["part"]; break
        part = c.part(pid) if pid else None
        where = f" · part {c.parts[pid]['index'] + 1}: {part_name(part)}" if part and pid in c.parts else ""
        self.info.setText(f"{f / fps:.1f} s of {c.total / fps:.1f} s{where}")
