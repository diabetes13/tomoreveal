"""Arrows and text boxes in the window (plan 3.5 phase 6): the "Arrows and text" list in the Look tab, adding them
by clicking in the preview, dragging, right-click menus, several selected at once (align), and editing labels."""
import math

from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from . import annotations as an
from .gui_fold import Section
from .gui_theme import DIM

COLOURS = (("automatic (white or black)", "auto"), ("white", "#ffffff"), ("black", "#000000"), ("yellow", "#ffd23f"),
           ("red", "#e4572e"), ("cyan", "#45b9e8"))


def exec_menu(menu, pos):
    """Show a menu (tests replace this so nothing waits for a click)."""
    menu.exec(pos)


# ---------------------------------------------------------------------------------------------------------------
# preview: hit names "ann:<id>" (the whole item), "anntip:<id>" (an arrow's tip), "anntail:<id>" (tail or label)
# ---------------------------------------------------------------------------------------------------------------
def _split(name):
    """"anntip:<id>:<j>" -> (id, j); "ann:<id>" -> (id, None)."""
    parts = name.split(":")
    return parts[1], (int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None)


class AnnotationMouse:
    def __init__(self, win):
        self.win = win; self.drag = None; self.selected = []  # ids selected in the preview (Ctrl + click adds)

    def hit(self, od, f):
        if f is None:
            return None
        rects = od.rects()
        for pre in ("anntip:", "anntail:", "ann:"):
            for n in reversed([n for n in rects if n.startswith(pre)]):  # drawn last = on top
                x0, y0, x1, y1 = rects[n][0]
                if x0 <= f[0] <= x1 and y0 <= f[1] <= y1:
                    return n
        return None

    def press(self, od, f, mods):
        w = self.win
        name = self.hit(od, f)
        if name is None:
            return False
        aid, j = _split(name); a = an.find(w.spec, aid)
        if a is None:
            return False
        if mods & QtC.Qt.ControlModifier:  # several at once
            self.selected = [x for x in self.selected if x != aid] if aid in self.selected else self.selected + [aid]
            w.prev.update(); return True
        if aid not in self.selected:
            self.selected = [aid]
        kind = "tip" if name.startswith("anntip:") else "tail" if name.startswith("anntail:") and a["kind"] == "arrow" else "move"
        self.drag = dict(aid=aid, kind=kind, f0=f, moved=False, more=j,
                         start={x: (dict(an.find(w.spec, x).get("pos") or {}) if an.find(w.spec, x) else {}) for x in self.selected})
        return True

    def move(self, od, f, mods):
        d = self.drag; w = self.win
        if d is None or f is None:
            return
        a = an.find(w.spec, d["aid"])
        if a is None:
            return
        d["moved"] = True; ren = w.st.ren
        if d["kind"] == "tail":  # angle and length of the tail, on screen
            (tx, ty), _ = ren.project(a["anchor"]["xyz"]); W, H = ren.size
            dx, dy = (f[0] - tx) * W, (f[1] - ty) * H
            a["tail"]["angle"] = round(math.degrees(math.atan2(-dy, dx)) % 360, 1)
            a["tail"]["length"] = round(max(0.02, min(0.6, math.hypot(dx, dy) / H)), 4)
        elif d["kind"] == "tip":  # point the tip at another spot
            p = ren.pick_point(*f)
            if p is not None:
                hit = None
                try:
                    hit = w.st.pick(w.spec_for(), {"frame": w.frame, "x": f[0], "y": f[1]})
                except Exception:
                    pass
                anc = dict(xyz=[float(v) for v in p], structure=hit.get("class") if hit and hit.get("found") else None,
                           component=hit.get("component") if hit and hit.get("found") else None)
                if d.get("more") is not None and d["more"] < len(a.get("more") or []):  # one of its extra pointers
                    a["more"][d["more"]] = anc
                else:
                    a["anchor" if a["kind"] == "arrow" else "pin"] = anc
        else:  # move text boxes (all selected ones together); a pinned box comes loose
            for aid in self.selected:
                b = an.find(w.spec, aid)
                if b is None or b["kind"] != "text":
                    continue
                st = d["start"].get(aid) or {}
                if b.get("pin"):
                    box = od.rects().get(f"ann:{aid}")
                    b["pin"] = None
                    if box:
                        st = dict(x=box[0][0], y=box[0][1], anchor="top-left"); d["start"][aid] = st
                b["pos"] = dict(x=min(0.98, max(0.0, float(st.get("x", 0.5)) + f[0] - d["f0"][0])),
                                y=min(0.98, max(0.0, float(st.get("y", 0.5)) + f[1] - d["f0"][1])), anchor=st.get("anchor", "top-left"))
        w.req = {"frame": w.frame}; w.do_preview(quick=True); w.prev.update()

    def release(self, od):
        d, self.drag = self.drag, None
        if d is not None and d["moved"]:
            self.win.editor.commit(); self.win.schedule(0)
            if hasattr(self.win, "ann_panel"):
                self.win.ann_panel.rebuild()

    # ---- menus ----
    def context(self, od, f, gpos):
        name = self.hit(od, f)
        if name is None:
            return False
        aid, j = _split(name)
        if aid not in self.selected:
            self.selected = [aid]
        item_menu(self.win, aid, gpos, j)
        return True

    def double(self, od, f):
        name = self.hit(od, f)
        if name is None:
            return False
        edit_label(self.win, _split(name)[0]); return True


def item_menu(win, aid, gpos, pointer=None):
    a = an.find(win.spec, aid)
    if a is None:
        return
    m = QtW.QMenu(win); arrow = a["kind"] == "arrow"
    m.addAction("Edit label..." if arrow else "Edit text...", lambda: edit_label(win, aid))
    m.addAction("Point at more things...", lambda: start_add_pointer(win, aid))
    if pointer is not None:
        m.addAction("Remove this pointer", lambda: change(win, aid, lambda b: b["more"].pop(pointer) if pointer < len(b.get("more") or []) else None))
    if an.pointers(a):
        m.addAction("Remove the extra pointers", lambda: change(win, aid, lambda b: b.pop("more", None)))
    st = a.setdefault("style", {})
    if arrow:
        sh = m.addMenu("Shape")
        for t, v in an.SHAPES:
            act = sh.addAction(t, lambda v=v: change(win, aid, lambda b: b["style"].__setitem__("shape", v))); act.setCheckable(True)
            act.setChecked(st.get("shape", "arrow") == v)
    else:
        bx = m.addMenu("Box")
        for t, v in an.BOXES:
            act = bx.addAction(t, lambda v=v: change(win, aid, lambda b: b["style"].__setitem__("box", v))); act.setCheckable(True)
            act.setChecked(st.get("box", "dark") == v)
    co = m.addMenu("Colour")
    for t, v in COLOURS:
        act = co.addAction(t, lambda v=v: change(win, aid, lambda b: b["style"].__setitem__("color", v))); act.setCheckable(True)
        act.setChecked(st.get("color", "auto") == v)
    m.addAction("Duplicate", lambda: (an.duplicate(win.spec, aid), commit(win)))
    cp = m.addMenu("Copy to")
    for i, p in enumerate(win.spec.get("shots", [])):
        if p.get("type") == "card":
            continue
        from .storyboard import part_name
        cp.addAction(f"{i + 1}. {part_name(p, win.spec, win.st.ds)}", lambda pid=p["id"]: copy_to(win, aid, pid))
    sel = [x for x in win.ann_mouse.selected if an.find(win.spec, x) and an.find(win.spec, x)["kind"] == "text"]
    if len(sel) >= 2:
        al = m.addMenu("Align")
        for t, how in (("left", "left"), ("centre", "center"), ("right", "right"), ("top", "top"), ("middle", "middle"),
                       ("bottom", "bottom"), ("distribute evenly (down)", "distribute")):
            al.addAction(t, lambda how=how: align(win, sel, how))
    m.addSeparator()
    m.addAction("Make all arrows look like this one" if arrow else "Make all text look like this one", lambda: same_style(win, aid))
    m.addAction("Delete", lambda: delete(win, aid))
    exec_menu(m, gpos)


def commit(win):
    win.editor.commit(); win.schedule(0)
    if hasattr(win, "ann_panel"):
        win.ann_panel.rebuild()


def change(win, aid, fn):
    a = an.find(win.spec, aid)
    if a is not None:
        fn(a); commit(win)


def delete(win, aid):
    win.spec["annotations"] = [a for a in an.items(win.spec) if a["id"] != aid]
    win.ann_mouse.selected = [x for x in win.ann_mouse.selected if x != aid]; commit(win)


def copy_to(win, aid, pid):
    b = an.duplicate(win.spec, aid)
    if b is not None:
        b["show"]["parts"] = [pid]
        if b["kind"] == "text":
            b["pos"] = dict(an.find(win.spec, aid).get("pos") or b["pos"])  # same place on the screen
        else:
            b["tail"] = dict(an.find(win.spec, aid)["tail"])  # same 3D anchor and tail
        commit(win)


def same_style(win, aid):
    a = an.find(win.spec, aid)
    for b in an.items(win.spec):
        if b is not a and b["kind"] == a["kind"]:
            b["style"] = dict(a.get("style") or {})
            if a["kind"] == "arrow":
                b.setdefault("label", {})["size"] = (a.get("label") or {}).get("size", 1.0)
    commit(win)


def align(win, ids, how):
    """Line up text boxes by their drawn boxes; one undo step."""
    od = win.prev.overlay; rects = od.rects() if od is not None else {}
    boxes = {aid: rects[f"ann:{aid}"][0] for aid in ids if f"ann:{aid}" in rects}
    if len(boxes) < 2:
        return
    if how == "distribute":
        order = sorted(boxes, key=lambda k: boxes[k][1]); top, bot = boxes[order[0]][1], boxes[order[-1]][1]
        for j, aid in enumerate(order):
            set_box(win, aid, boxes[aid], y=top + (bot - top) * j / (len(order) - 1))
    else:
        ref = {"left": min(b[0] for b in boxes.values()), "right": max(b[2] for b in boxes.values()),
               "center": sum((b[0] + b[2]) / 2 for b in boxes.values()) / len(boxes), "top": min(b[1] for b in boxes.values()),
               "bottom": max(b[3] for b in boxes.values()), "middle": sum((b[1] + b[3]) / 2 for b in boxes.values()) / len(boxes)}[how]
        for aid, b in boxes.items():
            bw, bh = b[2] - b[0], b[3] - b[1]
            if how in ("left", "center", "right"):
                set_box(win, aid, b, x={"left": ref, "center": ref - bw / 2, "right": ref - bw}[how])
            else:
                set_box(win, aid, b, y={"top": ref, "middle": ref - bh / 2, "bottom": ref - bh}[how])
    commit(win)


def set_box(win, aid, box, x=None, y=None):
    a = an.find(win.spec, aid)
    a["pin"] = None
    a["pos"] = dict(x=float(box[0] if x is None else x), y=float(box[1] if y is None else y), anchor="top-left")


def edit_label(win, aid):
    """A line to type in, over the preview at the item (Enter keeps it, Esc cancels)."""
    a = an.find(win.spec, aid)
    if a is None:
        return
    cur = (a.get("label") or {}).get("text", "") if a["kind"] == "arrow" else a.get("text", "")
    le = QtW.QLineEdit(win.prev); le.setText(cur); le.setPlaceholderText("type the label"); le.setMinimumWidth(200)
    od = win.prev.overlay; r = od.rects().get(f"anntail:{aid}") or od.rects().get(f"ann:{aid}") if od is not None else None
    pt = win.prev.to_widget(r[0][0], r[0][1]).toPoint() if r and win.prev.image_px() else QtC.QPoint(40, 40)
    le.move(max(4, min(pt.x(), win.prev.width() - 210)), max(4, min(pt.y(), win.prev.height() - 30))); le.show(); le.setFocus(); le.selectAll()
    win.label_editor = le

    def done():
        b = an.find(win.spec, aid)
        if b is not None and le.isVisible():
            if b["kind"] == "arrow":
                b.setdefault("label", {})["text"] = le.text()
            else:
                b["text"] = le.text()
            commit(win)
        le.hide(); le.deleteLater(); win.label_editor = None
    le.returnPressed.connect(done); le.editingFinished.connect(done)


def add_arrow_at(win, f):
    """A new arrow whose tip is the 3D point under f; label: the structure's name; shown during the selected part."""
    ren = win.st.ren
    p = ren.pick_point(*f) if ren is not None else None
    if p is None:
        win.say("Nothing there. Click on a structure or on the slice.", "err"); return None
    hit = None
    try:
        hit = win.st.pick(win.spec_for(), {"frame": win.frame, "x": f[0], "y": f[1]})
    except Exception:
        pass
    found = bool(hit and hit.get("found"))
    pid = win.selected_part if win.selected_part else None
    a = an.new_arrow(p, hit["class"] if found else None, hit.get("component") if found else None,
                     hit.get("name", "") if found else "", parts=[pid] if pid else "all")
    win.spec.setdefault("annotations", []).append(a); commit(win)
    QtC.QTimer.singleShot(0, lambda: edit_label(win, a["id"]))
    win.say("Arrow added. Type its label, drag its tail to turn it, or right-click it for more.", "ok")
    return a


def add_text_at(win, f):
    pid = win.selected_part if win.selected_part else None
    a = an.new_text("Text", x=f[0] if f else 0.7, y=f[1] if f else 0.2, parts=[pid] if pid else "all")
    win.spec.setdefault("annotations", []).append(a); commit(win)
    QtC.QTimer.singleShot(0, lambda: edit_label(win, a["id"]))
    return a


def start_add_pointer(win, aid):
    """Click things in the preview: the arrow or text box points at each of them too; a click on empty space ends."""
    win.pick_btn.setChecked(False); win.end_picks(); win.arrow_pick = aid; win.prev.setCursor(QtC.Qt.CrossCursor)
    win.say("Click each thing it should also point at. Click on empty space when done.")


def add_pointer_at(win, aid, f):
    """One more thing for an arrow or text box to point at (the 3D point under f). Returns False when nothing is there."""
    ren = win.st.ren; a = an.find(win.spec, aid)
    p = ren.pick_point(*f) if ren is not None and a is not None else None
    if p is None:
        return False
    hit = None
    try:
        hit = win.st.pick(win.spec_for(), {"frame": win.frame, "x": f[0], "y": f[1]})
    except Exception:
        pass
    found = bool(hit and hit.get("found"))
    an.add_pointer(a, p, hit["class"] if found else None, hit.get("component") if found else None); commit(win)
    n = len(an.pointers(a)) + (1 if a["kind"] == "arrow" else 0)
    win.say(f"It now points at {n} things. Click another, or on empty space when done.", "ok")
    return True


def start_add_arrow(win):
    win.pick_btn.setChecked(False); win.end_picks(); win.arrow_pick = True; win.prev.setCursor(QtC.Qt.CrossCursor)
    win.say("Click the spot the arrow should point to.")


def preview_menu(win):
    """Entries for the preview's right-click menu (gui_navigate)."""
    def extra(m, f, P):
        if f is None:
            return
        m.addAction("Add arrow here", lambda: add_arrow_at(win, f)).setEnabled(P is not None)
        m.addAction("Add text here", lambda: add_text_at(win, f))
    return extra


# ---------------------------------------------------------------------------------------------------------------
# the "Arrows and text" list (Look tab)
# ---------------------------------------------------------------------------------------------------------------
class AnnotationPanel(QtC.QObject):
    def __init__(self, win, layout):
        super().__init__(win); self.win = win
        g = Section("Arrows and text", "arrows_text", True, "Arrows point at a spot on a structure and stay on it while the camera "
                    "moves. Right-click one in the preview for its shape, colour and more.")
        self.group = g; layout.addWidget(g)
        v = QtW.QVBoxLayout(g.body)
        h = QtW.QHBoxLayout(); v.addLayout(h)
        self.add_arrow = QtW.QPushButton("+ Add arrow"); self.add_arrow.setToolTip("Then click the spot in the preview the arrow should point to")
        self.add_arrow.clicked.connect(lambda: start_add_arrow(win))
        self.add_text = QtW.QPushButton("+ Add text"); self.add_text.setToolTip("A text box; drag it in the preview to place it")
        self.add_text.clicked.connect(lambda: add_text_at(win, None))
        self.only_part = QtW.QCheckBox("only the selected part"); self.only_part.setChecked(True)
        self.only_part.setToolTip("List only the arrows and text of the part selected on the timeline")
        self.only_part.toggled.connect(lambda _: self.rebuild())
        h.addWidget(self.add_arrow); h.addWidget(self.add_text); h.addWidget(self.only_part); h.addStretch(1)
        self.rows = QtW.QVBoxLayout(); v.addLayout(self.rows)
        self.note = QtW.QLabel(""); self.note.setVisible(False)  # (3.5: the help text is the group's tooltip)

    def rebuild(self):
        from .gui_data import clear_layout
        clear_layout(self.rows); w = self.win
        if w.spec is None:
            return
        pid = w.selected_part
        for a in an.items(w.spec):
            if self.only_part.isChecked() and pid and not an.shown_in(a, pid):
                continue
            self.rows.addWidget(self._row(a))

    def _row(self, a):
        """One item: [shown] [arrow / text] [label] [shape or box] [colour] [x], and under it when it shows and what it
        points at (3.5: the common choices are in the row; the rest stays in the ⋯ menu)."""
        w = self.win; aid = a["id"]; arrow = a["kind"] == "arrow"
        row = QtW.QFrame(); row.setFrameShape(QtW.QFrame.StyledPanel)
        v = QtW.QVBoxLayout(row); v.setContentsMargins(4, 3, 4, 3); v.setSpacing(2)
        h = QtW.QHBoxLayout(); h.setContentsMargins(0, 0, 0, 0); v.addLayout(h)
        vis = QtW.QCheckBox(); vis.setChecked(a.get("visible", True)); vis.setToolTip("Show or hide this item")
        vis.toggled.connect(lambda on: change(w, aid, lambda b: b.__setitem__("visible", bool(on))))
        kind = QtW.QLabel("→" if arrow else "T"); kind.setToolTip("An arrow" if arrow else "A text box")
        txt = QtW.QLineEdit((a.get("label") or {}).get("text", "") if arrow else a.get("text", ""))
        txt.setToolTip("The label of the arrow, or the text"); txt.setMinimumWidth(80)
        txt.setPlaceholderText("label" if arrow else "text")

        def set_text():
            def fn(b, t=txt.text()):
                if b["kind"] == "arrow":
                    b.setdefault("label", {})["text"] = t
                else:
                    b["text"] = t
            change(w, aid, fn)
        txt.editingFinished.connect(set_text)
        st = a.get("style") or {}

        def combo(items, cur, key, tip):
            cb = QtW.QComboBox(); cb.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon); cb.setMinimumContentsLength(5)
            for t, d in items:
                cb.addItem(t, d)
            cb.setCurrentIndex(max(0, cb.findData(cur))); cb.setToolTip(tip)
            cb.activated.connect(lambda j: change(w, aid, lambda b: b.setdefault("style", {}).__setitem__(key, cb.itemData(j))))
            return cb
        look = combo(an.SHAPES, st.get("shape", "arrow"), "shape", "Shape of the arrow") if arrow else \
            combo(an.BOXES, st.get("box", "dark"), "box", "Box behind the text")
        col = combo(COLOURS, st.get("color", "auto"), "color", "Colour")
        dele = QtW.QToolButton(); dele.setText("✕"); dele.setToolTip("Delete this item"); dele.clicked.connect(lambda: delete(w, aid))
        for x in (vis, kind, txt, look, col, dele):
            h.addWidget(x)
        h.setStretch(2, 1)
        h2 = QtW.QHBoxLayout(); h2.setContentsMargins(22, 0, 0, 0); v.addLayout(h2)
        sh = a.get("show") or {}
        stt = QtW.QDoubleSpinBox(); stt.setRange(-0.1, 600); stt.setDecimals(1); stt.setSingleStep(0.5); stt.setSpecialValueText("start")
        stt.setPrefix("from "); stt.setSuffix(" s"); stt.setValue(-0.1 if sh.get("start") is None else float(sh["start"]))
        stt.setToolTip("When it appears, in seconds from the start of the part ('start': from the beginning)")
        en = QtW.QDoubleSpinBox(); en.setRange(-0.1, 600); en.setDecimals(1); en.setSingleStep(0.5); en.setSpecialValueText("end")
        en.setPrefix("to "); en.setSuffix(" s"); en.setValue(-0.1 if sh.get("end") is None else float(sh["end"]))
        en.setToolTip("When it disappears ('end': at the end of the part)")
        stt.valueChanged.connect(lambda x: change(w, aid, lambda b: b["show"].__setitem__("start", None if x < 0 else float(x))))
        en.valueChanged.connect(lambda x: change(w, aid, lambda b: b["show"].__setitem__("end", None if x < 0 else float(x))))
        here = QtW.QToolButton(); here.setText("⏵"); here.setToolTip("Start here: it appears at the playhead")
        here.clicked.connect(lambda: self.at_playhead(aid, "start"))
        there = QtW.QToolButton(); there.setText("⏹"); there.setToolTip("End here: it disappears at the playhead")
        there.clicked.connect(lambda: self.at_playhead(aid, "end"))
        n = len(an.pointers(a))
        more = QtW.QToolButton(); more.setText(f"+ point ({n + (1 if arrow else 0)})" if n else "+ point")
        more.setToolTip("Point at more things with this one label: click each of them in the preview (one line to each, as in "
                        "figure callouts). Right-click a pointer's end to remove it.")
        more.clicked.connect(lambda: start_add_pointer(w, aid))
        menu = QtW.QToolButton(); menu.setText("⋯"); menu.setToolTip("Edit, duplicate, copy to another part, align, same look for all ...")
        menu.clicked.connect(lambda: item_menu(w, aid, menu.mapToGlobal(QtC.QPoint(0, menu.height()))))
        for x in (stt, en, here, there, more, menu):
            h2.addWidget(x)
        h2.addStretch(1)
        return row

    def at_playhead(self, aid, which):
        w = self.win; plan = w.timeline.canvas.plan; a = an.find(w.spec, aid)
        if plan is None or a is None:
            return
        f = plan.frames[w.frame]; pid = f.get("part")
        from .storyboard import main_range
        rng = main_range(plan, pid) if pid else None
        if rng is None:
            return
        t = max(0.0, (w.frame - rng[0]) / plan.fps)
        if not an.shown_in(a, pid):
            a["show"]["parts"] = [pid]
        a["show"][which] = round(t, 2); commit(w)


def paint_selection(p, prev, win):
    """Dashed boxes around the selected arrows and text boxes."""
    od = prev.overlay
    if od is None or not win.ann_mouse.selected:
        return
    rects = od.rects()
    for aid in win.ann_mouse.selected:
        r = rects.get(f"ann:{aid}")
        if not r:
            continue
        a, b = prev.to_widget(r[0][0], r[0][1]), prev.to_widget(r[0][2], r[0][3])
        p.setPen(QtG.QPen(prev.palette().highlight().color(), 1, QtC.Qt.DashLine)); p.setBrush(QtC.Qt.NoBrush)
        p.drawRect(QtC.QRectF(a, b))
