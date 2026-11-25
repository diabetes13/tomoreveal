"""Moving around in the preview (plan 3.5 phase 1B): rotate, move (pan), zoom towards the mouse, centre on a point,
keys, a right-click menu, and a short history of view changes of its own.

The preview shows a frame of the movie, so moving the view changes the main view (or the view of the selected
"View from here" part), as dragging always did. Moving the view sideways sets the view's `pan`: the offset of the
rotation centre from the middle of what the view frames.
"""
import time

import numpy as np
from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from . import storyboard as sb

CLICK_PX = 4          # a right press that moves less than this is a click (menu), more is a drag (move the view)
HISTORY = 20          # view changes kept for "Undo view change"
GLIDE = 0.4           # seconds of the glide to a new rotation centre
EASE = 0.15           # seconds over which wheel and key moves are applied
MARKER_FADE = 0.5     # seconds the rotation-centre marker stays after a drag
HELP = ("Left drag: rotate · Right drag: move · Wheel: zoom · Right click: menu\n"
        "Middle drag or Shift + left drag: move · Ctrl + right drag: zoom smoothly\n"
        "Double-click a structure: turn around that point\n"
        "Keys (click the preview first): arrows move, Shift + arrows rotate, + and − zoom,\n"
        "F fits everything, Home goes back to the main view, 1 / 3 / 7: front / side / top view")
HINT = "Left drag: rotate · Right drag: move · Wheel: zoom · Right click: menu"


def exec_menu(menu, pos):
    """Show a menu (tests replace this so nothing waits for a click)."""
    menu.exec(pos)


def _lerp_view(a, b, u):
    out = dict(b)
    for k in ("tilt", "zoom"):
        if k in a and k in b:
            out[k] = a[k] + (b[k] - a[k]) * u if k == "tilt" else a[k] * (b[k] / a[k]) ** u
    if "az" in a and "az" in b:
        d = ((b["az"] - a["az"] + 180.0) % 360.0) - 180.0; out["az"] = ((a["az"] + d * u + 180.0) % 360.0) - 180.0
    pa, pb = np.asarray(a.get("pan") or [0, 0, 0], float), np.asarray(b.get("pan") or [0, 0, 0], float)
    out["pan"] = [float(v) for v in pa + (pb - pa) * u]
    return out


class Navigator:
    def __init__(self, win):
        self.win = win; self.history, self.future = [], []; self._last_push = 0.0
        self.drag = None; self.marker_until = 0.0; self.marker_on = False
        self.anim = QtC.QTimer(); self.anim.setInterval(30); self.anim.timeout.connect(self._step); self._anim = None
        self.fade = QtC.QTimer(); self.fade.setInterval(50); self.fade.timeout.connect(self._fade_step)

    # ---- settings ----
    @property
    def swapped(self):
        return self.win.settings.value("nav/swap_buttons", False, type=bool)

    @property
    def wheel_sign(self):
        return -1.0 if self.win.settings.value("nav/invert_wheel", False, type=bool) else 1.0

    # ---- the view being changed ----
    def ready(self):
        w = self.win
        return bool(w.spec) and w.gen is None and not w.busy and w.st.ren is not None

    def view(self):
        part = self.win.view_part()
        v = dict(part["view"] if part else self.win.spec["view"])
        v.setdefault("zoom", 1.0); v["pan"] = list(v.get("pan") or [0.0, 0.0, 0.0])
        return v

    def apply(self, v, quick=True):
        w = self.win
        if w.locked_part():  # a part with a locked view (3.5): nothing moves it
            self._anim = None; return
        part = w.view_part()
        pan = [float(x) for x in v.get("pan") or [0, 0, 0]]
        target = part["view"] if part else w.spec["view"]
        if any(abs(x) > 1e-9 for x in pan):
            target["pan"] = pan
        else:
            target.pop("pan", None)
        kw = dict(tilt=min(85.0, max(0.0, float(v["tilt"]))), az=((float(v["az"]) + 180.0) % 360.0) - 180.0,
                  zoom=min(8.0, max(0.3, float(v["zoom"]))))
        if part:
            w.set_part_view(part, quick=quick, **kw)
        else:
            w.set_view(quick=quick, **kw)

    def push(self, force=False):
        """Remember the view before a change (a burst of wheel steps counts once)."""
        now = time.monotonic()
        if not force and now - self._last_push < 0.6:
            self._last_push = now; return
        self._last_push = now
        v = self.view()
        if not self.history or self.history[-1] != v:
            self.history.append(v); del self.history[:-HISTORY]
        self.future.clear()

    def undo(self):
        if self.history:
            self.future.append(self.view()); self.apply(self.history.pop(), quick=False); self._last_push = 0.0

    def redo(self):
        if self.future:
            self.history.append(self.view()); self.apply(self.future.pop(), quick=False); self._last_push = 0.0

    # ---- smooth moves ----
    def glide(self, target, seconds=EASE):
        start = self.view() if self._anim is None else self._anim["now"]
        self._anim = dict(a=start, b=target, t0=time.monotonic(), T=max(1e-3, seconds), now=start)
        if seconds <= 0:
            self._anim = None; self.apply(target, quick=False); return
        self.anim.start()

    def _step(self):
        a = self._anim
        if a is None:
            self.anim.stop(); return
        u = min(1.0, (time.monotonic() - a["t0"]) / a["T"]); q = u * u * (3 - 2 * u)
        a["now"] = _lerp_view(a["a"], a["b"], q)
        if u >= 1.0:
            self.anim.stop(); self._anim = None; self.apply(a["b"], quick=False)
        else:
            self.apply(a["now"], quick=True)

    def target_view(self):
        """The view a running glide is heading for (so quick wheel steps add up), else the view now."""
        return dict(self._anim["b"]) if self._anim else self.view()

    # ---- geometry ----
    def pan_by(self, v, dfx, dfy, depth=None):
        """Move the view so the scene follows the mouse by (dfx, dfy) fractions of the frame."""
        ren = self.win.st.ren; pos, foc, right, up, d = ren.camera_frame()
        D = float((foc - pos) @ d) if depth is None else depth
        wph = ren.world_per_height(D); W, H = ren.size
        delta = -right * dfx * (W / H) * wph + up * dfy * wph
        v = dict(v); v["pan"] = [float(a + b) for a, b in zip(v.get("pan") or [0, 0, 0], delta)]
        return v

    def zoom_at(self, v, factor, f=None):
        """Zoom by factor, keeping the point under f (fractions) where it is."""
        ren = self.win.st.ren; v = dict(v)
        z = min(8.0, max(0.3, float(v.get("zoom", 1.0)) * factor)); factor = z / float(v.get("zoom", 1.0)); v["zoom"] = z
        if f is not None and abs(factor - 1) > 1e-9:
            P, _ = ren.point_under(*f); _, c, _, _, _ = ren.camera_frame()
            c2 = P + (c - P) / factor
            v["pan"] = [float(a + b) for a, b in zip(v.get("pan") or [0, 0, 0], c2 - c)]
        return v

    def centre_on(self, P):
        _, c, _, _, _ = self.win.st.ren.camera_frame(); v = self.view()
        v["pan"] = [float(a + b) for a, b in zip(v["pan"], np.asarray(P, float) - c)]
        return v

    def fit_box(self, lo, hi):
        v = self.view(); z, pan = sb.view_framing(self.win.spec_for(), self.win.st.ds.shape, lo, hi, v["tilt"], v["az"])
        v["zoom"], v["pan"] = z, pan
        return v

    # ---- mouse (called by the preview) ----
    def press(self, f, button, mods, pos):
        if not self.ready():
            return False
        rot = button == QtC.Qt.LeftButton and not (mods & QtC.Qt.ShiftModifier)
        if self.swapped and button in (QtC.Qt.LeftButton, QtC.Qt.RightButton) and not (mods & QtC.Qt.ShiftModifier):
            rot = button == QtC.Qt.RightButton
        kind = "rotate" if rot else "pan"
        if button == QtC.Qt.RightButton and mods & QtC.Qt.ControlModifier:
            kind = "zoom"
        depth = None
        if kind == "pan" and f is not None:  # pan speed matches the mouse at the depth of the point grabbed
            ren = self.win.st.ren; P, _ = ren.point_under(*f); cpos, _, _, _, d = ren.camera_frame(); depth = float((P - cpos) @ d)
        self.drag = dict(kind=kind, button=button, start=pos, last=pos, f=f, moved=False, depth=depth, pushed=False)
        return True

    def move(self, f, pos):
        d = self.drag
        if d is None:
            return
        if not d["moved"] and (pos - d["start"]).manhattanLength() <= CLICK_PX:
            return
        d["moved"] = True
        if not d["pushed"]:
            self.push(force=True); d["pushed"] = True; self.marker_on = True
        img = self.win.prev.image_px() or (1, 1)
        if d["kind"] == "rotate":
            self.win.on_drag(pos.x() - d["start"].x(), pos.y() - d["start"].y())
        elif d["kind"] == "pan":
            dl = pos - d["last"]
            self.apply(self.pan_by(self.view(), dl.x() / img[0], dl.y() / img[1], d["depth"]))
        else:  # Ctrl + right drag: zoom smoothly (up = closer)
            dl = pos - d["last"]
            self.apply(self.zoom_at(self.view(), 1.01 ** (-dl.y())))
        d["last"] = pos

    def release(self, f, global_pos):
        d, self.drag = self.drag, None
        if d is None:
            return
        if d["moved"]:
            if d["kind"] == "rotate":
                self.win.on_release()
            else:
                self.win.schedule(0)
            self.marker_until = time.monotonic() + MARKER_FADE; self.fade.start()
            return
        if d["button"] == QtC.Qt.RightButton:
            self.menu(f, global_pos)
        elif d["button"] == QtC.Qt.LeftButton and f is not None:
            self.win.on_click(*f)

    def wheel(self, delta, f, mods=QtC.Qt.NoModifier):
        if not self.ready() or not delta:
            return
        pp = self.win.path_point
        if pp and pp[0] == self.win.selected_part and mods & (QtC.Qt.ControlModifier | QtC.Qt.AltModifier):  # the selected path point
            panel = self.win.point_panel
            if mods & QtC.Qt.ControlModifier:
                panel.zoom.setValue(panel.zoom.value() + int(40 * delta / 120))
            else:
                panel.fov.setValue(panel.fov.value() - int(2 * delta / 120))
            panel._commit(); return
        self.push()
        self.glide(self.zoom_at(self.target_view(), 1.15 ** (self.wheel_sign * delta / 120.0), f))

    def double(self, f):
        if not self.ready() or f is None:
            return
        P = self.win.st.ren.pick_point(*f)
        if P is None:
            return
        self.push(force=True); self.glide(self.centre_on(P), GLIDE)

    # ---- the rotation-centre marker (preview only) ----
    def marker(self):
        """(fractions of the frame, opacity 0..1) of the rotation centre while dragging and shortly after, or None."""
        if self.drag is not None and self.drag["moved"]:
            a = 1.0
        else:
            left = self.marker_until - time.monotonic()
            if left <= 0:
                return None
            a = left / MARKER_FADE
        ren = self.win.st.ren
        if ren is None:
            return None
        _, c, _, _, _ = ren.camera_frame(); f, ok = ren.project(c)
        return (f, a) if ok else None

    def _fade_step(self):
        self.win.prev.update()
        if time.monotonic() > self.marker_until:
            self.fade.stop()

    # ---- keys ----
    def key(self, e):
        if not self.ready():
            return False
        k, mods = e.key(), e.modifiers(); shift = bool(mods & QtC.Qt.ShiftModifier)
        v = self.target_view()
        arrows = {QtC.Qt.Key_Left: (-1, 0), QtC.Qt.Key_Right: (1, 0), QtC.Qt.Key_Up: (0, -1), QtC.Qt.Key_Down: (0, 1)}
        if k in arrows:
            dx, dy = arrows[k]; self.push()
            if shift:
                v = dict(v, az=v["az"] - 5 * dx, tilt=min(85.0, max(0.0, v["tilt"] + 5 * dy)))
            else:
                v = self.pan_by(v, -0.05 * dx * self.win.st.ren.size[1] / self.win.st.ren.size[0], -0.05 * dy)
            self.glide(v); return True
        if k in (QtC.Qt.Key_Plus, QtC.Qt.Key_Equal, QtC.Qt.Key_Minus, QtC.Qt.Key_Underscore):
            self.push(); self.glide(self.zoom_at(v, 1.25 if k in (QtC.Qt.Key_Plus, QtC.Qt.Key_Equal) else 0.8)); return True
        if k == QtC.Qt.Key_F:
            self.push(force=True); self.glide(self.fit_selected(), GLIDE); return True
        if k == QtC.Qt.Key_Home:
            self.push(force=True); self.glide(self.home(), GLIDE); return True
        if k in (QtC.Qt.Key_1, QtC.Qt.Key_3, QtC.Qt.Key_7):
            self.push(force=True); self.glide(self.look(("front", "side", "top")[(QtC.Qt.Key_1, QtC.Qt.Key_3, QtC.Qt.Key_7).index(k)]), GLIDE)
            return True
        return False

    def look(self, where):
        v = self.view()
        v.update({"top": dict(tilt=0.0), "front": dict(tilt=85.0, az=0.0), "side": dict(tilt=85.0, az=90.0)}[where])
        return v

    def home(self):
        """Back to the main view (for a "View from here" part: the main view's values)."""
        part = self.win.view_part(); v = self.view()
        if part:
            m = self.win.spec["view"]
            v.update(tilt=float(m["tilt"]), az=float(m["az"]), zoom=float(m.get("zoom", 1.0)), pan=list(m.get("pan") or [0, 0, 0]))
        else:
            v.update(zoom=1.0, pan=[0.0, 0.0, 0.0])
        return v

    def fit_selected(self):
        """F: the structures of the selected close-up, or everything."""
        w = self.win; part = w.parts_panel.part(w.selected_part) if w.selected_part else None
        if part and part.get("type") == "focus":
            try:
                lo, hi = sb.focus_box(part, w.st.ds)
                return self.fit_box(lo, hi)
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        v = self.view(); v.update(zoom=1.0, pan=[0.0, 0.0, 0.0])
        return v

    # ---- the right-click menu ----
    def menu(self, f, global_pos):
        w = self.win
        if not self.ready():
            return
        m = QtW.QMenu(w); ren = w.st.ren
        P = ren.pick_point(*f) if f is not None else None
        hit = None
        if f is not None and w.st.has_segmentation:
            try:
                hit = w.st.pick(w.spec_for(), {"frame": w.frame, "x": f[0], "y": f[1]})
            except Exception:
                hit = None
            if hit is not None and not hit.get("found"):
                hit = None

        def act(text, fn, enabled=True, tip=None):
            a = m.addAction(text, fn); a.setEnabled(bool(enabled))
            if tip:
                a.setToolTip(tip)
            return a
        act("Centre here", lambda: (self.push(force=True), self.glide(self.centre_on(P), GLIDE)), P is not None)
        lk = m.addMenu("Look from")
        for t, where in (("above", "top"), ("the side", "side"), ("the front", "front")):
            lk.addAction(t, lambda where=where: (self.push(force=True), self.glide(self.look(where), GLIDE)))
        act("Fit everything", lambda: (self.push(force=True), self.glide(self.home() if not w.view_part() else dict(self.view(), zoom=1.0, pan=[0, 0, 0]), GLIDE)))
        cb = sb.clip_box(w.spec_for(), w.st.ds.shape)
        act("Fit the clip box", lambda: (self.push(force=True), self.glide(self.fit_box(np.array(cb[0], float), np.array(cb[1], float)), GLIDE)),
            cb is not None)
        if hit is not None:
            m.addSeparator(); key = hit["class"]; name = hit.get("name", "structure")
            lo_hi = None
            try:
                lo_hi = w.st.ds.target_box({"class": key, "component": hit.get("component")} if hit.get("component") is not None else {"class": key})
            except (KeyError, IndexError, TypeError, ValueError):
                pass
            act(f"Zoom to this {name} object" if hit.get("component") is not None else f"Zoom to {name}",
                lambda: (self.push(force=True), self.glide(self.fit_box(*lo_hi), GLIDE)), lo_hi is not None)
            act(f"Hide {name}", lambda: self.set_shown(key, False))
            act(f"Show only {name}", lambda: self.show_only(key))
            tgt = {"class": key, "component": hit["component"]} if hit.get("component") is not None else {"class": key}
            act(f"Add a close-up of this {name}" if hit.get("component") is not None else f"Add a close-up of {name}",
                lambda: w.add_focus(tgt, name))
        m.addSeparator()
        sel = w.parts_panel.part(w.selected_part) if w.selected_part else None
        if sel is not None and sel.get("type") == "path" and (sel.get("source") or {}).get("kind") == "points":
            act("Add path point here", lambda: self.add_path_point(sel["id"], P), P is not None)
        act("Use this view for the selected part", lambda: self.use_view(sel), sel is not None and sel.get("type") in ("overview", "slide", "hold", "focus", "path") and not sel.get("locked"),
            "Store this camera view in the selected overview, close-up or camera path")
        for extra in getattr(w, "preview_menu_extras", []):  # arrows and text boxes (phase 6) add their entries here
            extra(m, f, P)
        m.addSeparator()
        act("Undo view change", self.undo, bool(self.history)); act("Redo view change", self.redo, bool(self.future))
        exec_menu(m, global_pos)

    def set_shown(self, key, on):
        w = self.win
        if key in w.spec.get("classes", {}):
            w.spec["classes"][key]["show"] = bool(on); w.build_controls(); w.schedule(0)

    def show_only(self, key):
        w = self.win
        for k, c in w.spec.get("classes", {}).items():
            c["show"] = k == key
        w.build_controls(); w.schedule(0)

    def add_path_point(self, pid, P):
        w = self.win; part = w.parts_panel.part(pid)
        if part is None or P is None:
            return
        ws = list(part.get("waypoints") or []); ws.append(dict(point=[float(v) for v in P], pause=0.0))
        part["waypoints"] = ws; w.editor.commit(select=pid)
        w.say(f"Point {len(ws)} added.", "ok")

    def use_view(self, part):
        w = self.win
        if part is None:
            return
        if part.get("locked"):
            w.say("The view of this part is locked. Untick 'Lock the view of this part' in the Movie tab first.", "err"); return
        v = self.view(); pos, foc, _, _, _ = w.st.ren.camera_frame()
        if part["type"] in ("overview", "slide", "hold"):
            part["view"] = dict(tilt=float(v["tilt"]), az=float(v["az"]), zoom=float(v["zoom"]),
                                **({"pan": v["pan"]} if any(abs(x) > 1e-9 for x in v["pan"]) else {}))
        elif part["type"] == "focus":  # the angle it is seen from (3.5: also the turn, which used to follow the main view)
            part["tilt"] = float(v["tilt"]); part["view"] = dict(az=float(v["az"]))
        elif part["type"] == "path":
            ws = list(part.get("waypoints") or []); ws.append(dict(pos=[float(x) for x in pos], focal=[float(x) for x in foc], pause=0.0))
            part["waypoints"] = ws; part["source"] = dict(part.get("source") or {}, kind="points")
        w.editor.commit(select=part["id"]); w.say("The selected part now uses this view.", "ok")


def paint_marker(p, prev, nav):
    """Draw the rotation-centre marker (a small cross, 12 px) on the preview."""
    m = nav.marker() if nav is not None else None
    if m is None or not prev.image_px():
        return
    (fx, fy), a = m
    c = prev.to_widget(fx, fy); r = 6
    for col, wd in ((QtG.QColor(0, 0, 0, int(140 * a)), 3.5), (QtG.QColor(255, 255, 255, int(200 * a)), 1.5)):
        p.setPen(QtG.QPen(col, wd)); p.drawLine(QtC.QPointF(c.x() - r, c.y()), QtC.QPointF(c.x() + r, c.y()))
        p.drawLine(QtC.QPointF(c.x(), c.y() - r), QtC.QPointF(c.x(), c.y() + r))
