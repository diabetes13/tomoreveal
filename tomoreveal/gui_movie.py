"""Editing the movie: adding, removing, reordering, splitting parts, undo and redo, and the parts list of the Movie tab."""
from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from . import storyboard as sb
from .common import UserError
from .gui_data import clear_layout
from .gui_theme import DIM

ADD_ENTRIES = (("Slide up through the slices", "slide_up"), ("Slide back, revealing the segmentation", "reveal"),
               ("Overview", "overview"), ("Hold (pause)", "hold"))


class MovieEditor(QtC.QObject):
    """Every change to the parts goes through here: one storyboard edit function, then one undo step."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win; self.history = sb.History(); self.pending_after = None
        tl = win.timeline
        self.add_btn = QtW.QPushButton("+ Add part"); self.add_menu = QtW.QMenu(self.add_btn)
        self.add_menu.aboutToShow.connect(lambda: self.fill_add_menu(self.add_menu)); self.add_btn.setMenu(self.add_menu)
        self.add_btn.setToolTip("Add a part after the one under the playhead")
        self.split_btn = self._btn("Split", "Cut the slide or hold under the playhead in two (key S)", self.split)
        self.dup_btn = self._btn("Duplicate", "Copy the selected part (Ctrl+D)", lambda: self.duplicate())
        self.del_btn = self._btn("Delete", "Remove the selected part (Delete). Undo brings it back.", lambda: self.delete())
        self.undo_btn = self._btn("Undo", "Undo the last change (Ctrl+Z)", self.undo)
        self.redo_btn = self._btn("Redo", "Redo (Ctrl+Shift+Z)", self.redo)
        for i, b in enumerate((self.add_btn, self.split_btn, self.dup_btn, self.del_btn, self.undo_btn, self.redo_btn)):
            tl.buttons.insertWidget(i, b)
        tl.resizing.connect(self.resizing); tl.resized.connect(self.resized); tl.moved.connect(self.moved)
        tl.command.connect(self.command); tl.context_requested.connect(self.context_menu)
        tl.add_requested.connect(lambda pos: self.fill_add_menu(self.add_menu) or self.add_menu.exec(pos))
        self.hist_timer = QtC.QTimer(self); self.hist_timer.setSingleShot(True); self.hist_timer.setInterval(600)
        self.hist_timer.timeout.connect(self.record)
        self.refresh_buttons()

    def _btn(self, text, tip, fn):
        b = QtW.QPushButton(text); b.setToolTip(tip); b.clicked.connect(lambda _=False: fn())
        return b

    # ---- state ----
    @property
    def spec(self):
        return self.win.spec

    @property
    def plan(self):
        return self.win.timeline.canvas.plan

    def reset_history(self):
        """New data loaded: start a new history."""
        self.history.clear()
        if self.spec is not None:
            self.history.push(self.spec)
        self.refresh_buttons()

    def record(self):
        """Store the settings as one undo step (any change: structures, view, clip, look, parts)."""
        if self.spec is not None:
            self.history.push(self.spec)
        self.refresh_buttons()

    def changed(self):
        """A setting changed (slider, tick box ...): store it once things settle."""
        self.hist_timer.start()

    def refresh_buttons(self):
        has = self.spec is not None
        sel = self.target() if has else None
        self.add_btn.setEnabled(has); self.dup_btn.setEnabled(bool(sel)); self.del_btn.setEnabled(bool(sel))
        self.undo_btn.setEnabled(self.history.can_undo); self.redo_btn.setEnabled(self.history.can_redo)
        self.split_btn.setEnabled(self.can_split())

    def can_split(self):
        plan = self.plan
        if self.spec is None or plan is None:
            return False
        f = self.win.timeline.frame
        for sg in plan.segments:
            if sg["start"] <= f < sg["start"] + sg["count"]:
                return sg["phase"] == "main" and sg["type"] in ("slide", "hold") and sg["start"] < f
        return False

    def commit(self, select=None, show=None):
        """After an edit: one undo step, rebuild the Movie tab, redraw; optionally select a part and show its start."""
        self.hist_timer.stop(); self.history.push(self.spec)
        if select is not None:
            self.win.selected_part = select
        if show is not None:
            self.win.req = {"at": {"part": show, "u": 0}}
        self.win.build_controls(); self.win.schedule(0); self.refresh_buttons()

    # ---- where new parts go ----
    def anchor(self):
        """Id of the part after which a new part goes: the part under the playhead; in a transition, the one before it;
        "start" when there are no parts."""
        shots = self.spec["shots"] if self.spec else []
        if not shots:
            return "start"
        plan, f = self.plan, self.win.timeline.frame
        if plan is None:
            return shots[-1]["id"]
        for sg in plan.segments:
            if sg["start"] <= f < sg["start"] + sg["count"] and sg["part"]:
                pid = sg["part"]; rng = sb.main_range(plan, pid)
                if sg["phase"] == "transition" and rng and f < rng[0]:  # leading into pid: insert before it
                    i = [p["id"] for p in shots].index(pid) if pid in [p["id"] for p in shots] else 0
                    return shots[i - 1]["id"] if i > 0 else "start"
                return pid
        return shots[-1]["id"]

    def target(self, pid=None):
        ids = [p["id"] for p in (self.spec or {}).get("shots", [])]
        for cand in (pid, self.win.selected_part):
            if cand in ids:
                return cand
        if self.plan is not None:
            cand = sb.part_at_frame(self.plan, self.win.timeline.frame)
            if cand in ids:
                return cand
        return None

    # ---- edits ----
    def insert(self, part, after=None):
        if self.spec is None:
            return
        after = after or self.anchor()
        sb.insert_part(self.spec, part, after=after)
        self.commit(select=part["id"], show=part["id"])

    def add(self, kind, after=None):
        self.insert(sb.new_part(kind), after)

    def add_closeup(self, key, name, after=None):
        self.insert(sb.focus_shot({"class": key}, name), after)

    def pick_closeup(self, after=None):
        self.pending_after = after or self.anchor(); self.win.pick_btn.setChecked(True)

    def delete(self, pid=None):
        pid = self.target(pid)
        if pid is None:
            return
        ids = [p["id"] for p in self.spec["shots"]]; i = ids.index(pid)
        sb.remove_part(self.spec, pid)
        rest = self.spec["shots"]
        nxt = rest[i]["id"] if i < len(rest) else (rest[-1]["id"] if rest else None)
        self.commit(select=nxt, show=nxt)
        self.win.say(f"Removed part {i + 1}. Undo brings it back." if rest else "The movie has no parts. Add one with + Add part.")

    def duplicate(self, pid=None):
        pid = self.target(pid)
        if pid is None:
            return
        sb.duplicate_part(self.spec, pid)
        new = self.spec["shots"][[p["id"] for p in self.spec["shots"]].index(pid) + 1]["id"]
        self.commit(select=new, show=new)

    def split(self):
        f = self.win.timeline.frame; pid = sb.part_at_frame(self.plan, f) if self.plan else None
        try:
            if pid is None:
                raise UserError("Put the playhead inside a slide or a hold to split it.")
            sb.split_part(self.spec, self.win.st.ds, pid, f)
        except UserError as exc:
            self.win.say(str(exc), "err"); return
        # the first half stays selected with the playhead at its end, so + Add part goes between the halves
        self.win.req = {"at": {"part": pid, "u": 0.999}}
        self.commit(select=pid)
        self.win.say("Split in two. Use + Add part to put something between the halves.", "ok")

    def move_by(self, pid, delta):
        ids = [p["id"] for p in self.spec["shots"]]; i = ids.index(pid)
        if 0 <= i + delta < len(ids):
            sb.move_part(self.spec, pid, i + delta); self.commit(select=pid)

    def toggle(self, pid):
        p = self.spec["shots"][[q["id"] for q in self.spec["shots"]].index(pid)]
        p["on"] = not p.get("on", True); self.commit(select=pid)

    def reset_default(self):
        self.spec["shots"] = sb.default_parts(); self.commit(select=None)
        self.win.say("The movie is back to the three default parts. Undo brings your parts back.", "ok")

    def undo(self):
        self._restore(self.history.undo())

    def redo(self):
        self._restore(self.history.redo())

    def _restore(self, spec):
        if spec is None:
            return
        self.hist_timer.stop(); self.win.spec = spec
        self.win.build_controls(); self.win.schedule(0); self.refresh_buttons()

    # ---- timeline gestures ----
    def resizing(self, pid, secs):
        sb.set_part(self.spec, pid, seconds=secs)
        self.win.timeline.set_plan(sb.build_plan(self.spec, self.win.st.ds), self.spec)  # the preview waits for the release

    def resized(self, pid, secs):
        sb.set_part(self.spec, pid, seconds=secs); self.commit(select=pid)

    def moved(self, pid, pos):
        shots = self.spec["shots"]; visible = [p["id"] for p in shots if p["id"] in self.win.timeline.canvas.parts]
        if pid not in visible:
            return
        j = visible.index(pid)
        if pos > j:
            pos -= 1
        if pos == j:
            return
        rest = [v for v in visible if v != pid]; ids = [p["id"] for p in shots if p["id"] != pid]
        new_index = ids.index(rest[pos]) if pos < len(rest) else len(ids)
        sb.move_part(self.spec, pid, new_index); self.commit(select=pid)

    def command(self, cmd):
        {"delete": lambda: self.delete(), "duplicate": lambda: self.duplicate(), "split": self.split,
         "undo": self.undo, "redo": self.redo}[cmd]()

    # ---- menus ----
    def fill_add_menu(self, menu, after=None):
        menu.clear(); info = self.win.info or {}
        has_t, has_s = bool(info.get("has_tomogram")), bool(info.get("has_segmentation"))
        for text, kind in ADD_ENTRIES:
            a = menu.addAction(text, lambda kind=kind: self.add(kind, after))
            need = "needs a tomogram" if kind == "slide_up" and not has_t else "needs a segmentation" if kind == "reveal" and not has_s else ""
            a.setEnabled(not need); a.setToolTip(need)
            if kind == "overview":
                sub = menu.addMenu("Close-up of"); sub.setEnabled(has_s); sub.setToolTip("" if has_s else "needs a segmentation")
                self._fill_closeups(sub, after)
        menu.setToolTipsVisible(True)

    def _fill_closeups(self, sub, after):
        structs = [c for c in (self.win.info or {}).get("structures", []) if c.get("mesh")]
        several = len({c["source"] for c in structs}) > 1; src = None
        for c in structs:
            if several and c["source"] != src:
                src = c["source"]; sub.addSection(c["source_title"])
            name = (self.spec or {}).get("classes", {}).get(c["key"], {}).get("name", c["name"])
            pm = QtG.QPixmap(12, 12); pm.fill(QtG.QColor((self.spec or {}).get("classes", {}).get(c["key"], {}).get("color", c["color"])))
            sub.addAction(QtG.QIcon(pm), name, lambda key=c["key"], name=name: self.add_closeup(key, name, after))
        sub.addSeparator()
        sub.addAction("an object I click in the preview...", lambda: self.pick_closeup(after))

    def context_menu(self, pid, pos):
        if self.spec is None:
            return
        m = QtW.QMenu(self.win)
        sub = m.addMenu("Add part after" if pid else "Add part")
        self.fill_add_menu(sub, after=pid or None)
        if pid:
            ids = [p["id"] for p in self.spec["shots"]]; i = ids.index(pid); part = self.spec["shots"][i]
            m.addAction("Duplicate", lambda: self.duplicate(pid))
            sp = m.addAction("Split here", self.split); sp.setEnabled(self.can_split() and sb.part_at_frame(self.plan, self.win.timeline.frame) == pid)
            ml = m.addAction("Move left", lambda: self.move_by(pid, -1)); ml.setEnabled(i > 0)
            mr = m.addAction("Move right", lambda: self.move_by(pid, 1)); mr.setEnabled(i < len(ids) - 1)
            m.addAction("Switch on" if not part.get("on", True) else "Switch off", lambda: self.toggle(pid))
            m.addSeparator(); m.addAction("Delete", lambda: self.delete(pid))
        m.exec(pos)


class PartsRows(QtW.QWidget):
    """The parts of the movie as rows in the Movie tab: tick box, number and name, duration, tomogram, a menu."""

    def __init__(self, win):
        super().__init__(); self.win = win; self.rows = {}
        self.lay = QtW.QVBoxLayout(self); self.lay.setContentsMargins(0, 0, 0, 0); self.lay.setSpacing(2)

    def build(self):
        win, spec, info = self.win, self.win.spec, self.win.info or {}
        clear_layout(self.lay); self.rows = {}
        has_t, has_s = bool(info.get("has_tomogram")), bool(info.get("has_segmentation"))
        problems = win.timeline.canvas.plan.problems if win.timeline.canvas.plan else {}
        for i, p in enumerate(spec["shots"]):
            box = QtW.QFrame(); box.setFrameShape(QtW.QFrame.StyledPanel) if p["id"] == win.selected_part else None
            v = QtW.QVBoxLayout(box); v.setContentsMargins(4, 2, 4, 2); v.setSpacing(2)
            row = QtW.QHBoxLayout()
            on = QtW.QCheckBox(f"{i + 1}. {sb.part_name(p, spec, win.st.ds)}"); on.setChecked(bool(p.get("on", True)))
            on.toggled.connect(lambda val, pid=p["id"]: self._set(pid, "on", val))
            row.addWidget(on, 1)
            if p["type"] == "overview":
                mo = QtW.QComboBox()
                for t, d in (("rock", "rock"), ("full spin", "spin"), ("still", "none")):
                    mo.addItem(t, d)
                mo.setCurrentIndex(max(0, mo.findData(p.get("motion", "rock"))))
                mo.activated.connect(lambda j, mo=mo, pid=p["id"]: self._set(pid, "motion", mo.itemData(j))); row.addWidget(mo)
            sec = QtW.QDoubleSpinBox(); sec.setRange(0.2, 120); sec.setSingleStep(0.5); sec.setSuffix(" s"); sec.setValue(float(p.get("seconds", 5)))
            sec.valueChanged.connect(lambda val, pid=p["id"]: self._set(pid, "seconds", val)); row.addWidget(sec)
            more = QtW.QToolButton(); more.setText("⋮"); more.setToolTip("Duplicate, move or delete this part")
            more.setPopupMode(QtW.QToolButton.InstantPopup); menu = QtW.QMenu(more); more.setMenu(menu)
            menu.addAction("Duplicate", lambda pid=p["id"]: win.editor.duplicate(pid))
            menu.addAction("Move up", lambda pid=p["id"]: win.editor.move_by(pid, -1)).setEnabled(i > 0)
            menu.addAction("Move down", lambda pid=p["id"]: win.editor.move_by(pid, 1)).setEnabled(i < len(spec["shots"]) - 1)
            menu.addSeparator(); menu.addAction("Delete", lambda pid=p["id"]: win.editor.delete(pid))
            row.addWidget(more); v.addLayout(row)
            need = ""
            if p["type"] == "slide" and p.get("structures") == "none" and not has_t:
                need = "needs a tomogram"
            elif p["type"] == "slide" and p.get("structures") in ("above", "all") and not has_s:
                need = "needs a segmentation"
            elif p["id"] in problems:
                need = problems[p["id"]]
            if need:
                on.setToolTip(need); lab = QtW.QLabel(f"    {need}, so it is left out"); lab.setStyleSheet(DIM); v.addWidget(lab)
                on.setStyleSheet(DIM)
            tomo = win._tomo_row(p); v.addWidget(tomo)
            self.lay.addWidget(box)
            self.rows[p["id"]] = dict(on=on, seconds=sec, tomo=tomo, box=box)
        if not spec["shots"]:
            lab = QtW.QLabel("The movie has no parts. Add one with + Add part."); lab.setStyleSheet(DIM); self.lay.addWidget(lab)

    def _set(self, pid, key, value):
        sb.set_part(self.win.spec, pid, **{key: value})
        if key == "on":
            self.win.editor.commit(select=pid)
        else:
            self.win.editor.changed(); self.win._sweep_hint(); self.win.schedule()
