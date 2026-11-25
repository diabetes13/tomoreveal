"""Editing the movie: adding, removing, reordering, splitting parts, undo and redo, and the parts list of the Movie tab."""
import copy

from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from . import edits, storyboard as sb
from .animations import fx_settings
from .common import UserError
from .gui_data import clear_layout
from .gui_fold import Fold, fold_rows
from .gui_theme import DIM, part_colour
from .transitions import ease_curve, transition_of

TYPE_NAMES = {"slide": "slides", "overview": "overviews", "focus": "close-ups", "hold": "holds", "card": "cards", "path": "camera paths"}

ADD_ENTRIES = (("Slide up through the slices", "slide_up"), ("Slide back, revealing the segmentation", "reveal"),
               ("Overview", "overview"), ("View from here", "view"), ("One slice, zoomed in", "slice_close"), ("Hold (pause)", "hold"))
CARD_ENTRIES = (("Title card", "title_card"), ("End card (thank you)", "end_card"), ("Blank card", "card"))


def _mouse_held(win):
    """True while the mouse drags something in the preview (an item, an arrow, the camera, a drawing): the history
    timer then waits, so a slow drag is not split into several undo steps (3.6, R16)."""
    prev = getattr(win, "prev", None)
    if prev is None:
        return False
    od, nav, pen = getattr(prev, "overlay", None), getattr(prev, "nav", None), getattr(win, "pen", None)
    return bool((od is not None and od.drag is not None) or (nav is not None and nav.drag is not None)
                or (pen is not None and pen.stroke is not None))


class MovieEditor(QtC.QObject):
    """Every change to the parts goes through here: one storyboard edit function, then one undo step."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win; self.history = edits.History(); self.pending_after = None
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
        if _mouse_held(self.win):  # 3.6: a drag still going on is one step, at its end
            self.hist_timer.start(); return
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
            self.win.end_picks(keep=select); self.win.selected_part = select
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
        if after not in (None, "start") and after not in [p["id"] for p in self.spec["shots"]]:
            after = None  # that part was undone or deleted since: put it at the playhead instead
        after = after or self.anchor()
        edits.insert_part(self.spec, part, after=after)
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
        elif kind == "slice_close":  # the slice at the playhead, seen from the top, as a view of its own (3.5)
            v = self.spec["view"]; plan = self.win.timeline.canvas.plan; f = self.win.frame
            k = int(plan.frames[f]["k"]) if plan is not None and 0 <= f < len(plan.frames) else "current"
            p = sb.new_part("slide_up", seconds=4, pause_before=0.0, pause_after=0.0, structures="none", caption="",
                            view=dict(tilt=0.0, az=float(v["az"]), zoom=max(1.5, float(v.get("zoom", 1.0)))))
            p["from"] = p["to"] = k; p["tomogram"] = dict(p["tomogram"], mode="shown")
            self.insert(p, after)
            self.win.say("Added one slice seen from the top. With this part selected, scroll to zoom and right-drag to move "
                         "to the area you want; tick 'Lock the view of this part' when it looks right. Add arrows with a "
                         "right-click.", "ok")
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
        edits.remove_part(self.spec, pid)
        rest = self.spec["shots"]
        nxt = rest[i]["id"] if i < len(rest) else (rest[-1]["id"] if rest else None)
        self.commit(select=nxt, show=nxt)
        self.win.say(f"Removed part {i + 1}. Undo brings it back." if rest else "The movie has no parts. Add one with + Add part.")

    def duplicate(self, pid=None):
        pid = self.target(pid)
        if pid is None:
            return
        edits.duplicate_part(self.spec, pid)
        new = self.spec["shots"][[p["id"] for p in self.spec["shots"]].index(pid) + 1]["id"]
        self.commit(select=new, show=new)

    def split(self):
        f = self.win.timeline.frame; pid = sb.part_at_frame(self.plan, f) if self.plan else None
        try:
            if pid is None:
                raise UserError("Put the playhead inside a slide or a hold to split it.")
            edits.split_part(self.spec, self.win.st.ds, pid, f)
        except UserError as exc:
            self.win.say(str(exc), "err"); return
        # the first half stays selected with the playhead at its end, so + Add part goes between the halves
        self.win.req = {"at": {"part": pid, "u": 0.999}}
        self.commit(select=pid)
        self.win.say("Split in two. Use + Add part to put something between the halves.", "ok")

    def split_closeup(self, pid):
        """One close-up per object in focus, in a row (a quick tour); undo brings the single close-up back."""
        i = [p["id"] for p in self.spec["shots"]].index(pid); p = self.spec["shots"][i]
        items = sb.target_items(p.get("target"))
        if len(items) < 2:
            return
        name = self.win.parts_panel._name; new = []
        for key, comp in items:
            q = copy.deepcopy(p); q["id"] = sb.new_id(); q["target"] = sb.make_target([(key, comp)])
            q["caption"] = sb.closeup_caption([(key, comp)], name); new.append(q)
        self.spec["shots"][i:i + 1] = new
        self.commit(select=new[0]["id"], show=new[0]["id"])
        self.win.say(f"{len(new)} close-ups, one for each object. Undo brings the single close-up back.", "ok")

    def move_by(self, pid, delta):
        ids = [p["id"] for p in self.spec["shots"]]; i = ids.index(pid)
        if 0 <= i + delta < len(ids):
            edits.move_part(self.spec, pid, i + delta); self.commit(select=pid)

    def toggle(self, pid):
        p = self.spec["shots"][[q["id"] for q in self.spec["shots"]].index(pid)]
        p["on"] = not p.get("on", True); self.commit(select=pid)

    def reset_default(self):
        self.spec["shots"] = sb.default_parts(rock=False); self.commit(select=None)
        self.win.say("The movie is back to the three default parts. Undo brings your parts back.", "ok")

    def undo(self):
        self._record_pending(); self._restore(self.history.undo())

    def redo(self):
        self._record_pending(); self._restore(self.history.redo())

    def _record_pending(self):
        """A change still waiting for the history timer is one undo step of its own."""
        if self.hist_timer.isActive():
            self.hist_timer.stop(); self.record()

    def _restore(self, spec):
        if spec is None:
            return
        self.hist_timer.stop(); self.win.spec = spec
        self.win.build_controls(); self.win.schedule(0); self.refresh_buttons()

    # ---- timeline gestures ----
    def resizing(self, pid, secs):
        edits.set_part(self.spec, pid, seconds=secs)
        self.win.timeline.set_plan(sb.build_plan(self.spec, self.win.st.ds), self.spec, self.win.st.ds)  # the preview waits for the release

    def resized(self, pid, secs):
        edits.set_part(self.spec, pid, seconds=secs); self.commit(select=pid)

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
        edits.move_part(self.spec, pid, new_index); self.commit(select=pid)

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
            if kind == "slice_close":
                a.setToolTip("The slice at the playhead on its own, seen from the top and zoomed in, with a view of its own. "
                             "Zoom and move to the area you want to show, lock the view, and add arrows and text.")
                a.setEnabled(has_t)
            if kind == "view":
                a.setToolTip("A still view with its own camera position; drag in the preview to change it. "
                             "Two of them in a row make the camera fly from one to the other.")
            if kind == "overview":
                sub = menu.addMenu("Close-up of"); sub.setEnabled(has_s); sub.setToolTip("" if has_s else "needs a segmentation")
                self._fill_closeups(sub, after)
        sub = menu.addMenu("Camera path along"); sub.setEnabled(has_s)
        sub.setToolTip("The camera glides along a structure, such as a membrane" if has_s else "needs a segmentation")
        for c in [c for c in info.get("structures", []) if c.get("mesh")]:
            sub.addAction(self.win.parts_panel._name(c["key"]),
                          lambda key=c["key"]: self.insert(sb.new_part("path", source=dict(kind="follow", items=[{"structure": key, "component": None}])), after))
        a = menu.addAction("Camera path through points", lambda: self.insert(sb.new_part("path", source=dict(kind="points", items=[])), after))
        a.setToolTip("The camera flies through points you set")
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
        sub.addAction("a region I draw in the preview...", lambda: self.win.start_region_pick(None, after or self.anchor()))

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
    cb = QtW.QComboBox(); cb.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon); cb.setMinimumContentsLength(8)
    for t, d in items:
        cb.addItem(t, d)
    i = cb.findData(current)
    cb.setCurrentIndex(i if i >= 0 else 0)
    cb.activated.connect(lambda j: on_change(cb.itemData(j)))
    return cb


ENDS = (("no (stop as it is)", "sudden"), ("gently", "gentle"), ("very gently", "very_gentle"))
EASE_CHOICES = (("sudden", "sudden"), ("gentle", "gentle"), ("very gentle", "very_gentle"))
TRANSITION_KINDS = (("camera flight", "flight"), ("cut", "cut"), ("cross-dissolve", "dissolve"), ("fade through black", "black"),
                    ("fade through white", "white"))


def _hrow(*ws):
    w = QtW.QWidget(); h = QtW.QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
    for x in ws:
        h.addWidget(x)
    h.addStretch(1)
    return w


def speed_picture(tin, w=56, h=20):
    """A small picture of the speed over the transition: flat, bell-shaped or one-sided."""
    pm = QtG.QPixmap(w, h); pm.fill(QtC.Qt.transparent); p = QtG.QPainter(pm); p.setRenderHint(QtG.QPainter.Antialiasing)
    us = [i / 40 for i in range(41)]
    ys = [ease_curve(u, tin["ease_out_prev"], tin["ease_in_next"]) for u in us]
    v = [(ys[min(40, i + 1)] - ys[max(0, i - 1)]) for i in range(41)]; top = max(v) or 1.0
    path = QtG.QPainterPath(QtC.QPointF(0, h - 1))
    for i, x in enumerate(v):
        path.lineTo(QtC.QPointF(i / 40 * (w - 1), h - 1 - (h - 3) * x / top))
    path.lineTo(QtC.QPointF(w - 1, h - 1))
    p.setPen(QtG.QPen(QtG.QColor("#ff9f1c"), 1.5)); p.setBrush(QtG.QColor(255, 159, 28, 60)); p.drawPath(path); p.end()
    return pm


def _spin(lo, hi, value, on_change, step=0.5, suffix=" s", decimals=2, special=None):
    sp = QtW.QDoubleSpinBox(); sp.setRange(lo, hi); sp.setSingleStep(step); sp.setDecimals(decimals); sp.setSuffix(suffix)
    if special:  # the lowest value stands for "automatic"
        sp.setSpecialValueText(special)
    sp.setValue(value); sp.valueChanged.connect(on_change)
    return sp


SLIDE_ENDS = (("first slice", "first"), ("last slice", "last"), ("where the slice is", "current"), ("a slice number", "number"))
STRUCTURES = (("hidden", "none"), ("above the slice", "above"), ("all", "all"), ("leave as they are", "keep"))
MOTIONS = (("still", "none"), ("rock", "rock"), ("full spin", "spin"))
# rows of a part's settings that go under "More settings" (3.5), by part type; the unnamed rows after one go with it
MORE_ROWS = {
    "focus": ("Camera push-in", "Highlight", "Object effect", "Zoom", "Tilt", "Vignette", "Caption", "On the picture", "Slow down at the end"),
    "overview": ("Caption", "On the picture", "Slow down at the end"),
    "slide": ("Pause before", "Pause after", "Caption", "On the picture", "Slow down at the end"),
    "path": ("Looks", "Other structures", "Caption", "On the picture"),
    "hold": ("Caption", "On the picture"),
    "card": ("Caption", "On the picture"),
}
EACH_CHOICES = (("as above", ""), ("see-through", "glass"), ("hidden", "hide"))
FOCUS_MOTIONS = (("orbit around it", "orbit"), ("rock", "rock"), ("wiggle", "wiggle"), ("hold still", "none"))
HIGHLIGHTS = (("none", "none"), ("pulse (brighter and back)", "pulse"), ("glowing outline", "glow"))
EFFECTS = (("none", "none"), ("cross-section sweep", "section"), ("grow in", "grow"))
SECTION_DIRS = (("through the slices, top to bottom", "slices"), ("from the viewer's side", "viewer"), ("along the object's longest axis", "axis"))
GROW_HOW = (("build up from the bottom", "bottom"), ("build up from the slice", "slice"), ("fade in", "fade"))
OTHERS = (("grey", "grey"), ("hidden", "hide"), ("unchanged", "keep"))
OTHER_COLOURS = (("grey", "grey"), ("darker", "darken"), ("unchanged colour", "keep"), ("hidden", "hide"))


class PartsPanel(QtW.QWidget):
    """The parts of the movie in the Movie tab: one line each that opens to show its settings (one open at a time).
    The open line is the part selected on the timeline. Widgets look parts up by id, never by position."""

    def __init__(self, win):
        super().__init__(); self.win = win; self.rows = {}; self.open_id = None; self._sig = None; self._filled = None
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
        why = self._problems().get(p["id"])
        if why == "structure not loaded":
            return "structure not loaded, so it is left out; choose another structure or load it again"
        if why:
            return f"{why}, so it is left out"
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
            self.refresh_lines()
            # its boxes may show values that a timeline drag, undo or threshold changed; 3.6: made again only then, so
            # an edit elsewhere (ticking another part, an arrow) does not empty and refill them
            if self.open_id in self.rows and self._filled != (self.open_id, self._detail_key(self.open_id)):
                self.fill_details(self.open_id)
        sel = self.win.selected_part
        if sel in self.rows and sel != self.open_id:
            self.open(sel, select=False)
        elif sel not in self.rows and self.open_id is not None:
            self.close_open()

    def _detail_key(self, pid):
        """What the open part's settings show: the part, the structures' names and settings, the main view."""
        import json
        w = self.win; info = w.info or {}
        return json.dumps([self.part(pid), w.spec.get("classes"), w.spec.get("view"), [p["id"] for p in w.spec["shots"]],
                           bool(info.get("has_tomogram")), getattr(w, "multi_pick", None)], sort_keys=True, default=str)

    def rebuild(self):
        clear_layout(self.lay); self.rows = {}; keep = self.open_id; self.open_id = None; self._filled = None
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
            if p["type"] == "focus" and len(sb.target_items(p.get("target"))) > 1:
                menu.addAction("Make a close-up for each", lambda pid=pid: self.win.editor.split_closeup(pid))
            menu.addSeparator(); menu.addAction("Delete", lambda pid=pid: self.win.editor.delete(pid))
            stripe = QtW.QFrame(); stripe.setFixedWidth(5); stripe.setObjectName("stripe")  # 3.6: as on the timeline
            stripe.setStyleSheet(f"QFrame#stripe {{ background: {part_colour(p)}; border-radius: 2px }}")
            stripe.setToolTip(f"All {TYPE_NAMES.get(p['type'], 'parts of this kind')} have this colour, here and on the timeline")
            for w, stretch in ((stripe, 0), (arrow, 0), (tick, 0), (num, 0), (name, 1), (dur, 0), (more, 0)):
                h.addWidget(w, stretch)
            arrow.clicked.connect(lambda _=False, pid=pid: self.toggle(pid)); head.clicked.connect(lambda pid=pid: self.toggle(pid))
            tick.toggled.connect(lambda on, pid=pid: self.set_on(pid, on))
            note = QtW.QLabel(""); note.setStyleSheet(DIM); note.setWordWrap(True); note.setContentsMargins(46, 0, 0, 0)
            details = QtW.QWidget(); details.setVisible(False); dl = QtW.QFormLayout(details); dl.setContentsMargins(24, 2, 0, 4)
            v.addWidget(head); v.addWidget(note); v.addWidget(details)
            self.lay.addWidget(box)
            self.rows[pid] = dict(box=box, head=head, stripe=stripe, arrow=arrow, on=tick, num=num, name=name, dur=dur, note=note,
                                  details=details, form=dl)
            self.refresh_line(pid)
        if not self.win.spec["shots"]:
            lab = QtW.QLabel("The movie has no parts. Add one with + Add part."); lab.setStyleSheet(DIM); self.lay.addWidget(lab)
        if keep in self.rows:
            self.open(keep, select=False)
        self.win.apply_help()

    def refresh_lines(self):
        """Every line's name, length and note, without touching any box (also after each new plan)."""
        for pid in self.rows:
            self.refresh_line(pid)

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
            self.win.end_picks(); self.close_open(); self.win.selected_part = None; self.win.timeline.set_selected(None)
        else:
            self.open(pid)

    def close_open(self):
        self._filled = None
        r = self.rows.get(self.open_id)
        if r:
            clear_layout(r["form"]); r["details"].setVisible(False); r["arrow"].setArrowType(QtC.Qt.RightArrow)
            r["box"].setStyleSheet("")
        self.open_id = None

    def open(self, pid, select=True):
        """Open one line (closing the others); select=True also selects the part on the timeline and shows it."""
        if pid not in self.rows:
            return
        self.win.end_picks(keep=pid)
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
            w.ensureWidgetVisible(r["box"], 0, 20); w.horizontalScrollBar().setValue(0)

    # ---- settings ----
    def set_on(self, pid, on):
        # 3.6: ticking a part no longer opens it (the open part's settings stayed put only when it was that part)
        edits.set_part(self.win.spec, pid, on=bool(on)); self.win.editor.commit()

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
        elif self._filled is not None and self._filled[0] == pid:
            self._filled = (pid, self._detail_key(pid))  # the box that changed already shows the new value

    def fill_details(self, pid):
        """Make the settings of one part (again)."""
        self._fill_details(pid)
        if self.part(pid) is not None:
            self._filled = (pid, self._detail_key(pid))  # after the rows: making them may tidy the part

    def _fill_details(self, pid):
        r, p = self.rows[pid], self.part(pid)
        form = r["form"]; clear_layout(form)
        QtC.QTimer.singleShot(0, self.win.apply_help)  # tooltips for the new rows
        add = form.addRow
        t = p["type"]
        if t == "slide":
            self._slide_rows(pid, p, form)
            self._view_rows(pid, p, form)
        elif t == "overview":
            add("Camera movement", _combo(MOTIONS, p.get("motion", "rock"), lambda v: self.set(pid, "motion", v, rebuild=True)))
            if p.get("motion", "rock") == "rock":
                add("Rock angle", _spin(5, 90, float(p.get("angle", 25)), lambda v: self.set(pid, "angle", v), step=5, suffix="\u00b0", decimals=0))
            if p.get("motion", "rock") in ("rock", "spin"):
                ax = _combo(sb.SPIN_AXES, p.get("spin_axis", "z"), lambda v: self.set(pid, "spin_axis", None if v == "z" else v))
                ax.setToolTip("The axis the camera turns around: Z (as seen from above, the usual turn), X or Y (the volume "
                              "tumbles, showing its top, side and bottom), or the screen's up-down line through the centre "
                              "(the volume turns around itself however the view is tilted or rotated)")
                add("Turn around", ax)
                cen = _combo(sb.CENTRES, p.get("centre") or "volume", lambda v: self.set(pid, "centre", None if v == "volume" else v))
                cen.setToolTip("Spin turns the camera around this axis. If the centre is far from what is shown, things seem "
                               "to circle around an empty point. 'Middle of what is shown' is the middle of the clip box "
                               "(also when the view is not fitted to it).")
                add("Centre of the turn", cen)
                steady = QtW.QCheckBox("Keep the size steady during the turn"); steady.setChecked(bool(p.get("steady_zoom")))
                steady.setToolTip("The camera keeps one distance for the whole turn, so the tomogram does not swing closer "
                                  "and further away (it is a little smaller, so no corner leaves the picture)")
                steady.toggled.connect(lambda on: self.set(pid, "steady_zoom", bool(on) or None))
                gentle = QtW.QCheckBox("Start gently"); gentle.setChecked(p.get("ease_start") in ("gentle", "very_gentle"))
                gentle.setToolTip("The turn starts slowly instead of at full speed")
                gentle.toggled.connect(lambda on: self.set(pid, "ease_start", "gentle" if on else None))
                add("", _hrow(steady, gentle))
            self._view_rows(pid, p, form)
        elif t == "hold":
            self._view_rows(pid, p, form)
        elif t == "focus":
            self._focus_rows(pid, p, form)
            self._lock_row(pid, p, form)
        elif t == "card":
            from .gui_cards import card_rows
            card_rows(self, pid, p, form)
        elif t == "path":
            from .gui_paths import path_rows
            path_rows(self, pid, p, form)
            self._lock_row(pid, p, form)
        add("Duration", _spin(0.2, 120, float(p.get("seconds", 5)), lambda v: self.set(pid, "seconds", v)))
        cap = QtW.QLineEdit(p.get("caption", "")); cap.setPlaceholderText("text shown at the bottom while this part plays")
        cap.textChanged.connect(lambda txt: self.set(pid, "caption", txt)); add("Caption", cap); r["caption"] = cap
        from .gui_overlays import part_overlay_row
        ovr = part_overlay_row(self.win, pid, p); r["overlays"] = ovr
        if t == "card":
            add("On the picture", ovr); self._fold_more(t, form); return
        tomo = self.win._tomo_row(p); add("", tomo); r["tomo"] = tomo
        add("On the picture", ovr)
        if t in ("slide", "overview", "focus"):
            add("Slow down at the end", _combo(ENDS, p.get("ease_end", "sudden"), lambda v: self.set(pid, "ease_end", v)))
        self._fold_more(t, form)
        self._effects_rows(pid, p, form)
        self._transition_rows(pid, p, form)

    EFFECT_ROWS = (("Look", "style"), ("Soft shadows", "shadow"), ("Outlines", "outline"), ("Depth fade", "depth_fade"), ("Membranes", "membranes"),
                   ("Smoother membranes", "soften"), ("Shadows on the slice", "tomo_shadow"))

    def _effects_rows(self, pid, p, form):
        """Effects in this part only (3.5): shadows, outlines, depth fade, membranes. "As in the Look tab" = the whole movie."""
        own = sb.part_look(p); n = len(own)
        fd = Fold(f"Look and effects in this part ({n} changed)" if n else "Look and effects in this part", key="part_effects",
                  tip="A look of its own for this part (for example Ceramic in the overview and see-through membranes in a "
                      "close-up), or effects on or off in this part only. 'As in the Look tab' follows the Look tab, which "
                      "sets them for the whole movie. Changes blend in smoothly at the start of the part.")

        combos = {}

        def choose(name, v, also=None):
            q = self.part(pid)
            if q is None:
                return
            cur = sb.part_look(q)
            for k, val in [(name, v)] + list((also or {}).items()):
                if val is None:
                    cur.pop(k, None)
                else:
                    cur[k] = val
                if k in combos:  # keep a combo that changed along in step
                    ch = sb.PART_LOOK[k]
                    combos[k].setCurrentIndex(next((i for i, (_, d) in enumerate(ch) if d == val and (val is not None or i == 0)), 0))
            self.set(pid, "look", cur or None)
            m = len(cur); fd._text = f"Look and effects in this part ({m} changed)" if m else "Look and effects in this part"
            fd.button.setText(fd._text)
        for label, name in self.EFFECT_ROWS:
            cb = QtW.QComboBox(); cb.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon); cb.setMinimumContentsLength(8)
            choices = sb.PART_LOOK[name]
            for t, d in choices:
                cb.addItem(t)
            cur = own.get(name)
            cb.setCurrentIndex(next((i for i, (_, d) in enumerate(choices) if d == cur and (cur is not None or i == 0)), 0))
            cb.activated.connect(lambda j, name=name, choices=choices: choose(name, choices[j][1])); combos[name] = cb
            cb.setToolTip("A whole look for this part only (the background stays the movie's); the rows below can still "
                          "change single effects" if name == "style" else
                          "Smoother surfaces of the membranes (structures ticked 'Treat as membrane') in this part only. "
                          "Thin parts are kept. The first time, the smoother surfaces take a few seconds to make" if name == "soften" else
                          f"{label} in this part: as in the Look tab (the whole movie), or its own choice")
            fd.form.addRow(label, cb)
            if name == "outline":
                fd.form.addRow("Outline these", self._outline_only_button(pid, choose))
        form.addRow(fd)

    def _outline_only_button(self, pid, choose):
        """Which structures get an outline in this part: every one (as in the Structures panel) or the ticked ones only."""
        b = QtW.QToolButton(); b.setPopupMode(QtW.QToolButton.InstantPopup)
        b.setToolButtonStyle(QtC.Qt.ToolButtonTextOnly); menu = QtW.QMenu(b); b.setMenu(menu)
        b.setToolTip("Outlines on some structures only, in this part: tick the ones to outline. 'Every structure' follows the "
                     "outline boxes in the Structures panel. If outlines are off, picking structures turns them on (medium) "
                     "in this part.")

        def structures():
            return [c for c in (self.win.info or {}).get("structures", []) if c.get("mesh")]

        def label():
            only = sb.part_look(self.part(pid) or {}).get(sb.OUTLINE_ONLY)
            if only is None:
                text = "every structure"
            elif not only:
                text = "none"
            else:
                names = [self._name(k) for k in only]
                text = ", ".join(names[:2]) + (f" and {len(names) - 2} more" if len(names) > 2 else "")
            b.setText(text + " \u25be")

        def outlines_off(own):
            from .look import LOOK_PRESETS, style35
            if own.get("outline"):
                return own["outline"] == "off"
            if own.get("style") in LOOK_PRESETS:
                return LOOK_PRESETS[own["style"]].get("outline", "off") == "off"
            return style35(self.win.spec).get("outline", "off") == "off"

        def put(only):
            q = self.part(pid)
            if q is None:
                return
            own = sb.part_look(q)
            also = {"outline": "medium"} if only is not None and outlines_off(own) else None  # picked structures should show
            choose(sb.OUTLINE_ONLY, only, also)
            label()

        def toggle(key, on):
            own = sb.part_look(self.part(pid) or {}); only = own.get(sb.OUTLINE_ONLY)
            if only is None:  # start from what the Structures panel outlines
                only = [c["key"] for c in structures() if not (self.win.spec.get("classes", {}).get(c["key"]) or {}).get("no_outline")]
            only = [k for k in only if k != key] + ([key] if on else [])
            put(only)

        def fill():
            menu.clear(); only = sb.part_look(self.part(pid) or {}).get(sb.OUTLINE_ONLY)
            a = menu.addAction("Every structure (as in the Structures panel)"); a.setCheckable(True); a.setChecked(only is None)
            a.triggered.connect(lambda _=False: put(None))
            menu.addSeparator()
            for c in structures():
                on = (c["key"] in only) if only is not None else not (self.win.spec.get("classes", {}).get(c["key"]) or {}).get("no_outline")
                a = menu.addAction(self._name(c["key"])); a.setCheckable(True); a.setChecked(on)
                a.toggled.connect(lambda v, key=c["key"]: toggle(key, v))
        menu.aboutToShow.connect(fill); label()
        return b

    def _fold_more(self, t, form):
        """Put the less used rows of a part under "More settings" (closed unless the user opened it before)."""
        fd = Fold("More settings", key=f"part_more_{t}", tip="Captions, what is shown on the picture, and finer settings of this part")
        if fold_rows(form, MORE_ROWS.get(t, ()), fd):
            form.addRow(fd)
        else:
            fd.deleteLater()

    def transition_heading(self, pid):
        """"Transition from part 3 to part 4" (positions among all parts, as numbered on the timeline)."""
        shots = self.win.spec["shots"]; ids = [q["id"] for q in shots]
        if pid not in ids:
            return "Transition"
        i = ids.index(pid); prev = next((j for j in range(i - 1, -1, -1) if shots[j].get("on", True)), None)
        return f"Transition from part {prev + 1} to part {i + 1}" if prev is not None else f"Transition into part {i + 1} (from the start)"

    def _transition_rows(self, pid, p, form):
        """The transition into this part (3.5): kind, length, how it leaves and arrives, overlaps, straight or arc."""
        r = self.rows[pid]; tin = transition_of(p)

        def set_tr(**kw):
            q = self.part(pid)
            if q is not None:
                cur = transition_of(q); cur.update(kw)
                self.set(pid, "transition_in", cur); pic.setPixmap(speed_picture(cur))
        head = QtW.QLabel(f"<b>{self.transition_heading(pid)}</b>"); r["transition_head"] = head
        fd = Fold("", key="part_transition", header=head, tip="How the movie gets from the previous part to this one")
        form.addRow(fd); form = fd.form
        trs = p.get("transition")
        secs = _spin(-0.1, 10, -0.1 if trs is None else float(trs), lambda v: self.set(pid, "transition", None if v < 0 else v),
                     step=0.1, special="automatic")
        secs.setToolTip("Seconds of the transition into this part; 0 cuts straight to it")
        kind = _combo(TRANSITION_KINDS, tin["kind"], lambda v: set_tr(kind=v))
        kind.setToolTip("Camera flight, a cut, a cross-dissolve, or a fade through black or white")
        form.addRow("Kind", _hrow(kind, secs))
        pic = QtW.QLabel(); pic.setPixmap(speed_picture(tin)); pic.setToolTip("Speed during the transition: high where the curve is high")
        leave = _combo(EASE_CHOICES, tin["ease_out_prev"], lambda v: set_tr(ease_out_prev=v)); leave.setToolTip("How gently the camera leaves the previous part")
        arrive = _combo(EASE_CHOICES, tin["ease_in_next"], lambda v: set_tr(ease_in_next=v)); arrive.setToolTip("How gently the camera arrives at this part")
        form.addRow("Leave / arrive", _hrow(leave, arrive, pic))
        op = _spin(0, 10, tin["overlap_prev"], lambda v: set_tr(overlap_prev=v), step=0.1)
        op.setPrefix("before "); op.setToolTip("Start the transition this many seconds before the previous part ends (its motion goes on meanwhile)")
        on = _spin(0, 10, tin["overlap_next"], lambda v: set_tr(overlap_next=v), step=0.1)
        on.setPrefix("into "); on.setToolTip("This part's own motion already starts this many seconds before the transition ends")
        form.addRow("Overlap", _hrow(op, on))
        path = _combo((("straight", "straight"), ("arc (a little higher in the middle)", "arc")), tin["path"], lambda v: set_tr(path=v))
        path.setToolTip("Fly straight, or a little higher in the middle (for long moves)")
        form.addRow("Flight path", path)
        r["transition_rows"] = (head, kind, secs, leave, arrive, op, on, path)

    def _lock_row(self, pid, p, form):
        """Lock the view of this part: it keeps its camera however the preview is moved, and the preview cannot move it."""
        cb = QtW.QCheckBox("Lock the view of this part"); cb.setChecked(bool(p.get("locked")))
        cb.setToolTip("The part keeps the camera position it has now, whatever happens to the main view, and moving around in "
                      "the preview cannot change it by accident while the playhead is in this part. Untick to change it again.")
        cb.toggled.connect(lambda on: self.lock(pid, on))
        form.addRow("", cb)

    def lock(self, pid, on):
        """Lock: a part that follows the main view gets that view as its own first, so it stays as it looks now."""
        p = self.part(pid)
        if p is None:
            return
        main = self.win.spec["view"]
        if on:
            if p["type"] in ("overview", "slide", "hold") and not p.get("view"):
                p["view"] = dict(tilt=float(main["tilt"]), az=float(main["az"]), zoom=float(main.get("zoom", 1.0)),
                                 **({"pan": list(main["pan"])} if main.get("pan") else {}))
            elif p["type"] == "focus":
                v = p.get("view") if isinstance(p.get("view"), dict) else {}
                p["view"] = dict(v, az=float(v.get("az", main["az"])))  # the angle it is seen from stays
                if p.get("tilt") is None:
                    p["tilt"] = float(max(float(main.get("tilt", 42)), 50))
            p["locked"] = True
        else:
            p.pop("locked", None)
        self.win.editor.commit(select=pid)  # (its settings are made again by sync: the part changed)
        self.win.say("The view of this part is locked." if on else "The view of this part can be changed again.", "ok")

    def _view_rows(self, pid, p, form):
        """View: the main view, or this part's own camera position (Tilt, Rotate, Zoom), and the lock."""
        own = bool(p.get("view")); locked = bool(p.get("locked"))
        main = self.win.spec["view"]

        def choose(v):
            self.set(pid, "view", dict(tilt=float(main["tilt"]), az=float(main["az"]), zoom=float(main["zoom"])) if v == "own" else None,
                     rebuild=True)
        vc = _combo((("main view", "main"), ("own view", "own")), "own" if own else "main", choose)
        vc.setToolTip("The main view, or a camera position of this part's own (for example one slice seen from the top and "
                      "zoomed in). With its own view, dragging, scrolling and right-dragging in the preview change it while "
                      "this part is selected and the playhead is in it.")
        vc.setEnabled(not locked)
        form.addRow("View", vc)
        self._lock_row(pid, p, form)
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
        form.addRow("Zoom", slider("zoom", 30, 800, 100, lambda x: f"{x / 100:.2f}x"))
        for s, val, _, _ in self.view_sliders.values():
            s.setEnabled(not locked)

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
        self.win.editor.commit(select=pid)  # (its settings are made again by sync: the part changed)

    def _focus_rows(self, pid, p, form):
        win = self.win; ds = win.st.ds
        tgt = p.get("target") or {}; items = sb.target_items(tgt)
        if "items" not in tgt and len(items) <= 1:  # one structure or one object, as before
            self._single_target_rows(pid, p, form)
        else:
            form.addRow("Objects in focus", self._items_list(pid, p))
        form.addRow("", self._focus_buttons(pid, p))
        oc, oo = sb.others_choice(p.get("others", "grey"))
        orow = QtW.QWidget(); h = QtW.QHBoxLayout(orow); h.setContentsMargins(0, 0, 0, 0)
        op = QtW.QSpinBox(); op.setRange(0, 100); op.setSuffix(" %"); op.setPrefix("opacity "); op.setValue(round(100 * oo))
        op.setToolTip("How visible the other structures stay; 0 % hides them"); op.setEnabled(oc != "hide")

        def colour(v):
            op.setEnabled(v != "hide"); self.set(pid, "others", sb.set_others_choice(self.part(pid).get("others"), colour=v))
        col = _combo(OTHER_COLOURS, oc, colour)
        col.setToolTip("The structures that are not in focus: grey, darker, in their own colour, or hidden")
        op.valueChanged.connect(lambda v: self.set(pid, "others", sb.set_others_choice(self.part(pid).get("others"), opacity=v / 100)))
        h.addWidget(col); h.addWidget(op); h.addStretch(1)
        form.addRow("Other structures", orow)
        each = self._each_rows(pid, p)
        if each is not None:
            form.addRow(each)
        form.addRow("Camera movement", _combo(FOCUS_MOTIONS, p.get("motion", "orbit"), lambda v: self.set(pid, "motion", v, rebuild=True)))
        self._animation_rows(pid, p, form)
        form.addRow("Zoom", _spin(0.3, 4, float(p.get("zoom", 1)), lambda v: self.set(pid, "zoom", v), step=0.1, suffix=" x"))
        form.addRow("Tilt", _spin(-1, 85, float(p["tilt"]) if p.get("tilt") is not None else -1,
                                  lambda v: self.set(pid, "tilt", None if v < 0 else v), step=5, suffix="\u00b0", decimals=0,
                                  special="automatic"))
        steady = QtW.QCheckBox("Keep zoom steady"); steady.setChecked(bool(p.get("steady_zoom")))
        steady.setToolTip("The camera keeps the same distance while it moves, so the object does not grow and shrink")
        steady.toggled.connect(lambda on: self.set(pid, "steady_zoom", bool(on) or None))
        form.addRow("", steady)
        form.addRow("Vignette", self._vignette_row(pid, p))

    def _fx(self, pid, key, rebuild=False, **kw):
        q = self.part(pid)
        if q is not None:
            cur = dict(fx_settings(q)[key]); cur.update(kw); self.set(pid, key, cur, rebuild=rebuild)

    def _animation_rows(self, pid, p, form):
        """Wiggle amounts, push-in, highlight and object effect of a close-up (they can be combined)."""
        S = fx_settings(p)

        def row(*ws):
            w = QtW.QWidget(); h = QtW.QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
            for x in ws:
                h.addWidget(x)
            h.addStretch(1)
            return w
        if p.get("motion") == "wiggle":
            w = S["wiggle"]
            amp = _spin(1, 30, float(w["amplitude"]), lambda v: self._fx(pid, "wiggle", amplitude=v), step=1, suffix="\u00b0", decimals=0)
            amp.setToolTip("How far the camera swings around the object")
            if w.get("smooth"):  # 3.5: speed as Slow - Fast; whole cycles are fitted to the close-up
                fq = QtW.QSlider(QtC.Qt.Horizontal); fq.setRange(15, 80); fq.setFixedWidth(90); fq.setValue(round(100 * float(w["frequency"])))
                fq.setToolTip("Slow (left) to fast (right). The speed is adjusted so the camera ends where it began.")
                fq.valueChanged.connect(lambda v: self._fx(pid, "wiggle", frequency=v / 100))
                speed = row(QtW.QLabel("slow"), fq, QtW.QLabel("fast"))
                shapes = (("circle", "circle"), ("figure eight", "eight"), ("drift (slow, never repeats)", "drift"))
            else:  # a close-up from an older project: as it was
                speed = _spin(0.1, 2, float(w["frequency"]), lambda v: self._fx(pid, "wiggle", frequency=v), step=0.1, suffix=" per second", decimals=1)
                shapes = (("circle", "circle"), ("figure eight", "eight"))
            shp = _combo(shapes, w.get("shape", "circle"), lambda v: self._fx(pid, "wiggle", shape=v))
            form.addRow("Wiggle", row(amp, shp)); form.addRow("Wiggle speed", speed)
        pu = S["push"]
        on = QtW.QCheckBox("push in"); on.setChecked(bool(pu["on"])); on.setToolTip("The camera moves slowly closer during the close-up")
        on.toggled.connect(lambda v: self._fx(pid, "push", rebuild=True, on=bool(v)))
        ws = [on]
        if pu["on"]:
            ws += [_spin(0.5, 3, float(pu["start"]), lambda v: self._fx(pid, "push", start=v), step=0.05, suffix=" x \u2192", decimals=2),
                   _spin(0.5, 3, float(pu["end"]), lambda v: self._fx(pid, "push", end=v), step=0.05, suffix=" x", decimals=2)]
        form.addRow("Camera push-in", row(*ws))
        hl = S["highlight"]
        hk = _combo(HIGHLIGHTS, hl["kind"], lambda v: self._fx(pid, "highlight", rebuild=True, kind=v))
        ws = [hk]
        if hl["kind"] != "none":
            n = QtW.QSpinBox(); n.setRange(1, 10); n.setValue(int(hl.get("pulses", 2))); n.setSuffix(" times")
            n.valueChanged.connect(lambda v: self._fx(pid, "highlight", pulses=int(v))); ws.append(n)
        form.addRow("Highlight", row(*ws))
        ef = S["effect"]
        ek = _combo(EFFECTS, ef["kind"], lambda v: self._fx(pid, "effect", rebuild=True, kind=v))
        ek.setToolTip("Cross-section: a cut moves through the objects and shows their inside. Grow in: the objects appear gradually.")
        ws = [ek]
        if ef["kind"] == "section":
            ws.append(_combo(SECTION_DIRS, ef.get("direction", "slices"), lambda v: self._fx(pid, "effect", rebuild=True, direction=v)))
            ws.append(_combo((("whole close-up", "whole"), ("halfway, then hold", "half")), ef.get("sweep", "whole"),
                             lambda v: self._fx(pid, "effect", sweep=v)))
        elif ef["kind"] == "grow":
            ws.append(_combo(GROW_HOW, ef.get("how", "bottom"), lambda v: self._fx(pid, "effect", how=v)))
        form.addRow("Object effect", row(*ws))
        if ef["kind"] == "section" and ef.get("direction", "slices") == "slices":
            mv = QtW.QCheckBox("move the tomogram slice with the cut"); mv.setChecked(bool(ef.get("move_slice", True)))
            mv.setToolTip("The slice follows the cut, so you see the density inside the object exactly where it is cut")
            mv.toggled.connect(lambda v: self._fx(pid, "effect", move_slice=bool(v))); form.addRow("", mv)

    def _vignette_row(self, pid, p):
        """As in the Look tab, or this close-up's own vignette (strength) and background blur."""
        v = p.get("vignette") if isinstance(p.get("vignette"), dict) else None
        w = QtW.QWidget(); h = QtW.QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
        mode = _combo((("as in the Look tab", "default"), ("on", "on"), ("off", "off")),
                      "default" if v is None else ("on" if v.get("on", True) else "off"),
                      lambda m: self.set(pid, "vignette", None if m == "default" else dict(self.part(pid).get("vignette") or {}, on=m == "on"),
                                         rebuild=True))
        mode.setToolTip("Darkens the edges of the picture around the objects in focus")
        h.addWidget(mode)
        if v is not None and v.get("on", True):
            st = QtW.QSpinBox(); st.setRange(0, 100); st.setSuffix(" %"); st.setPrefix("strength "); st.setValue(round(100 * float(v.get("strength", 0.6))))
            st.valueChanged.connect(lambda x: self.set(pid, "vignette", dict(self.part(pid)["vignette"], strength=x / 100)))
            bl = QtW.QCheckBox("blur"); bl.setChecked(bool((v.get("blur") or {}).get("on")))
            bl.setToolTip("Blur the background: the objects in focus stay sharp, things in front of them and behind them are blurred "
                          "(amount: Look tab ▸ Vignette)")
            bl.toggled.connect(lambda on: self.set(pid, "vignette", dict(self.part(pid)["vignette"],
                                                                         blur=dict((self.part(pid)["vignette"].get("blur") or {}), on=bool(on)))))
            h.addWidget(st); h.addWidget(bl)
        h.addStretch(1)
        return w

    def _each_rows(self, pid, p):
        """Each structure in this close-up (3.5): as set above, see-through or hidden, for this part only."""
        structs = [c for c in (self.win.info or {}).get("structures", []) if c.get("mesh")]
        if not structs:
            return None
        each = sb.each_of(p); n = len(each)
        fd = Fold(f"Each structure ({n} changed)" if n else "Each structure", key="part_each",
                  tip="Make single structures see-through or hide them in this close-up only, for example to see inside a "
                      "membrane or to clear what is in the way. The rest follow 'Other structures'.")
        spec = self.win.spec

        def choose(key, v):
            q = self.part(pid)
            if q is None:
                return
            cur = sb.each_of(q)
            if v:
                cur[key] = v
            else:
                cur.pop(key, None)
            self.set(pid, "each", cur or None)
            m = len(cur); fd._text = f"Each structure ({m} changed)" if m else "Each structure"; fd.button.setText(fd._text)
        for c in structs:
            cs = spec["classes"].get(c["key"], {})
            pm = QtG.QPixmap(12, 12); pm.fill(QtG.QColor(cs.get("color", c["color"])))
            lab = QtW.QLabel(); lab.setPixmap(pm)
            name = QtW.QLabel(cs.get("name", c["name"])); name.setToolTip(c.get("source_title", "") or name.text())
            cb = _combo(EACH_CHOICES, each.get(c["key"], ""), lambda v, key=c["key"]: choose(key, v))
            cb.setToolTip("As set above, see-through, or hidden, in this close-up only")
            fd.form.addRow(_hrow(lab, name), cb)
        return fd

    def _name(self, key):
        ds, spec = self.win.st.ds, self.win.spec; k = ds.id_of(key)
        return spec["classes"].get(key, {}).get("name") or (ds.classes[k]["name"] if k is not None else "structure")

    def set_items(self, pid, items):
        """New objects in focus; the caption follows unless the user wrote their own."""
        p = self.part(pid)
        if p is None:
            return
        old = sb.closeup_caption(sb.target_items(p.get("target")), self._name)
        p["target"] = sb.make_target(items)
        if not p.get("caption") or p.get("caption") == old:
            p["caption"] = sb.closeup_caption(items, self._name)
        self.win.editor.commit(select=pid)

    def _items_list(self, pid, p):
        ds = self.win.st.ds; w = QtW.QWidget(); v = QtW.QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(1)
        items = sb.target_items(p.get("target"))
        for i, (key, comp) in enumerate(items):
            k = ds.id_of(key); row = QtW.QHBoxLayout()
            pm = QtG.QPixmap(12, 12); pm.fill(QtG.QColor(self.win.spec["classes"].get(key, {}).get("color", "#888888")))
            sw = QtW.QLabel(); sw.setPixmap(pm); row.addWidget(sw)
            if k is None:
                text = f"{self._name(key)} (structure not loaded)"
            elif comp is None:
                text = f"{self._name(key)} (whole structure)"
            else:
                try:
                    n = int(ds.components(k)[2][int(comp)])
                except (IndexError, KeyError, TypeError):
                    n = 0
                text = f"one object of {self._name(key)}, {n:,} voxels"
            lab = QtW.QLabel(text); row.addWidget(lab, 1)
            rm = QtW.QToolButton(); rm.setText("\u2715"); rm.setToolTip("Take this out of the close-up")
            rm.clicked.connect(lambda _=False, i=i: self.set_items(pid, [it for j, it in enumerate(sb.target_items(self.part(pid)["target"])) if j != i]))
            row.addWidget(rm); v.addLayout(row)
        if not items:
            lab = QtW.QLabel("nothing yet: tick structures or click objects in the preview"); lab.setStyleSheet(DIM); v.addWidget(lab)
        return w

    def _focus_buttons(self, pid, p):
        """'Objects in focus' check list of whole structures, and picking objects in the preview."""
        w = QtW.QWidget(); h = QtW.QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
        b = QtW.QToolButton(); b.setText("Structures in focus \u25be"); b.setPopupMode(QtW.QToolButton.InstantPopup)
        b.setToolTip("Tick whole structures to show them together in this close-up")
        menu = QtW.QMenu(b); b.setMenu(menu)

        def fill():
            menu.clear(); items = sb.target_items(self.part(pid).get("target"))
            for c in (self.win.info or {}).get("structures", []):
                if not c.get("mesh"):
                    continue
                a = menu.addAction(self._name(c["key"])); a.setCheckable(True); a.setChecked((c["key"], None) in items)
                a.toggled.connect(lambda on, key=c["key"]: self.set_items(pid, [it for it in sb.target_items(self.part(pid)["target"])
                                                                                if it != (key, None)] + ([(key, None)] if on else [])))
        menu.aboutToShow.connect(fill)
        pick = QtW.QPushButton("+ Click objects in the preview"); pick.setCheckable(True); pick.setChecked(self.win.multi_pick == pid)
        pick.setToolTip("Click objects to add them; click again to take one out; Shift-click adds the whole structure. Esc ends.")
        pick.toggled.connect(lambda on: self.win.start_multi_pick(pid) if on else self.win.end_multi_pick())
        frame = QtW.QPushButton("Frame a region..."); frame.setToolTip(
            "Draw a frame in the preview: the close-up shows only what is inside it, for example where two objects touch.")
        frame.clicked.connect(lambda: self.win.start_region_pick(pid))
        h.addWidget(b); h.addWidget(pick); h.addStretch(1)
        out = QtW.QWidget(); v = QtW.QVBoxLayout(out); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(2); v.addWidget(w)
        h2 = QtW.QHBoxLayout(); h2.addWidget(frame)
        if sb.part_region(p) is not None:
            lab = QtW.QLabel("shows the frame you drew"); lab.setStyleSheet(DIM); h2.addWidget(lab)
            clr = QtW.QToolButton(); clr.setText("×"); clr.setToolTip("Forget the drawn frame: frame the objects in focus again")
            clr.clicked.connect(lambda: self.set(pid, "region", None, rebuild=True)); h2.addWidget(clr)
        h2.addStretch(1); v.addLayout(h2)
        return out

    def _single_target_rows(self, pid, p, form):
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
                self.win.editor.commit(select=pid)  # (its settings are made again by sync: the part changed)
            elif v == "pick":
                obj.setCurrentIndex(1 if tgt.get("component") is not None else 0)
                win.start_object_pick(pid, ds.key_of(k))
        obj.activated.connect(chose)
        form.addRow("Object", obj)
