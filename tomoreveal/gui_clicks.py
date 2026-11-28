"""Clicks in the preview that belong to a task: one click mode at a time.

A button or a menu starts a mode; the next click in the preview (or a frame drawn in it) goes to that mode. Starting a
mode ends the one before, whatever it was, and `ClickModes.end` is the only place that puts back the cursor, the frame
drawing, the buttons and the note."""
from PySide6 import QtCore as QtC

try:
    from shiboken6 import isValid as _alive
except ImportError:  # pragma: no cover
    def _alive(obj):
        return True


class Mode:
    """One way of clicking in the preview. `part`: the part it changes; `after`: where a new part goes (the playhead when
    that part is gone); `arrow`: the arrow or text it changes; `stays`: more clicks follow until Esc; `box`: a frame is
    drawn instead of a click; `main`: the preview shows the main view while it is on."""
    part = after = arrow = None
    stays = box = False
    main = True
    message = ""

    def text(self, win):
        return self.message

    def click(self, win, fx, fy):
        """Handle one click. A mode ends itself when its task is done (a miss keeps it on); return False to end it."""

    def boxed(self, win, box):
        pass

    def ended(self, win):
        pass


def _pick(win, fx, fy):
    """The structure under a click, or None (the reason is shown)."""
    try:
        p = win.st.pick_at(win.spec_for(), fx, fy)
    except Exception as exc:
        win.say(f"Could not pick: {exc}", "err"); return None
    if not p.get("found"):
        win.say("Nothing there. Click directly on a coloured structure.", "err"); return None
    return p


class NewCloseup(Mode):
    """'+ Click a structure in the preview': a new close-up of what is clicked, after `after`."""
    needs_segmentation = True
    message = "Click the structure you want a close-up of."

    def __init__(self, after=None):
        self.after = after

    def click(self, win, fx, fy):
        p = _pick(win, fx, fy)
        if p is None:
            return True
        extra = f" (one object, {p['voxels']:,} voxels)" if p.get("component") is not None else ""
        target = {"class": p["class"], "component": p["component"]} if p.get("component") is not None else {"class": p["class"]}
        win.clicks.end(); win.add_focus(target, p["name"], self.after)
        win.say(f"Added a close-up of {p['name']}{extra}.", "ok")


class CloseupObject(Mode):
    """'Pick an object in the preview...' of a close-up: the next click on that structure sets its object."""
    needs_segmentation = True

    def __init__(self, part, key):
        self.part, self.key = part, key

    def text(self, win):
        return f"Click one {win.spec['classes'].get(self.key, {}).get('name', 'structure')} object in the preview."

    def click(self, win, fx, fy):
        p = _pick(win, fx, fy)
        if p is None:
            return True
        if p["class"] != self.key or p.get("component") is None:
            win.say(f"That is not one {win.spec['classes'].get(self.key, {}).get('name', 'such')} object. Click one of them.", "err")
            return True
        win.clicks.end(); part = win.parts_panel.part(self.part)
        if part is not None:
            part["target"] = {"class": self.key, "component": p["component"]}
            win.editor.commit(select=self.part); win.parts_panel.fill_details(self.part)
            win.say(f"The close-up now shows one object ({p['voxels']:,} voxels).", "ok")


class CloseupObjects(Mode):
    """'+ Click objects in the preview' of a close-up: each click adds or removes one object, until Esc."""
    needs_segmentation = True; stays = True
    message = ("Click objects to add them to the close-up; click one again to take it out; Shift-click adds the whole "
               "structure. Press Esc when done.")

    def __init__(self, part):
        self.part = part

    def click(self, win, fx, fy):
        from PySide6 import QtWidgets as QtW
        from .storyboard import target_items
        p = _pick(win, fx, fy)
        if p is None:
            return True
        part = win.parts_panel.part(self.part)
        if part is None:
            return False
        whole = bool(QtW.QApplication.keyboardModifiers() & QtC.Qt.ShiftModifier) or p.get("component") is None
        it = (p["class"], None if whole else int(p["component"]))
        items = target_items(part.get("target"))
        items = [x for x in items if x != it] if it in items else items + [it]
        win.parts_panel.set_items(self.part, items)
        win.req = win._main_req(); win.schedule(0)
        win.say(f"{len(items)} in focus. Click more, or press Esc when done.", "ok")
        return True

    def ended(self, win):
        win.req = {"at": {"part": self.part, "u": 0.5}}; win.schedule(0)  # the close-up again


class PathPoints(Mode):
    """'Add point by clicking in the preview' of a camera path: each click adds a point at its end, until Esc."""
    stays = True; main = False
    message = "Click a structure or the slice to add a point. Press Esc when done."

    def __init__(self, part):
        self.part = part

    def click(self, win, fx, fy):
        pt = win.st.ren.pick_point(fx, fy) if win.st.ren is not None else None
        part = win.parts_panel.part(self.part)
        if pt is None or part is None:
            win.say("Nothing there. Click on a structure or on the slice.", "err"); return True
        ws = list(part.get("waypoints") or []); ws.append(dict(point=[float(v) for v in pt], pause=0.0))
        part["waypoints"] = ws; win.editor.commit(select=self.part)
        win.say(f"Point {len(ws)} added. Click more, or press Esc when done.", "ok")
        return True


class PathObject(Mode):
    """'one object I click in the preview...' of a camera path: the camera follows the object clicked."""
    needs_segmentation = True
    message = "Click the object the camera should follow."

    def __init__(self, part):
        self.part = part

    def click(self, win, fx, fy):
        p = win.st.pick_at(win.spec_for(), fx, fy); part = win.parts_panel.part(self.part)
        if not p.get("found") or part is None:
            win.say("Nothing there. Click directly on a coloured structure.", "err"); return True
        part["source"] = dict(kind="follow", items=[{"structure": p["class"], "component": p.get("component")}])
        win.clicks.end(); win.editor.commit(select=self.part, show=self.part); win.say("The camera now follows that object.", "ok")


class Region(Mode):
    """'Frame a region...': a frame drawn in the preview; the close-up `part` (or a new one after `after`) shows what is inside."""
    box = True; main = False
    message = "Draw a frame around what the close-up should show: press the mouse button and drag. Esc cancels."

    def __init__(self, part=None, after=None):
        self.part, self.after = part, (after if part is None else None)

    def boxed(self, win, box):
        win.clicks.end()
        if win.st.ren is None or win.gen is not None:
            return
        box = win.st.ren.region_box(*box)  # in the picture on screen
        if box is None:
            win.say("There is nothing inside that frame. Draw it around structures or the slice.", "err"); return
        region = dict(lo=[round(float(v), 2) for v in box[0]], hi=[round(float(v), 2) for v in box[1]])
        if self.part is None:
            from .storyboard import make_target, new_part
            part = new_part("focus", target=make_target([]), others=dict(colour="keep", opacity=1.0), region=region,
                            motion="wiggle", caption="")
            win.editor.insert(part, self.after)
            win.say("Added a close-up of the frame you drew.", "ok")
        else:
            part = win.parts_panel.part(self.part)
            if part is None:
                return
            part["region"] = region; win.editor.commit(select=self.part, show=self.part)
            win.say("The close-up now shows the frame you drew.", "ok")


class NewArrow(Mode):
    """'+ Add arrow': the next click is where the new arrow points."""
    main = False
    message = "Click the spot the arrow should point to."

    def click(self, win, fx, fy):
        from .gui_annotations import add_arrow_at
        win.clicks.end(); add_arrow_at(win, (fx, fy))


class MorePointers(Mode):
    """'Point at more things': each click adds one more thing an arrow or text points at, until a click on empty space."""
    stays = True; main = False
    message = "Click each thing it should also point at. Click on empty space when done."

    def __init__(self, arrow):
        self.arrow = arrow

    def click(self, win, fx, fy):
        from .gui_annotations import add_pointer_at
        if add_pointer_at(win, self.arrow, (fx, fy)):
            return True
        win.clicks.end(); win.say("Done.", "ok")
        return True


class ClickModes(QtC.QObject):
    """The window's click mode: `mode` is the active one, or None."""
    changed = QtC.Signal()

    def __init__(self, win):
        super().__init__(win); self.win = win; self.mode = None; self._buttons = []

    def start(self, mode):
        """Start `mode`, ending the one before."""
        self.end()
        w = self.win; self.mode = mode; mode.message = mode.text(w); mode.selected = w.selected_part
        w.prev.setCursor(QtC.Qt.CrossCursor); w.prev.box_mode = mode.box; w.prev._box = None
        self._sync()
        if mode.main and w.spec:
            w.req = w._main_req(); w.schedule(0)
        w.say(mode.message)

    def end(self):
        """End the active mode, if any: the cursor, the frame drawing, the buttons and the note go back."""
        m, self.mode = self.mode, None
        if m is None:
            self._sync(); return  # (a button pressed when no mode could start goes back)
        w = self.win
        w.prev.setCursor(QtC.Qt.ArrowCursor); w.prev.box_mode = False; w.prev._box = None; w.prev.update()
        self._sync()
        if m.stays or getattr(w, "_msg", (None,))[0] == m.message:  # its instructions are no longer true
            w.say("")
        m.ended(w)

    def end_for(self, keep=None):
        """Part `keep` (or none) is selected or opened: a mode for another part ends, and a mode for an arrow ends when
        another part than the one selected at its start is chosen. Modes for something new stay."""
        m = self.mode
        if m is not None and (m.part is not None and m.part != keep or m.arrow is not None and keep != m.selected):
            self.end()

    def check(self):
        """After a change, undo or redo: a mode whose part or arrow is gone ends, and so does adding points to a path
        that no longer goes through points."""
        m, w = self.mode, self.win
        if m is None or w.spec is None:
            return
        if m.part is not None:
            p = w.parts_panel.part(m.part)
            if p is None or isinstance(m, PathPoints) and (p.get("source") or {}).get("kind") != "points":
                self.end()
        elif m.arrow is not None and not any(a["id"] == m.arrow for a in w.spec.get("annotations") or []):
            self.end()

    def escape(self):
        """Esc: end the active mode. False when there was none."""
        if self.mode is None:
            return False
        self.end(); return True

    def click(self, fx, fy):
        """A click in the preview. True when a mode took it."""
        m, w = self.mode, self.win
        if m is None:
            return False
        if m.box:  # (a frame is drawn, not clicked)
            return True
        if w.gen is not None or (getattr(m, "needs_segmentation", False) and not w.st.has_segmentation):
            return True
        if m.click(w, fx, fy) is False and self.mode is m:
            self.end()
        return True

    def boxed(self, fx0, fy0, fx1, fy1):
        m = self.mode
        if m is None or not m.box:
            self.end(); return
        m.boxed(self.win, (fx0, fy0, fx1, fy1))
        if self.mode is m:
            self.end()

    # ---- buttons that show a mode ----
    def follow(self, button, test):
        """A checkable button shows whether `test(mode)` holds; it is set with its signals blocked."""
        self._buttons.append((button, test)); self._set(button, test)

    def _set(self, button, test):
        button.blockSignals(True); button.setChecked(bool(self.mode is not None and test(self.mode))); button.blockSignals(False)

    def _sync(self):
        self._buttons = [(b, t) for b, t in self._buttons if _alive(b)]
        for b, t in self._buttons:
            self._set(b, t)
        self.changed.emit()
