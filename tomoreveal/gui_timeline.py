"""The movie timeline under the preview: one block per part, a time ruler and a playhead."""
import math

from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from .storyboard import part_name

TYPE_COLOURS = {"slide": "#4e79a7", "overview": "#59a14f", "focus": "#d1495b", "hold": "#8a8f98", "card": "#9c6ade", "path": "#e0a030"}  # readable in both themes
PLAYHEAD = "#ff9f1c"
RULER_H, ROW_TOP, ROW_H, MARGIN = 18, 22, 36, 8
COMPACT_H = 20      # row height with "Compact" on
LANE_H = 20         # one lane of arrow / text bars under the parts
LANE_GAP = 6        # space between the parts and the first lane
ANN_COLOURS = {"arrow": "#f0a020", "text": "#5b9bf0"}
MIN_BLOCK = 3.0     # never draw a part narrower than this; narrower neighbours merge into one "several parts" mark
ZOOM_STEP = 1.5     # the - and + buttons
SEVERAL = "#9a9a9a"


def ann_label(a):
    """Short name of an arrow or text box on the timeline: its kind and its words."""
    words = ((a.get("label") or {}).get("text", "") if a["kind"] == "arrow" else a.get("text", "")).strip()
    kind = "Arrow" if a["kind"] == "arrow" else "Text"
    return f"{kind}: {' '.join(words.split())}" if words else kind


class TimelineCanvas(QtW.QWidget):
    """The painted strip. Positions are in frames; `ppf` is pixels per frame and `offset` the scrolled pixels."""
    frame_changed = QtC.Signal(int)
    released = QtC.Signal()
    part_selected = QtC.Signal(str)
    part_activated = QtC.Signal(str)
    zoomed = QtC.Signal()
    resizing = QtC.Signal(str, float)      # live, while the right edge is dragged
    resized = QtC.Signal(str, float)       # on release
    moved = QtC.Signal(str, int)           # part id, new position among the parts that are on
    command = QtC.Signal(str)              # delete, duplicate, split, undo, redo (keys)
    context_requested = QtC.Signal(str, QtC.QPoint)
    add_requested = QtC.Signal(QtC.QPoint)
    transition_changed = QtC.Signal(str, str, float)  # part id, "overlap_prev" / "overlap_next", seconds (on release)
    annotation_changed = QtC.Signal(str, str, float, float)  # annotation id, part id, start, end seconds (on release)
    annotation_selected = QtC.Signal(str)

    def __init__(self):
        super().__init__()
        self.row_h = ROW_H
        self.setMinimumHeight(ROW_TOP + ROW_H + 6); self.setFocusPolicy(QtC.Qt.StrongFocus); self.setMouseTracking(True)
        self.setAttribute(QtC.Qt.WA_AcceptTouchEvents, False)
        self.plan = None; self.spec = None; self.ds = None; self.frame = 0; self.selected = None
        self.ppf = 1.0; self.offset = 0.0; self.fit = True; self._drag = None
        self.pending_view = None  # zoom and scroll read from a project, applied when the plan arrives
        self.parts = {}  # id -> dict(index, part, start, end, main=(start, count))

    # ---- data ----
    @property
    def total(self):
        return len(self.plan.frames) if self.plan else 0

    def set_plan(self, plan, spec, ds=None):
        x = self.x_of(self.frame); seen = self.plan is not None and MARGIN <= x <= self.width() - MARGIN
        self.plan, self.spec = plan, spec; self.parts = {}
        if ds is not None:
            self.ds = ds  # for the names of close-ups
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
        if self.pending_view is not None and self.total:
            v, self.pending_view = self.pending_view, None
            self.set_view(v); self._keep_view = True  # the saved scroll wins over "keep the playhead in sight" once
        elif self.fit:
            self.fit_all()
        else:  # after an edit: keep the zoom; a playhead that was in sight stays in sight
            self.offset = self.clamp_offset(self.offset)
            if seen:
                self.ensure_visible()
            self.zoomed.emit()
        self.update_height(); self.update()

    def part(self, pid):
        for p in (self.spec or {}).get("shots", []):
            if p.get("id") == pid:
                return p
        return None

    # ---- geometry ----
    def fit_all(self):
        self.fit = True; self.offset = 0.0
        self.ppf = self.fit_ppf(); self.zoomed.emit(); self.update()

    def fit_ppf(self):
        return max(1e-3, (self.width() - 2 * MARGIN) / max(1, self.total))

    def max_ppf(self):
        """Most zoomed in: one second fills the timeline."""
        fps = self.plan.fps if self.plan else 30
        return max(self.fit_ppf(), (self.width() - 2 * MARGIN) / max(1, fps))

    def clamp_offset(self, off):
        return max(0.0, min(self.content_width() - self.width(), off))

    def zoom_level(self):
        """0 = the whole movie fits, 1 = one second fills the timeline (logarithmic)."""
        a, b = self.fit_ppf(), self.max_ppf()
        return 0.0 if b <= a * 1.0001 else min(1.0, max(0.0, math.log(self.ppf / a) / math.log(b / a)))

    def set_ppf(self, ppf, x=None):
        """Zoom to ppf pixels per frame, keeping the time under x (default: the playhead) where it is."""
        if not self.total:
            return
        a, b = self.fit_ppf(), self.max_ppf()
        ppf = min(b, max(a, ppf))
        if x is None:
            x = min(max(MARGIN, self._playhead_x()), self.width() - MARGIN)
        f = (x + self.offset - MARGIN) / self.ppf
        self.ppf = ppf; self.fit = ppf <= a * 1.0001
        self.offset = 0.0 if self.fit else self.clamp_offset(MARGIN + f * ppf - x)
        self.zoomed.emit(); self.update()

    def zoom_by(self, factor, x=None):
        self.set_ppf(self.ppf * factor, x)

    def set_zoom_level(self, level, x=None):
        a, b = self.fit_ppf(), self.max_ppf()
        self.set_ppf(a * (b / a) ** min(1.0, max(0.0, level)), x)

    def zoom_to_part(self, pid=None):
        """The part (default: the selected one) fills about 80 % of the width."""
        d = self.parts.get(pid or self.selected)
        if not d:
            return False
        n = max(1, d["end"] - d["start"]); W = self.width() - 2 * MARGIN
        self.set_ppf(0.8 * W / n, 0)
        x0 = MARGIN + d["start"] * self.ppf
        self.offset = self.clamp_offset(x0 - MARGIN - 0.1 * W); self.zoomed.emit(); self.update()
        return True

    def visible_frames(self):
        """(first, last) frame position visible, as floats within 0..total."""
        a = (self.offset - MARGIN) / self.ppf; b = (self.offset + self.width() - MARGIN) / self.ppf
        return max(0.0, a), min(float(self.total), b)

    def scroll_to_frame(self, f):
        """Scroll so frame position f is at the left edge (zoom unchanged)."""
        self.offset = self.clamp_offset(MARGIN + f * self.ppf - MARGIN); self.zoomed.emit(); self.update()

    def view_state(self):
        """Zoom and scroll for the project file: seconds shown and the time at the left edge."""
        fps = self.plan.fps if self.plan else 30
        a, b = self.visible_frames()
        return dict(fit=bool(self.fit), seconds=round((b - a) / fps, 3), start=round(a / fps, 3), compact=self.row_h == COMPACT_H)

    def set_view(self, v):
        if not isinstance(v, dict):
            return
        self.set_compact(bool(v.get("compact")))
        if v.get("fit", True) or not self.total:
            self.fit_all(); return
        fps = self.plan.fps if self.plan else 30
        secs = max(1e-3, float(v.get("seconds", 1)))
        a, b = self.fit_ppf(), self.max_ppf()
        self.ppf = min(b, max(a, (self.width() - 2 * MARGIN) / (secs * fps))); self.fit = self.ppf <= a * 1.0001
        self.offset = 0.0 if self.fit else self.clamp_offset(float(v.get("start", 0)) * fps * self.ppf)
        self.zoomed.emit(); self.update()

    def set_compact(self, on):
        self.row_h = COMPACT_H if on else ROW_H
        self.setMinimumHeight(ROW_TOP + self.row_h + 6); self.updateGeometry(); self.update()

    def content_width(self):
        return 2 * MARGIN + self.total * self.ppf

    def x_of(self, f):
        return MARGIN + f * self.ppf - self.offset

    def frame_at(self, x):
        return int(min(max(0, self.total - 1), max(0, math.floor((x + self.offset - MARGIN) / self.ppf))))

    def segment_rects(self):
        """[(segment, QRectF)] of every segment, left to right. Main blocks are at least 3 px wide; transitions and
        pauses keep their real width (at least 1 px), so when zoomed out they become thin lines and never hide the
        blocks next to them."""
        out = []; H = self.row_h
        for sg in (self.plan.segments if self.plan else []):
            x0, x1 = self.x_of(sg["start"]), self.x_of(sg["start"] + sg["count"])
            main = sg["phase"] == "main"
            top = ROW_TOP if main else ROW_TOP + H * 0.2
            out.append((sg, QtC.QRectF(x0, top, max(MIN_BLOCK if main else 1.0, x1 - x0), H if main else H * 0.6)))
        return out

    def groups(self):
        """Parts as drawn: [(list of part ids, x0, x1)] left to right. Neighbouring parts narrower than 3 px are
        merged into one group until it is 3 px wide; a group of several parts is drawn as a grey "several parts" mark."""
        order = sorted(self.parts, key=lambda pid: self.parts[pid]["start"]); out = []; cur = None
        for pid in order:
            d = self.parts[pid]; x0, x1 = self.x_of(d["start"]), self.x_of(d["end"])
            if cur is not None and cur[2] - cur[1] < MIN_BLOCK:
                cur[0].append(pid); cur[2] = x1
                continue
            if cur is not None:
                out.append(cur)
            cur = [[pid], x0, x1]
        if cur is not None:
            out.append(cur)
        return [(ids, x0, max(x1, x0 + MIN_BLOCK)) for ids, x0, x1 in out]

    def ann_bars(self):
        """Bars of the arrows and text boxes: [(annotation id, part id, x0, x1, lane, start s, end s)], in lanes so that
        overlapping items never hide each other."""
        from .annotations import items, shown_in
        if not self.plan or not self.spec:
            return []
        fps = self.plan.fps; bars = []
        for a in items(self.spec):
            sh = a.get("show") or {}
            for pid, d in self.parts.items():
                if not d["main"] or not shown_in(a, pid):
                    continue
                n = d["main"][1] / fps; s0 = float(sh.get("start") or 0.0); s1 = float(sh["end"]) if sh.get("end") is not None else n
                s1 = min(s1, n)
                if s1 <= s0:
                    continue
                bars.append([a["id"], pid, self.x_of(d["main"][0] + s0 * fps), self.x_of(d["main"][0] + s1 * fps), 0, s0, s1, a])
        bars.sort(key=lambda b: b[2]); ends = []
        for b in bars:
            lane = next((i for i, e in enumerate(ends) if e <= b[2] - 2), len(ends))
            if lane == len(ends):
                ends.append(b[3])
            else:
                ends[lane] = b[3]
            b[4] = lane
        return bars

    def lanes_top(self):
        return ROW_TOP + self.row_h + LANE_GAP

    def update_height(self):
        n = max([b[4] for b in self.ann_bars()] + [-1]) + 1
        h = ROW_TOP + self.row_h + 6 + (n * LANE_H + LANE_GAP if n else 0)
        if self.minimumHeight() != h:
            self.setMinimumHeight(h); self.updateGeometry()

    def ann_at(self, pos):
        """(bar, "move" | "start" | "end") under pos."""
        top = self.lanes_top()
        for b in self.ann_bars():
            y0 = top + b[4] * LANE_H
            if y0 <= pos.y() <= y0 + LANE_H - 1 and b[2] - 4 <= pos.x() <= b[3] + 4:
                if abs(pos.x() - b[2]) <= 4:
                    return b, "start"
                if abs(pos.x() - b[3]) <= 4:
                    return b, "end"
                return b, "move"
        return None

    def several_at(self, pos):
        """Ids of the parts merged under pos (a "several parts" mark), or []."""
        if not (ROW_TOP <= pos.y() <= ROW_TOP + self.row_h):
            return []
        for ids, x0, x1 in self.groups():
            if len(ids) > 1 and x0 <= pos.x() <= x1:
                return ids
        return []

    def block_rect(self, pid):
        d = self.parts.get(pid)
        if not d:
            return None
        return QtC.QRectF(self.x_of(d["start"]), ROW_TOP, max(MIN_BLOCK, self.x_of(d["end"]) - self.x_of(d["start"])), self.row_h)

    def part_at(self, pos):
        if not (ROW_TOP <= pos.y() <= ROW_TOP + self.row_h):
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
        else:
            self.ppf = max(self.ppf, self.fit_ppf()); self.offset = self.clamp_offset(self.offset); self.zoomed.emit()

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
        # blocks: parts merged into a "several parts" mark are drawn as one grey block
        merged = {pid for ids, _, _ in self.groups() if len(ids) > 1 for pid in ids}
        for sg, r in self.segment_rects():
            if sg["part"] in merged:
                continue
            col = QtG.QColor(TYPE_COLOURS.get(sg["type"], "#888888"))
            if sg["phase"] == "main":
                p.setPen(QtC.Qt.NoPen); p.setBrush(col); p.drawRoundedRect(r, 3, 3) if r.width() > 6 else p.drawRect(r)
            elif r.width() < 4:  # zoomed out: a thin line
                light = QtG.QColor(col); light.setAlpha(140); p.fillRect(r, light)
            else:  # transitions and pauses: lighter and hatched
                light = QtG.QColor(col); light.setAlpha(90)
                p.setPen(QtC.Qt.NoPen); p.setBrush(light); p.drawRect(r)
                hatch = QtG.QBrush(col, QtC.Qt.BDiagPattern); p.setBrush(hatch); p.drawRect(r)
        for sg, r in self.segment_rects():  # small handles at both ends of a transition block
            if sg.get("transition") and r.width() >= 8:
                col = QtG.QColor(TYPE_COLOURS.get(sg["type"], "#888888")).darker(130)
                p.setPen(QtC.Qt.NoPen); p.setBrush(col)
                for x in (r.left() + 1, r.right() - 3):
                    p.drawRoundedRect(QtC.QRectF(x, r.top() + r.height() * 0.25, 2, r.height() * 0.5), 1, 1)
        d0 = self._drag
        if d0 and d0.get("kind") == "tedge" and d0.get("last") is not None:
            r = [rr for sg, rr in self.segment_rects() if sg is d0["sg"]]
            if r:
                fps = self.plan.fps; dv = (d0["last"] - d0["base"]) * fps * self.ppf
                x = r[0].right() + dv if d0["key"] == "overlap_next" else r[0].left() - dv
                p.setPen(QtG.QPen(pal.color(QtG.QPalette.Highlight), 2, QtC.Qt.DashLine))
                p.drawLine(QtC.QPointF(x, ROW_TOP - 2), QtC.QPointF(x, ROW_TOP + self.row_h + 2))
        for ids, x0, x1 in self.groups():
            if len(ids) > 1:
                r = QtC.QRectF(x0, ROW_TOP, x1 - x0, self.row_h)
                p.setPen(QtC.Qt.NoPen); p.setBrush(QtG.QColor(SEVERAL)); p.drawRect(r)
                p.setBrush(QtG.QBrush(QtG.QColor("#6a6a6a"), QtC.Qt.Dense6Pattern)); p.drawRect(r)
        p.setPen(QtG.QColor("#ffffff")); fm = p.fontMetrics()
        for pid, d in self.parts.items():
            if not d["main"] or pid in merged:
                continue
            x0, x1 = self.x_of(d["main"][0]), self.x_of(d["main"][0] + d["main"][1])
            if x1 - x0 < 14 or x1 < 0 or x0 > self.width():
                continue  # too narrow for any text (the tooltip still names it)
            part = self.part(pid)
            num = str(d["index"] + 1); label = f"{num}  {part_name(part, self.spec, self.ds) if part else ''}"
            r = QtC.QRectF(x0 + 3, ROW_TOP, max(0.0, x1 - x0 - 6), self.row_h)
            text = num if fm.horizontalAdvance(num + "  ") + fm.horizontalAdvance("Abc…") > r.width() else fm.elidedText(label, QtC.Qt.ElideRight, int(r.width()))
            if fm.horizontalAdvance(text) <= r.width() + 1:
                p.drawText(r, QtC.Qt.AlignVCenter | QtC.Qt.AlignLeft, text)
        if self.selected in self.parts:
            pen = QtG.QPen(pal.color(QtG.QPalette.Highlight), 2.5); p.setPen(pen); p.setBrush(QtC.Qt.NoBrush)
            p.drawRoundedRect(self.block_rect(self.selected).adjusted(-1, -1, 1, 1), 4, 4)
        d = self._drag
        if d and d.get("kind") == "block" and d.get("moving") and d.get("target") is not None:
            order = sorted(self.parts, key=lambda pid: self.parts[pid]["start"]); t = d["target"]
            xi = self.x_of(self.parts[order[t]]["start"]) if t < len(order) else self.x_of(self.total)
            p.setPen(QtG.QPen(pal.color(QtG.QPalette.Highlight), 3)); p.drawLine(QtC.QPointF(xi, ROW_TOP - 3), QtC.QPointF(xi, ROW_TOP + self.row_h + 3))
        # arrows and text boxes: one row each under the parts, in a lightly shaded band
        bars = self.ann_bars(); top = self.lanes_top(); sel_ann = getattr(self, "selected_ann", None)
        if bars:
            n = max(b[4] for b in bars) + 1
            band = QtG.QColor(pal.color(QtG.QPalette.AlternateBase)); p.fillRect(QtC.QRectF(0, top - 3, self.width(), n * LANE_H + 4), band)
            p.setPen(dim); p.drawLine(QtC.QPointF(0, top - 3), QtC.QPointF(self.width(), top - 3))
        f2 = p.font(); f2.setPointSizeF(max(7.0, f2.pointSizeF() * 0.95)); p.setFont(f2); fm2 = p.fontMetrics()
        for aid, pid, x0, x1, lane, s0, s1, a in bars:
            r = QtC.QRectF(x0, top + lane * LANE_H + 1, max(4.0, x1 - x0), LANE_H - 3)
            col = QtG.QColor(ANN_COLOURS["arrow" if a["kind"] == "arrow" else "text"]); col.setAlpha(230 if a.get("visible", True) else 80)
            p.setPen(QtG.QPen(pal.color(QtG.QPalette.Highlight), 2) if aid == sel_ann else QtG.QPen(col.darker(140), 1)); p.setBrush(col)
            p.drawRoundedRect(r, 4, 4)
            if r.width() >= 10:  # grips at both ends: drag to change when it shows
                p.setPen(QtC.Qt.NoPen); p.setBrush(QtG.QColor(0, 0, 0, 70))
                for gx in (r.left() + 2, r.right() - 4):
                    p.drawRoundedRect(QtC.QRectF(gx, r.top() + 4, 2, r.height() - 8), 1, 1)
            lab = ann_label(a)
            if r.width() > 22:
                p.setPen(QtG.QColor("#111111"))
                p.drawText(r.adjusted(7, 0, -6, 0), QtC.Qt.AlignVCenter | QtC.Qt.AlignLeft, fm2.elidedText(lab, QtC.Qt.ElideRight, int(r.width() - 13)))
        # playhead with a handle on the ruler; the handle carries a + (add a part here)
        x = self._playhead_x()
        p.setPen(QtG.QPen(QtG.QColor(PLAYHEAD), 2)); p.drawLine(QtC.QPointF(x, 2), QtC.QPointF(x, self.height()))
        p.setBrush(QtG.QColor(PLAYHEAD)); p.setPen(QtC.Qt.NoPen)
        p.drawRoundedRect(QtC.QRectF(x - 7, 0, 14, 11), 3, 3)
        p.setPen(QtG.QPen(QtG.QColor("#000000"), 1.6))
        p.drawLine(QtC.QPointF(x - 3.5, 5.5), QtC.QPointF(x + 3.5, 5.5)); p.drawLine(QtC.QPointF(x, 2), QtC.QPointF(x, 9))

    # ---- mouse and keys ----
    def _move_playhead(self, f, emit=True):
        f = int(min(max(0, self.total - 1), max(0, f)))
        if f != self.frame or emit:
            self.frame = f; self.ensure_visible(); self.update()
            if emit:
                self.frame_changed.emit(f)

    def ensure_visible(self):
        if getattr(self, "_keep_view", False):
            self._keep_view = False; return
        if self.fit or not self.total:
            return
        x = self.x_of(self.frame)
        if x < MARGIN or x > self.width() - MARGIN:
            self.offset = max(0.0, min(self.content_width() - self.width(), self.offset + x - self.width() / 2)); self.zoomed.emit()

    # editing gestures: drag a block's right edge (length), drag a block sideways (order)
    EDGE, MOVE_START = 5, 6

    def edge_at(self, pos):
        """Id of the part whose main block has its right edge within 5 px of pos."""
        if not (ROW_TOP <= pos.y() <= ROW_TOP + self.row_h):
            return None
        for pid, d in self.parts.items():
            if d["main"] and abs(self.x_of(d["main"][0] + d["main"][1]) - pos.x()) <= self.EDGE:
                return pid
        return None

    def transition_edge_at(self, pos):
        """(part id, "overlap_prev" | "overlap_next", segment) when pos is on the left or right end of a transition."""
        if not (ROW_TOP <= pos.y() <= ROW_TOP + self.row_h):
            return None
        for sg, r in self.segment_rects():
            if sg.get("transition") and r.width() >= 8:
                if abs(r.left() - pos.x()) <= self.EDGE:
                    return sg["part"], "overlap_prev", sg
                if abs(r.right() - pos.x()) <= self.EDGE:
                    return sg["part"], "overlap_next", sg
        return None

    def on_handle(self, pos):
        """The + handle on the playhead (top of the ruler)."""
        return pos.y() <= 11 and abs(pos.x() - self._playhead_x()) <= 8

    def _playhead_x(self):
        return self.x_of(self.frame + 0.5) if self.ppf > 2 else self.x_of(self.frame)

    def drop_index(self, x):
        """Where a dragged block would land: the position among the parts that are on, 0 = first."""
        order = sorted(self.parts, key=lambda pid: self.parts[pid]["start"])
        for i, pid in enumerate(order):
            d = self.parts[pid]
            if x < (self.x_of(d["start"]) + self.x_of(d["end"])) / 2:
                return i
        return len(order)

    def mousePressEvent(self, e):
        if not self.total:
            return
        self.setFocus(); pos = e.position()
        if e.button() == QtC.Qt.MiddleButton:  # drag to scroll
            self._drag = dict(kind="pan", x0=pos.x(), off=self.offset); self.setCursor(QtC.Qt.ClosedHandCursor); return
        if e.button() == QtC.Qt.RightButton:
            pid = self.part_at(pos)
            if pid is not None:
                self.selected = pid; self.part_selected.emit(pid); self.update()
            self.context_requested.emit(pid or "", e.globalPosition().toPoint())
            return
        if e.button() != QtC.Qt.LeftButton:
            return
        if self.on_handle(pos):
            self.add_requested.emit(e.globalPosition().toPoint()); return
        ab = self.ann_at(pos)
        if ab is not None:
            b, how = ab; self.selected_ann = b[0]; self.annotation_selected.emit(b[0])
            self._drag = dict(kind="ann", bar=b, how=how, x0=pos.x(), s0=b[5], s1=b[6], new=(b[5], b[6])); self.update(); return
        te = self.transition_edge_at(pos)
        if te is not None:
            pid, key, sg = te; part = self.part(pid) or {}
            from .storyboard import transition_of
            self._drag = dict(kind="tedge", pid=pid, key=key, x0=pos.x(), base=float(transition_of(part)[key]), last=None, sg=sg)
            self.selected = pid; self.part_selected.emit(pid); self.update(); return
        edge = self.edge_at(pos)
        if edge is not None:
            part = self.part(edge)
            self._drag = dict(kind="resize", pid=edge, x0=pos.x(), secs=float(part.get("seconds", 5)), last=None)
            self.selected = edge; self.part_selected.emit(edge); self.update(); return
        pid = self.part_at(pos)
        if pid is not None:
            self.selected = pid; self.part_selected.emit(pid)
            self._drag = dict(kind="block", pid=pid, x0=pos.x(), moving=False, target=None)
        else:
            self._drag = dict(kind="playhead")
        self._move_playhead(self.frame_at(pos.x()))

    def mouseMoveEvent(self, e):
        pos = e.position(); d = self._drag
        if d is None:
            self.setCursor(QtC.Qt.SizeHorCursor if (self.edge_at(pos) or self.transition_edge_at(pos)) else
                           QtC.Qt.PointingHandCursor if self.on_handle(pos) else QtC.Qt.ArrowCursor)
            return
        if d["kind"] == "pan":
            self.offset = self.clamp_offset(d["off"] - (pos.x() - d["x0"])); self.zoomed.emit(); self.update()
        elif d["kind"] == "playhead":
            self._move_playhead(self.frame_at(pos.x()))
        elif d["kind"] == "ann":
            fps = self.plan.fps if self.plan else 30; dt = (pos.x() - d["x0"]) / (self.ppf * fps)
            step = 0.5 if e.modifiers() & QtC.Qt.ShiftModifier else 0.1
            s0, s1 = d["s0"], d["s1"]
            if d["how"] == "start":
                s0 = min(s1 - 0.1, max(0.0, s0 + dt))
            elif d["how"] == "end":
                s1 = max(s0 + 0.1, s1 + dt)
            else:
                s0, s1 = max(0.0, s0 + dt), max(0.0, s0 + dt) + (s1 - s0)
            d["new"] = (round(round(s0 / step) * step, 2), round(round(s1 / step) * step, 2)); self.update()
        elif d["kind"] == "tedge":
            step = 0.5 if e.modifiers() & QtC.Qt.ShiftModifier else 0.1
            dt = (pos.x() - d["x0"]) / (self.ppf * (self.plan.fps if self.plan else 30))
            v = d["base"] + (dt if d["key"] == "overlap_next" else -dt)  # right end to the right / left end to the left: more overlap
            v = min(10.0, max(0.0, round(round(v / step) * step, 2)))
            if v != d["last"]:
                d["last"] = v; self.update()
        elif d["kind"] == "resize":
            step = 0.5 if e.modifiers() & QtC.Qt.ShiftModifier else 0.1
            secs = d["secs"] + (pos.x() - d["x0"]) / (self.ppf * (self.plan.fps if self.plan else 30))
            secs = min(120.0, max(0.2, round(round(secs / step) * step, 2)))
            if secs != d["last"]:
                d["last"] = secs; self.resizing.emit(d["pid"], secs)  # only the timeline redraws while dragging
        elif d["kind"] == "block":
            if not d["moving"] and abs(pos.x() - d["x0"]) >= self.MOVE_START:
                d["moving"] = True; self.setCursor(QtC.Qt.ClosedHandCursor)
            if d["moving"]:
                d["target"] = self.drop_index(pos.x()); self.update()
            else:
                self._move_playhead(self.frame_at(pos.x()))

    def mouseReleaseEvent(self, e):
        d, self._drag = self._drag, None
        self.setCursor(QtC.Qt.ArrowCursor)
        if d is None:
            return
        if d["kind"] == "pan":
            return
        if d["kind"] == "ann":
            if d["new"] != (d["s0"], d["s1"]):
                self.annotation_changed.emit(d["bar"][0], d["bar"][1], float(d["new"][0]), float(d["new"][1]))
            self.released.emit(); return
        if d["kind"] == "tedge":
            if d["last"] is not None and d["last"] != d["base"]:
                self.transition_changed.emit(d["pid"], d["key"], float(d["last"]))
            self.released.emit(); return
        if d["kind"] == "resize":
            if d["last"] is not None:
                self.resized.emit(d["pid"], d["last"])
        elif d["kind"] == "block" and d["moving"] and d["target"] is not None:
            self.update(); self.moved.emit(d["pid"], d["target"])
        self.released.emit()

    def mouseDoubleClickEvent(self, e):
        pid = self.part_at(e.position())
        if pid is not None:
            self.selected = pid; self.part_selected.emit(pid); self.part_activated.emit(pid)

    def wheelEvent(self, e):
        """Ctrl + wheel zooms around the mouse; the wheel, Shift + wheel and a two-finger swipe scroll."""
        ad, pd = e.angleDelta(), e.pixelDelta()
        if e.modifiers() & QtC.Qt.ControlModifier:
            n = (ad.y() or ad.x()) / 120.0
            if n:
                self.zoom_by(1.25 ** n, e.position().x())
            return
        if pd.x() or pd.y():  # touchpad: pixel steps
            d = pd.x() if abs(pd.x()) >= abs(pd.y()) else pd.y()
        else:
            d = (ad.x() if abs(ad.x()) >= abs(ad.y()) else ad.y()) * 0.5
        if d and not self.fit:
            self.offset = self.clamp_offset(self.offset - d)
            self.zoomed.emit(); self.update()

    def event(self, e):
        if e.type() == QtC.QEvent.NativeGesture and e.gestureType() == QtC.Qt.ZoomNativeGesture:  # touchpad pinch
            self.zoom_by(math.exp(e.value()), e.position().x()); return True
        if e.type() == QtC.QEvent.ToolTip:
            return self._tooltip(e)
        return super().event(e)

    def keyPressEvent(self, e):
        k, shift = e.key(), bool(e.modifiers() & QtC.Qt.ShiftModifier)
        ctrl0 = bool(e.modifiers() & QtC.Qt.ControlModifier)
        fps = self.plan.fps if self.plan else 30
        if ctrl0 and k in (QtC.Qt.Key_Minus, QtC.Qt.Key_Underscore):
            self.zoom_by(1 / ZOOM_STEP); return
        if ctrl0 and k in (QtC.Qt.Key_Plus, QtC.Qt.Key_Equal):
            self.zoom_by(ZOOM_STEP); return
        if (ctrl0 and k == QtC.Qt.Key_0) or (shift and not ctrl0 and k == QtC.Qt.Key_Z):
            self.fit_all(); return
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
        elif k == QtC.Qt.Key_Escape and self._drag and self._drag.get("kind") == "block":
            self._drag = None; self.setCursor(QtC.Qt.ArrowCursor); self.update(); return  # cancel the move
        else:
            ctrl = bool(e.modifiers() & QtC.Qt.ControlModifier)
            cmd = {(QtC.Qt.Key_Delete, False): "delete", (QtC.Qt.Key_Backspace, False): "delete", (QtC.Qt.Key_D, True): "duplicate",
                   (QtC.Qt.Key_S, False): "split", (QtC.Qt.Key_Z, True): "redo" if shift else "undo", (QtC.Qt.Key_Space, False): "play",
                   (QtC.Qt.Key_Y, True): "redo"}.get((k, ctrl))
            if cmd:
                self.command.emit(cmd); return
            return super().keyPressEvent(e)
        self.released.emit()

    def _tooltip(self, e):
        if True:
            pos = e.position() if hasattr(e, "position") else QtC.QPointF(e.pos())
            several = self.several_at(pos)
            if several:
                names = [f"{self.parts[q]['index'] + 1}. {part_name(self.part(q), self.spec, self.ds)}" for q in several if self.part(q)]
                QtW.QToolTip.showText(e.globalPos(), "Several parts (zoom in to see them):\n" + "\n".join(names), self)
                return True
            ab = self.ann_at(pos)
            if ab:
                b = ab[0]
                QtW.QToolTip.showText(e.globalPos(), f"{ann_label(b[7])}\nshown {b[5]:.1f} s to {b[6]:.1f} s into the part\n"
                                      "Drag it to move it, drag an end to change when it shows", self)
                return True
            pid = self.part_at(e.position() if hasattr(e, "position") else QtC.QPointF(e.pos()))
            part = self.part(pid) if pid else None
            if part:
                d = self.parts[pid]; fps = self.plan.fps
                lines = [f"{d['index'] + 1}. {part_name(part, self.spec, self.ds)}", f"{(d['main'][1] if d['main'] else 0) / fps:.1f} s"
                         + (f" (with transitions and pauses {(d['end'] - d['start']) / fps:.1f} s)" if d["end"] - d["start"] != (d["main"] or (0, 0))[1] else "")]
                if part["type"] == "slide" and d["main"]:
                    a = self.plan.frames[d["main"][0]]["k"]; b = self.plan.frames[d["main"][0] + d["main"][1] - 1]["k"]
                    lines.append(f"slices {a + 1} → {b + 1}")
                QtW.QToolTip.showText(e.globalPos(), "\n".join(lines), self)
            else:
                QtW.QToolTip.hideText()
            return True


class OverviewStrip(QtW.QWidget):
    """A thin strip over the timeline showing the whole movie in miniature, with a light rectangle for the part that
    is visible below. Drag the rectangle to scroll, drag its edges to zoom, click elsewhere to jump there."""
    HEIGHT, EDGE = 12, 4

    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas; self.setFixedHeight(self.HEIGHT); self.setMouseTracking(True); self._drag = None
        self.setToolTip("The whole movie. The light box is the part shown below: drag it to scroll, drag its edges to zoom, "
                        "click elsewhere to jump there.")

    def x_of(self, f):
        return MARGIN + f / max(1, self.canvas.total) * (self.width() - 2 * MARGIN)

    def frame_of(self, x):
        return (x - MARGIN) / max(1.0, self.width() - 2 * MARGIN) * max(1, self.canvas.total)

    def view_rect(self):
        a, b = self.canvas.visible_frames()
        return QtC.QRectF(self.x_of(a), 0, max(4.0, self.x_of(b) - self.x_of(a)), self.HEIGHT)

    def paintEvent(self, e):
        c = self.canvas
        if not c.plan:
            return
        p = QtG.QPainter(self); pal = self.palette()
        p.fillRect(self.rect(), pal.color(QtG.QPalette.Base))
        for pid, d in c.parts.items():
            part = c.part(pid)
            col = QtG.QColor(TYPE_COLOURS.get(part.get("type") if part else "", "#888888"))
            x0, x1 = self.x_of(d["start"]), self.x_of(d["end"])
            p.fillRect(QtC.QRectF(x0, 3, max(1.0, x1 - x0 - 0.5), self.HEIGHT - 6), col)
        r = self.view_rect()
        hl = QtG.QColor(pal.color(QtG.QPalette.Text)); hl.setAlpha(60)
        p.setBrush(hl); p.setPen(QtG.QPen(pal.color(QtG.QPalette.Text), 1)); p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))

    def mousePressEvent(self, e):
        c = self.canvas; x = e.position().x(); r = self.view_rect()
        if abs(x - r.left()) <= self.EDGE:
            self._drag = dict(kind="left", fixed=c.visible_frames()[1])
        elif abs(x - r.right()) <= self.EDGE:
            self._drag = dict(kind="right", fixed=c.visible_frames()[0])
        elif r.left() <= x <= r.right():
            self._drag = dict(kind="move", x0=x, f0=c.visible_frames()[0])
        else:  # jump: centre the view there
            a, b = c.visible_frames(); c.scroll_to_frame(self.frame_of(x) - (b - a) / 2)
            self._drag = dict(kind="move", x0=x, f0=c.visible_frames()[0])

    def mouseMoveEvent(self, e):
        c = self.canvas; x = e.position().x(); d = self._drag
        if d is None:
            r = self.view_rect()
            self.setCursor(QtC.Qt.SizeHorCursor if min(abs(x - r.left()), abs(x - r.right())) <= self.EDGE else QtC.Qt.OpenHandCursor)
            return
        if d["kind"] == "move":
            c.scroll_to_frame(d["f0"] + self.frame_of(x) - self.frame_of(d["x0"]))
        else:
            f = min(max(0.0, self.frame_of(x)), float(c.total))
            a, b = (f, d["fixed"]) if d["kind"] == "left" else (d["fixed"], f)
            if b - a >= 1:
                W = c.width() - 2 * MARGIN
                c.ppf = min(c.max_ppf(), max(c.fit_ppf(), W / (b - a))); c.fit = c.ppf <= c.fit_ppf() * 1.0001
                c.scroll_to_frame(a)

    def mouseReleaseEvent(self, e):
        self._drag = None


class Timeline(QtW.QWidget):
    """Timeline strip, a scroll bar when zoomed in, a row of buttons, and the time line of text below."""
    frame_changed = QtC.Signal(int)
    released = QtC.Signal()
    part_selected = QtC.Signal(str)
    part_activated = QtC.Signal(str)

    def __init__(self):
        super().__init__()
        lay = QtW.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(2)
        self.canvas = TimelineCanvas()
        self.strip = OverviewStrip(self.canvas); self.strip.setVisible(False); lay.addWidget(self.strip)
        lay.addWidget(self.canvas)
        self.scroll = QtW.QScrollBar(QtC.Qt.Horizontal); self.scroll.setVisible(False); lay.addWidget(self.scroll)
        self.buttons = QtW.QHBoxLayout(); lay.addLayout(self.buttons)
        # the zoom bar: [-] ----o---- [+]  [Fit all]  [Zoom to part]  [Compact]
        zb = QtW.QWidget(); zl = QtW.QHBoxLayout(zb); zl.setContentsMargins(0, 0, 0, 0); zl.setSpacing(3); self.zoom_bar = zb

        def tb(text, tip, fn):
            b = QtW.QToolButton(); b.setText(text); b.setToolTip(tip); b.clicked.connect(fn); b.setAutoRaise(False); zl.addWidget(b)
            return b
        self.zoom_out_btn = tb("−", "Zoom out (Ctrl + minus, or Ctrl + mouse wheel)", lambda: self.canvas.zoom_by(1 / ZOOM_STEP))
        self.zoom_slider = QtW.QSlider(QtC.Qt.Horizontal); self.zoom_slider.setRange(0, 1000); self.zoom_slider.setFixedWidth(110)
        self.zoom_slider.setToolTip("Zoom in time: left shows the whole movie, right shows about one second.")
        self.zoom_slider.valueChanged.connect(lambda v: self.canvas.set_zoom_level(v / 1000.0) if not self._syncing else None)
        zl.addWidget(self.zoom_slider)
        self.zoom_in_btn = tb("+", "Zoom in (Ctrl + plus, or Ctrl + mouse wheel)", lambda: self.canvas.zoom_by(ZOOM_STEP))
        self.fit_btn = QtW.QPushButton("Fit all"); self.fit_btn.setToolTip("Show the whole movie (Ctrl + 0 or Shift + Z)")
        self.fit_btn.clicked.connect(self.canvas.fit_all); zl.addWidget(self.fit_btn)
        self.part_btn = QtW.QPushButton("Zoom to part"); self.part_btn.setToolTip("Zoom in on the selected part.")
        self.part_btn.clicked.connect(lambda: self.canvas.zoom_to_part()); zl.addWidget(self.part_btn)
        self.compact_btn = QtW.QToolButton(); self.compact_btn.setText("Compact"); self.compact_btn.setCheckable(True)
        self.compact_btn.setToolTip("Lower blocks, for long movies."); self.compact_btn.toggled.connect(self.canvas.set_compact); zl.addWidget(self.compact_btn)
        self._syncing = False
        self.buttons.addStretch(1); self.buttons.addWidget(zb)
        self.info = QtW.QLabel(""); self.info.setStyleSheet("color: palette(placeholder-text)"); lay.addWidget(self.info)
        c = self.canvas
        c.frame_changed.connect(self._frame); c.released.connect(self.released)
        c.part_selected.connect(self.part_selected); c.part_activated.connect(self.part_activated)
        c.zoomed.connect(self._sync_scroll); c.part_selected.connect(lambda pid: self.part_btn.setEnabled(True))
        for name in ("resizing", "resized", "moved", "command", "context_requested", "add_requested", "transition_changed",
                     "annotation_changed", "annotation_selected"):
            setattr(self, name, getattr(c, name))
        self.scroll.valueChanged.connect(self._scrolled)

    def set_plan(self, plan, spec, ds=None):
        self.canvas.set_plan(plan, spec, ds); self._sync_scroll(); self._text()

    def set_frame(self, i):
        f = int(min(max(0, self.canvas.total - 1), max(0, i))); moved = f != self.canvas.frame
        self.canvas.frame = f
        if moved:  # a playhead that did not move does not scroll the timeline
            self.canvas.ensure_visible()
        self.canvas.update(); self._text()

    def set_selected(self, pid):
        self.canvas.selected = pid; self.canvas.update(); self._text(); self.part_btn.setEnabled(pid in self.canvas.parts)

    @property
    def frame(self):
        return self.canvas.frame

    def _frame(self, f):
        self._text(); self.frame_changed.emit(f)

    def _sync_scroll(self):
        c = self.canvas; extra = c.content_width() - c.width()
        self._syncing = True
        self.zoom_slider.setValue(round(c.zoom_level() * 1000)); self._syncing = False
        self.strip.setVisible(not c.fit and extra > 1); self.strip.update()
        self.part_btn.setEnabled(c.selected in c.parts)
        if self.compact_btn.isChecked() != (c.row_h == COMPACT_H):
            self.compact_btn.blockSignals(True); self.compact_btn.setChecked(c.row_h == COMPACT_H); self.compact_btn.blockSignals(False)
        self.scroll.setVisible(not c.fit and extra > 1)
        self.scroll.blockSignals(True); self.scroll.setRange(0, max(0, int(extra))); self.scroll.setPageStep(max(1, c.width()))
        self.scroll.setValue(int(c.offset)); self.scroll.blockSignals(False)

    def _scrolled(self, v):
        self.canvas.offset = float(v); self.canvas.update(); self.strip.update()

    def _text(self):
        c = self.canvas
        if not c.plan:
            self.info.setText(""); return
        fps = c.plan.fps; f = c.frame; pid = None
        for sg in c.plan.segments:
            if sg["start"] <= f < sg["start"] + sg["count"]:
                pid = sg["part"]; break
        part = c.part(pid) if pid else None
        where = f" · part {c.parts[pid]['index'] + 1}: {part_name(part, c.spec, c.ds)}" if part and pid in c.parts else ""
        self.info.setText(f"{f / fps:.1f} s of {c.total / fps:.1f} s{where}")
