"""Editing the movie: adding, removing, reordering, splitting parts, undo and redo, and the parts list of the Movie tab."""
from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from . import storyboard as sb
from .common import UserError
from .gui_data import clear_layout
from .gui_theme import DIM

ADD_ENTRIES = (("Slide up through the slices", "slide_up"), ("Slide back, revealing the segmentation", "reveal"),
               ("Overview", "overview"), ("View from here", "view"), ("Hold (pause)", "hold"))
CARD_ENTRIES = (("Title card", "title_card"), ("End card (thank you)", "end_card"), ("Blank card", "card"))


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
        if kind == "title_card":  # at the start, with the tomogram's name as the title
            import re as _re
            name = _re.sub(r"\.[^.]+$", "", (self.win.info or {}).get("tomo_name") or "") or "Title"
            self.insert(sb.new_part("title_card", title=name), "start")
        elif kind == "end_card":  # at the end
            self.insert(sb.new_part("end_card"), self.spec["shots"][-1]["id"] if self.spec["shots"] else "start")
        elif kind == "view":  # the camera position of the main view as it is now
            v = self.spec["view"]
            self.insert(sb.new_part("view", view=dict(tilt=float(v["tilt"]), az=float(v["az"]), zoom=float(v["zoom"]))), after)
        else:
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
        self.win.timeline.set_plan(sb.build_plan(self.spec, self.win.st.ds), self.spec, self.win.st.ds)  # the preview waits for the release

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
         "undo": self.undo, "redo": self.redo, "play": self.win.toggle_play}[cmd]()

    # ---- menus ----
    def fill_add_menu(self, menu, after=None):
        menu.clear(); info = self.win.info or {}
        has_t, has_s = bool(info.get("has_tomogram")), bool(info.get("has_segmentation"))
        for text, kind in ADD_ENTRIES:
            a = menu.addAction(text, lambda kind=kind: self.add(kind, after))
            need = "needs a tomogram" if kind == "slide_up" and not has_t else "needs a segmentation" if kind == "reveal" and not has_s else ""
            a.setEnabled(not need); a.setToolTip(need)
            if kind == "view":
                a.setToolTip("A still view with its own camera position; drag in the preview to change it. "
                             "Two of them in a row make the camera fly from one to the other.")
            if kind == "overview":
                sub = menu.addMenu("Close-up of"); sub.setEnabled(has_s); sub.setToolTip("" if has_s else "needs a segmentation")
                self._fill_closeups(sub, after)
        menu.addSeparator()
        for text, kind in CARD_ENTRIES:
            a = menu.addAction(text, lambda kind=kind: self.add(kind, after))
            a.setToolTip({"title_card": "A card with a title at the start of the movie",
                          "end_card": "A 'Thank you' card at the end of the movie",
                          "card": "An empty card at the playhead, for any text or pictures"}[kind])
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


class ClickableHeader(QtW.QWidget):
    clicked = QtC.Signal()

    def mousePressEvent(self, e):
        if e.button() == QtC.Qt.LeftButton:
            self.clicked.emit()


def _combo(items, current, on_change):
    """A drop-down list of (text, value); on_change(value) when the user picks one."""
    cb = QtW.QComboBox()
    for t, d in items:
        cb.addItem(t, d)
    i = cb.findData(current)
    cb.setCurrentIndex(i if i >= 0 else 0)
    cb.activated.connect(lambda j: on_change(cb.itemData(j)))
    return cb


def _spin(lo, hi, value, on_change, step=0.5, suffix=" s", decimals=2, special=None):
    sp = QtW.QDoubleSpinBox(); sp.setRange(lo, hi); sp.setSingleStep(step); sp.setDecimals(decimals); sp.setSuffix(suffix)
    if special:  # the lowest value stands for "automatic"
        sp.setSpecialValueText(special)
    sp.setValue(value); sp.valueChanged.connect(on_change)
    return sp


SLIDE_ENDS = (("first slice", "first"), ("last slice", "last"), ("where the slice is", "current"), ("a slice number", "number"))
STRUCTURES = (("hidden", "none"), ("above the slice", "above"), ("all", "all"), ("leave as they are", "keep"))
MOTIONS = (("rock", "rock"), ("full spin", "spin"), ("still", "none"))
FOCUS_MOTIONS = (("orbit around it", "orbit"), ("rock", "rock"), ("hold still", "none"))
OTHERS = (("grey", "grey"), ("hidden", "hide"), ("unchanged", "keep"))


class PartsPanel(QtW.QWidget):
    """The parts of the movie in the Movie tab: one line each that opens to show its settings (one open at a time).
    The open line is the part selected on the timeline. Widgets look parts up by id, never by position."""

    def __init__(self, win):
        super().__init__(); self.win = win; self.rows = {}; self.open_id = None; self._sig = None
        self.lay = QtW.QVBoxLayout(self); self.lay.setContentsMargins(0, 0, 0, 0); self.lay.setSpacing(3)

    # ---- helpers ----
    def part(self, pid):
        for p in self.win.spec["shots"]:
            if p["id"] == pid:
                return p
        return None

    def _problems(self):
        plan = self.win.timeline.canvas.plan
        return plan.problems if plan is not None else {}

    def _reason(self, p):
        info = self.win.info or {}
        if not p.get("on", True):
            return "switched off"
        if p["type"] == "slide" and p.get("structures") == "none" and not info.get("has_tomogram"):
            return "needs a tomogram, so it is left out"
        if p["type"] == "slide" and p.get("structures") in ("above", "all") and not info.get("has_segmentation"):
            return "needs a segmentation, so it is left out"
        if p["id"] in self._problems():
            return "structure not loaded, so it is left out; choose another structure or load it again"
        return ""

    # ---- building ----
    def sync(self, force=False):
        """Rebuild when the list of parts changed (ids or order, or other data); otherwise update the lines in place."""
        spec, info = self.win.spec, self.win.info or {}
        sig = (id(spec), tuple(p["id"] for p in spec["shots"]), bool(info.get("has_tomogram")), bool(info.get("has_segmentation")),
               tuple(c["key"] for c in info.get("structures", [])))
        if force or sig != self._sig:
            self._sig = sig; self.rebuild()
        else:
            for pid in self.rows:
                self.refresh_line(pid)
        sel = self.win.selected_part
        if sel in self.rows and sel != self.open_id:
            self.open(sel, select=False)
        elif sel not in self.rows and self.open_id is not None:
            self.close_open()

    def rebuild(self):
        clear_layout(self.lay); self.rows = {}; keep = self.open_id; self.open_id = None
        for i, p in enumerate(self.win.spec["shots"]):
            box = QtW.QFrame(); box.setFrameShape(QtW.QFrame.StyledPanel); box.setObjectName("part")
            v = QtW.QVBoxLayout(box); v.setContentsMargins(4, 2, 4, 2); v.setSpacing(2)
            head = ClickableHeader(); h = QtW.QHBoxLayout(head); h.setContentsMargins(0, 0, 0, 0)
            arrow = QtW.QToolButton(); arrow.setArrowType(QtC.Qt.RightArrow); arrow.setAutoRaise(True)
            arrow.setToolTip("Open or close the settings of this part")
            tick = QtW.QCheckBox(); tick.setToolTip("Untick to leave this part out of the movie without deleting it")
            num = QtW.QLabel(f"{i + 1}"); num.setFixedWidth(18)
            name = QtW.QLabel(""); dur = QtW.QLabel(""); dur.setStyleSheet(DIM)
            more = QtW.QToolButton(); more.setText("\u22ee"); more.setToolTip("Duplicate, move or delete this part")
            more.setPopupMode(QtW.QToolButton.InstantPopup); menu = QtW.QMenu(more); more.setMenu(menu)
            pid = p["id"]
            menu.addAction("Duplicate", lambda pid=pid: self.win.editor.duplicate(pid))
            menu.addAction("Move up", lambda pid=pid: self.win.editor.move_by(pid, -1)).setEnabled(i > 0)
            menu.addAction("Move down", lambda pid=pid: self.win.editor.move_by(pid, 1)).setEnabled(i < len(self.win.spec["shots"]) - 1)
            menu.addSeparator(); menu.addAction("Delete", lambda pid=pid: self.win.editor.delete(pid))
            for w, stretch in ((arrow, 0), (tick, 0), (num, 0), (name, 1), (dur, 0), (more, 0)):
                h.addWidget(w, stretch)
            arrow.clicked.connect(lambda _=False, pid=pid: self.toggle(pid)); head.clicked.connect(lambda pid=pid: self.toggle(pid))
            tick.toggled.connect(lambda on, pid=pid: self.set_on(pid, on))
            note = QtW.QLabel(""); note.setStyleSheet(DIM); note.setWordWrap(True); note.setContentsMargins(46, 0, 0, 0)
            details = QtW.QWidget(); details.setVisible(False); dl = QtW.QFormLayout(details); dl.setContentsMargins(46, 2, 0, 4)
            v.addWidget(head); v.addWidget(note); v.addWidget(details)
            self.lay.addWidget(box)
            self.rows[pid] = dict(box=box, head=head, arrow=arrow, on=tick, num=num, name=name, dur=dur, note=note,
                                  details=details, form=dl)
            self.refresh_line(pid)
        if not self.win.spec["shots"]:
            lab = QtW.QLabel("The movie has no parts. Add one with + Add part."); lab.setStyleSheet(DIM); self.lay.addWidget(lab)
        if keep in self.rows:
            self.open(keep, select=False)

    def refresh_line(self, pid):
        r, p = self.rows[pid], self.part(pid)
        if p is None:
            return
        r["on"].blockSignals(True); r["on"].setChecked(bool(p.get("on", True))); r["on"].blockSignals(False)
        r["name"].setText(sb.part_name(p, self.win.spec, self.win.st.ds)); r["dur"].setText(f"{float(p.get('seconds', 5)):.1f} s")
        why = self._reason(p)
        r["note"].setText(why); r["note"].setVisible(bool(why))
        r["name"].setStyleSheet(DIM if why else ""); r["on"].setToolTip(why or "Untick to leave this part out of the movie without deleting it")

    # ---- opening and closing ----
    def toggle(self, pid):
        if self.open_id == pid:
            self.close_open(); self.win.selected_part = None; self.win.timeline.set_selected(None)
        else:
            self.open(pid)

    def close_open(self):
        r = self.rows.get(self.open_id)
        if r:
            clear_layout(r["form"]); r["details"].setVisible(False); r["arrow"].setArrowType(QtC.Qt.RightArrow)
            r["box"].setStyleSheet("")
        self.open_id = None

    def open(self, pid, select=True):
        """Open one line (closing the others); select=True also selects the part on the timeline and shows it."""
        if pid not in self.rows:
            return
        self.close_open(); self.open_id = pid; r = self.rows[pid]
        self.fill_details(pid); r["details"].setVisible(True); r["arrow"].setArrowType(QtC.Qt.DownArrow)
        r["box"].setStyleSheet("QFrame#part { border: 1px solid palette(highlight); border-radius: 3px }")  # not its labels
        if select:
            self.win.selected_part = pid; self.win.timeline.set_selected(pid)
            if self.win.timeline.canvas.parts.get(pid):
                self.win.req = {"at": {"part": pid, "u": 0}}; self.win.schedule(0)
            self.win.editor.refresh_buttons()
        QtC.QTimer.singleShot(0, lambda: self.scroll_to(pid))

    def scroll_to(self, pid):
        r = self.rows.get(pid); w = self.parentWidget()
        while w is not None and not isinstance(w, QtW.QScrollArea):
            w = w.parentWidget()
        if r and w is not None:
            w.ensureWidgetVisible(r["box"], 0, 20)

    # ---- settings ----
    def set_on(self, pid, on):
        sb.set_part(self.win.spec, pid, on=bool(on)); self.win.editor.commit(select=pid)

    def set(self, pid, key, value, rebuild=False):
        p = self.part(pid)
        if p is None:
            return
        if value is None:
            p.pop(key, None)
        else:
            p[key] = value
        self.refresh_line(pid); self.win.editor.changed(); self.win._sweep_hint(); self.win.schedule()
        if rebuild:
            self.fill_details(pid)

    def fill_details(self, pid):
        r, p = self.rows[pid], self.part(pid)
        form = r["form"]; clear_layout(form)
        add = form.addRow
        t = p["type"]
        if t == "slide":
            self._slide_rows(pid, p, form)
        elif t == "overview":
            add("Camera movement", _combo(MOTIONS, p.get("motion", "rock"), lambda v: self.set(pid, "motion", v, rebuild=True)))
            if p.get("motion", "rock") == "rock":
                add("Rock angle", _spin(5, 90, float(p.get("angle", 25)), lambda v: self.set(pid, "angle", v), step=5, suffix="\u00b0", decimals=0))
            self._view_rows(pid, p, form)
        elif t == "focus":
            self._focus_rows(pid, p, form)
        elif t == "card":
            from .gui_cards import card_rows
            card_rows(self, pid, p, form)
        add("Duration", _spin(0.2, 120, float(p.get("seconds", 5)), lambda v: self.set(pid, "seconds", v)))
        cap = QtW.QLineEdit(p.get("caption", "")); cap.setPlaceholderText("text shown at the bottom while this part plays")
        cap.textChanged.connect(lambda txt: self.set(pid, "caption", txt)); add("Caption", cap); r["caption"] = cap
        from .gui_overlays import part_overlay_row
        ovr = part_overlay_row(self.win, pid, p); r["overlays"] = ovr
        if t == "card":
            add("On the picture", ovr); return
        tomo = self.win._tomo_row(p); add("", tomo); r["tomo"] = tomo
        add("On the picture", ovr)
        tr = p.get("transition")
        add("Transition in", _spin(-0.1, 10, -0.1 if tr is None else float(tr), lambda v: self.set(pid, "transition", None if v < 0 else v),
                                   step=0.1, special="automatic"))
        form.labelForField(form.itemAt(form.rowCount() - 1, QtW.QFormLayout.FieldRole).widget()).setToolTip(
            "Seconds of camera flight into this part; 0 cuts straight to it")

    def _view_rows(self, pid, p, form):
        """View: the main view, or this part's own camera position (Tilt, Rotate, Zoom)."""
        own = bool(p.get("view"))
        main = self.win.spec["view"]

        def choose(v):
            self.set(pid, "view", dict(tilt=float(main["tilt"]), az=float(main["az"]), zoom=float(main["zoom"])) if v == "own" else None,
                     rebuild=True)
        form.addRow("View", _combo((("main view", "main"), ("own view", "own")), "own" if own else "main", choose))
        if not own:
            return
        vw = p["view"]; self.view_sliders = {}

        def slider(key, lo, hi, scale, fmt):
            row = QtW.QWidget(); h = QtW.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
            s = QtW.QSlider(QtC.Qt.Horizontal); s.setRange(lo, hi); s.setValue(int(round(float(vw.get(key, 0)) * scale)))
            val = QtW.QLabel(fmt(s.value())); val.setFixedWidth(52)

            def moved(x, key=key):
                val.setText(fmt(x)); cur = dict(self.part(pid).get("view") or {}); cur[key] = x / scale
                self.set(pid, "view", cur)
            s.valueChanged.connect(moved); h.addWidget(s, 1); h.addWidget(val)
            self.view_sliders[key] = (s, val, scale, fmt)
            return row
        form.addRow("Tilt", slider("tilt", 0, 85, 1, lambda x: f"{x}\u00b0"))
        form.addRow("Rotate", slider("az", -180, 180, 1, lambda x: f"{x}\u00b0"))
        form.addRow("Zoom", slider("zoom", 50, 300, 100, lambda x: f"{x / 100:.2f}x"))
        tip = QtW.QLabel("Or drag in the preview while this part is selected and the playhead is in it.")
        tip.setStyleSheet(DIM); tip.setWordWrap(True); form.addRow("", tip)

    def show_view(self, pid):
        """The part's view was changed by dragging in the preview: move its sliders along (without changing it again)."""
        if pid != self.open_id or not getattr(self, "view_sliders", None):
            return
        vw = (self.part(pid) or {}).get("view") or {}
        for key, (s, val, scale, fmt) in self.view_sliders.items():
            try:
                s.blockSignals(True); s.setValue(int(round(float(vw.get(key, 0)) * scale))); val.setText(fmt(s.value()))
            except RuntimeError:  # the sliders were rebuilt meanwhile
                pass
            finally:
                try:
                    s.blockSignals(False)
                except RuntimeError:
                    pass

    def _slide_rows(self, pid, p, form):
        nz = (self.win.info or {}).get("dims", [1, 1, 1])[2]
        for key, label, choices in (("from", "From", SLIDE_ENDS), ("to", "To", SLIDE_ENDS[:2] + SLIDE_ENDS[3:])):
            val = p.get(key); mode = "number" if isinstance(val, int) else val
            row = QtW.QWidget(); h = QtW.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
            num = QtW.QSpinBox(); num.setRange(1, nz); num.setPrefix("slice "); num.setValue((val + 1) if isinstance(val, int) else 1)
            num.setVisible(mode == "number")
            num.valueChanged.connect(lambda v, key=key: self.set(pid, key, int(v) - 1))
            cb = _combo(choices, mode, lambda v, key=key, num=num: (num.setVisible(v == "number"),
                                                                     self.set(pid, key, int(num.value()) - 1 if v == "number" else v)))
            h.addWidget(cb, 1); h.addWidget(num); form.addRow(label, row)
        form.addRow("Structures", _combo(STRUCTURES, p.get("structures", "keep"), lambda v: self.set(pid, "structures", v)))
        form.addRow("Pause before", _spin(0, 10, float(p.get("pause_before", 0)), lambda v: self.set(pid, "pause_before", v), step=0.25))
        form.addRow("Pause after", _spin(0, 10, float(p.get("pause_after", 0)), lambda v: self.set(pid, "pause_after", v), step=0.25))

    def structure_combo(self, pid, p):
        """Drop-down of the loaded structures with their colours, grouped under the source when there are several."""
        win, spec = self.win, self.win.spec
        structs = [c for c in (win.info or {}).get("structures", []) if c.get("mesh")]
        cb = QtW.QComboBox(); model = QtG.QStandardItemModel(); cb.setModel(model)
        several = len({c["source"] for c in structs}) > 1; src = None
        for c in structs:
            if several and c["source"] != src:
                src = c["source"]; head = QtG.QStandardItem(c["source_title"]); head.setEnabled(False); model.appendRow(head)
            cs = spec["classes"].get(c["key"], {})
            pm = QtG.QPixmap(12, 12); pm.fill(QtG.QColor(cs.get("color", c["color"])))
            it = QtG.QStandardItem(QtG.QIcon(pm), cs.get("name", c["name"])); it.setData(c["key"], QtC.Qt.UserRole); model.appendRow(it)
        cur = (p.get("target") or {}).get("class")
        k = win.st.ds.id_of(cur) if cur is not None else None
        key = win.st.ds.key_of(k) if k is not None else None
        i = cb.findData(key)
        if i < 0:
            cb.insertItem(0, "(structure not loaded)", None); i = 0
        cb.setCurrentIndex(i)
        cb.activated.connect(lambda j: self.retarget(pid, cb.itemData(j)))
        return cb

    def retarget(self, pid, key):
        """Another structure for a close-up: whole structure, and the caption follows unless the user wrote their own."""
        p = self.part(pid)
        if p is None or key is None:
            return
        ds, spec = self.win.st.ds, self.win.spec
        old = (p.get("target") or {}).get("class"); k_old = ds.id_of(old) if old is not None else None
        old_name = spec["classes"].get(ds.key_of(k_old), {}).get("name") if k_old is not None else None
        new_name = spec["classes"].get(key, {}).get("name", ds.classes[ds.id_of(key)]["name"])
        p["target"] = {"class": key}
        if not p.get("caption") or p.get("caption") == old_name:
            p["caption"] = new_name
        self.win.editor.commit(select=pid); self.fill_details(pid)

    def _focus_rows(self, pid, p, form):
        win = self.win; ds = win.st.ds
        form.addRow("Structure", self.structure_combo(pid, p))
        tgt = p.get("target") or {}; k = ds.id_of(tgt.get("class")) if tgt.get("class") is not None else None
        obj = QtW.QComboBox(); obj.addItem("whole structure", "whole")
        if tgt.get("component") is not None and k is not None:
            try:
                n = int(ds.components(k)[2][int(tgt["component"])])
            except (IndexError, KeyError, TypeError):
                n = 0
            obj.addItem(f"one object (picked in the preview), {n:,} voxels", "one")
            obj.setCurrentIndex(1)
        obj.addItem("pick an object in the preview...", "pick"); obj.setEnabled(k is not None)

        def chose(j):
            v = obj.itemData(j)
            if v == "whole":
                t = dict(p.get("target") or {}); t.pop("component", None); p["target"] = t
                self.win.editor.commit(select=pid); self.fill_details(pid)
            elif v == "pick":
                obj.setCurrentIndex(1 if tgt.get("component") is not None else 0)
                win.start_object_pick(pid, ds.key_of(k))
        obj.activated.connect(chose)
        form.addRow("Object", obj)
        form.addRow("Other structures", _combo(OTHERS, p.get("others", "grey"), lambda v: self.set(pid, "others", v)))
        form.addRow("Camera movement", _combo(FOCUS_MOTIONS, p.get("motion", "orbit"), lambda v: self.set(pid, "motion", v)))
        form.addRow("Zoom", _spin(0.3, 4, float(p.get("zoom", 1)), lambda v: self.set(pid, "zoom", v), step=0.1, suffix=" x"))
        form.addRow("Tilt", _spin(-1, 85, float(p["tilt"]) if p.get("tilt") is not None else -1,
                                  lambda v: self.set(pid, "tilt", None if v < 0 else v), step=5, suffix="\u00b0", decimals=0,
                                  special="automatic"))
