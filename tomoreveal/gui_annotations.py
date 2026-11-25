"""Arrows and text boxes in the window (plan 3.5 phase 6): the "Arrows and text" list in the Look tab, adding them
by clicking in the preview, dragging, right-click menus, several selected at once (align), and editing labels."""
import math

import numpy as np

from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from . import annotations as an
from .gui_fold import Section
from .gui_theme import DIM

COLOURS = (("automatic (white or black)", "auto"), ("white", "#ffffff"), ("black", "#000000"), ("yellow", "#ffd23f"),
           ("red", "#e4572e"), ("cyan", "#45b9e8"))


KIND_GLYPHS = {"arrow": "→", "text": "T", "curve": "⤻", "freehand": "✎", "line": "╱", "ellipse": "◯", "rect": "▭"}
HEADS_SHORT = (("head: end", "end"), ("heads: both", "both"), ("head: start", "start"), ("no head", "none"))
KIND_NAMES = {"arrow": "An arrow", "text": "A text box", "curve": "A curved arrow", "freehand": "A freehand arrow",
              "line": "A line", "ellipse": "An ellipse", "rect": "A rectangle"}


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


TIP_PX = 12  # 3.6: a tip (or the end of a drawn shape) is grabbed within this many pixels on screen


class AnnotationMouse:
    def __init__(self, win):
        self.win = win; self.drag = None; self.selected = []  # ids selected in the preview (Ctrl + click adds)

    def hit(self, od, f):
        if f is None:
            return None
        rects = od.rects(); sz = self.win.prev.image_px()
        if sz:  # 3.6 (R16): ends and handles first, within TIP_PX of their middle (they win over the camera and the label)
            best = None
            for n, r in rects.items():
                if n.startswith(("anncurve:", "anntip:", "anntail:")):
                    x0, y0, x1, y1 = r[0]; dx, dy = ((x0 + x1) / 2 - f[0]) * sz[0], ((y0 + y1) / 2 - f[1]) * sz[1]
                    d = (dx * dx + dy * dy) ** 0.5
                    pri = 0 if n.startswith("anncurve:") else 1 if n.startswith("anntip:") else 2
                    if d <= TIP_PX and (n.startswith(("anncurve:", "anntip:")) or not _label_box(r[0], sz)) and \
                            (best is None or (pri, d) < best[0]):
                        best = ((pri, d), n)
            if best is not None:
                return best[1]
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
            w.prev.update(); _sync_bar(w); return True
        if aid not in self.selected:
            self.selected = [aid]
        kind = ("bend" if name.startswith("anncurve:") else "tip" if name.startswith("anntip:") else
                "tail" if name.startswith("anntail:") and a["kind"] != "text" else "move")
        self.drag = dict(aid=aid, kind=kind, f0=f, moved=False, more=j, tip0=self.tip_at(a), pinned=not an.is_2d(a),
                         shown0=shown_fracs(w, a), under=None, t_pick=0.0,
                         start={x: (dict(an.find(w.spec, x).get("pos") or {}) if an.find(w.spec, x) else {}) for x in self.selected})
        _sync_bar(w)
        return True

    def tip_at(self, a):
        """Where an arrow's tip (a drawn shape's pinned point) is on the picture now (fractions), or None."""
        if an.is_drawn(a):
            S = shown_fracs(self.win, a)
            return None if S is None else tuple(float(v) for v in an.ref_point(a, S))
        if a.get("kind") != "arrow":
            return None
        if an.is_2d(a):
            return tuple(float(v) for v in a["tip2d"])
        ren = self.win.st.ren
        if ren is None or not (a.get("anchor") or {}).get("xyz"):
            return None
        (tx, ty), front = ren.project(a["anchor"]["xyz"])
        return (tx, ty) if front else None

    def put_tip(self, a, f):
        """Move an arrow's tip to f: on the picture (2D), or onto the 3D spot under f (3D; nothing there: unchanged)."""
        w = self.win
        if an.is_2d(a):
            a["tip2d"] = [round(min(1.0, max(0.0, f[0])), 4), round(min(1.0, max(0.0, f[1])), 4)]; return True
        anc = pick_anchor(w, f)
        if anc is None:
            return False
        a["anchor"] = anc
        return True

    def _follow(self, a, f):
        """During a drag the tip follows the mouse on the picture (2D); it is pinned again on release (3.6, R16)."""
        a["place"] = "2d"; a["tip2d"] = [round(min(1.0, max(0.0, f[0])), 4), round(min(1.0, max(0.0, f[1])), 4)]

    def _look_under(self, f):
        """What a pinned tip would be pinned to at f: a name for the note beside the crosshair (at most 10 times a second)."""
        import time
        d = self.drag
        if not d["pinned"] or time.monotonic() - d["t_pick"] < 0.1:
            return
        d["t_pick"] = time.monotonic(); d["at"] = f
        ren = self.win.st.ren
        if ren is None or ren.pick_point(*f) is None:
            d["under"] = ""; return
        hit = None
        try:
            hit = self.win.st.pick(self.win.spec_for(), {"frame": self.win.frame, "x": f[0], "y": f[1]})
        except Exception:
            pass
        d["under"] = (hit.get("name") or "structure") if hit and hit.get("found") else "the slice"

    def move(self, od, f, mods):
        d = self.drag; w = self.win
        if d is None or f is None:
            return
        a = an.find(w.spec, d["aid"])
        if a is None:
            return
        d["moved"] = True; ren = w.st.ren; d["at"] = f
        if an.is_drawn(a):
            self._move_drawn(a, d, f, mods)
        elif a["kind"] == "arrow" and d["kind"] == "move":  # the whole arrow: its tip goes along with the mouse
            if d["tip0"] is not None:
                g = (d["tip0"][0] + f[0] - d["f0"][0], d["tip0"][1] + f[1] - d["f0"][1]); self._follow(a, g); self._look_under(g)
        elif d["kind"] == "tail":  # angle and length of the tail, on screen
            tp = self.tip_at(a)
            if tp is None:
                return
            (tx, ty) = tp; W, H = ren.size
            dx, dy = (f[0] - tx) * W, (f[1] - ty) * H
            a["tail"]["angle"] = round(math.degrees(math.atan2(-dy, dx)) % 360, 1)
            a["tail"]["length"] = round(max(0.02, min(0.6, math.hypot(dx, dy) / H)), 4)
        elif d["kind"] == "tip":  # point the tip at another spot
            if d.get("more") is not None and d["more"] < len(a.get("more") or []):  # one of its extra pointers
                anc = pick_anchor(w, f)
                if anc is not None:
                    a["more"][d["more"]] = anc
            elif a["kind"] == "arrow":
                self._follow(a, f); self._look_under(f)
            else:
                anc = pick_anchor(w, f)
                if anc is not None:
                    a["pin"] = anc
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

    def _move_drawn(self, a, d, f, mods):
        """Drag a drawn shape (3.6): the whole of it, one end (the stroke turns and stretches about the other end; a
        corner of an ellipse or rectangle), or a curve's bend. It stays on the picture during the drag."""
        from . import drawing
        S0 = d["shown0"]
        if S0 is None:
            return
        S0 = np.asarray(S0, float); k = a["kind"]; dx, dy = f[0] - d["f0"][0], f[1] - d["f0"][1]
        if d["kind"] == "move":
            S = S0 + (dx, dy)
        elif d["kind"] == "bend" and k == "curve":
            S = S0.copy(); m = drawing.bezier_middle(S0[0], S0[1], S0[2]) + (dx, dy)
            S[1] = drawing.control_for_middle(S0[0], m, S0[2])
        elif k in ("ellipse", "rect"):
            S = S0.copy(); i = 1 if d["kind"] == "tip" else 0; g = (S0[i][0] + dx, S0[i][1] + dy)
            if mods & QtC.Qt.ShiftModifier:
                g = _square_frac(self.win, S0[1 - i], g)
            S[i] = g
        else:
            end, fixed = (S0[-1], S0[0]) if d["kind"] == "tip" else (S0[0], S0[-1])
            g = (end[0] + dx, end[1] + dy)
            if mods & QtC.Qt.ShiftModifier and k == "line":
                g = _snap_frac(self.win, fixed, g)
            S = drawing.similarity(S0, fixed, end, g)
        a["place"] = "2d"; a["points"] = [[round(float(x), 5), round(float(y), 5)] for x, y in S]
        if d["pinned"] and d["kind"] in ("move", "tip") or (d["pinned"] and k in ("ellipse", "rect")):
            self._look_under(tuple(an.ref_point(a, S)))

    def release(self, od):
        d, self.drag = self.drag, None
        if d is not None and d["moved"]:
            a = an.find(self.win.spec, d["aid"])
            if a is not None and d.get("pinned") and d.get("more") is None and \
                    (an.is_drawn(a) or (a["kind"] == "arrow" and d["kind"] in ("tip", "move"))):
                w = self.win; w.req = {"frame": w.frame}; w.do_preview()  # full size: the spot is found to the pixel
                repin(w, a)
            self.win.editor.commit(); self.win.schedule(0)
            if hasattr(self.win, "ann_panel"):
                self.win.ann_panel.rebuild()
        self.win.prev.update()

    def paint(self, p, prev):
        """While a pinned tip is dragged: a crosshair at the mouse and what the tip will be pinned to (3.6, R16)."""
        d = self.drag
        if d is None or not d.get("moved") or not d.get("pinned") or d.get("at") is None or d["kind"] == "bend":
            return
        a = an.find(self.win.spec, d["aid"]) if self.win.spec is not None else None
        if a is None or a["kind"] == "text":
            return
        f = self.tip_at(a) or d["at"]; c = prev.to_widget(*f)
        for col, wd in ((QtG.QColor(0, 0, 0, 160), 3), (QtG.QColor(255, 210, 63), 1.4)):
            p.setPen(QtG.QPen(col, wd))
            p.drawLine(QtC.QPointF(c.x() - 14, c.y()), QtC.QPointF(c.x() - 4, c.y())); p.drawLine(QtC.QPointF(c.x() + 4, c.y()), QtC.QPointF(c.x() + 14, c.y()))
            p.drawLine(QtC.QPointF(c.x(), c.y() - 14), QtC.QPointF(c.x(), c.y() - 4)); p.drawLine(QtC.QPointF(c.x(), c.y() + 4), QtC.QPointF(c.x(), c.y() + 14))
        u = d.get("under")
        text = "pin to: " + u if u else "nothing here: it will stay on the picture" if u == "" else "pin to: ..."
        fm = p.fontMetrics(); r = QtC.QRectF(c.x() + 16, c.y() + 10, fm.horizontalAdvance(text) + 12, fm.height() + 6)
        p.setPen(QtC.Qt.NoPen); p.setBrush(QtG.QColor(0, 0, 0, 170)); p.drawRoundedRect(r, 4, 4)
        p.setPen(QtG.QColor(255, 255, 255)); p.drawText(r, QtC.Qt.AlignCenter, text)

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


def pick_anchor(win, f):
    """{"xyz", "structure", "component"} of the 3D spot under f (fractions of the picture), or None (nothing there)."""
    ren = win.st.ren
    p = ren.pick_point(*f) if ren is not None else None
    if p is None:
        return None
    hit = None
    try:
        hit = win.st.pick(win.spec_for(), {"frame": win.frame, "x": f[0], "y": f[1]})
    except Exception:
        pass
    found = bool(hit and hit.get("found"))
    return dict(xyz=[float(v) for v in p], structure=hit.get("class") if found else None,
                component=hit.get("component") if found else None)


def _label_box(box, sz):
    """A tail box that is a label (wider than a handle): grabbed anywhere on it, not by its middle only."""
    return (box[2] - box[0]) * sz[0] > 2 * TIP_PX + 8


def _px(win):
    ren = win.st.ren
    return ren.size if ren is not None else (16, 9)


def _snap_frac(win, p0, p1):
    from .drawing import snap_angle
    W, H = _px(win); q = snap_angle((p0[0] * W, p0[1] * H), (p1[0] * W, p1[1] * H))
    return (q[0] / W, q[1] / H)


def _square_frac(win, p0, p1):
    from .drawing import square
    W, H = _px(win); q = square((p0[0] * W, p0[1] * H), (p1[0] * W, p1[1] * H))
    return (q[0] / W, q[1] / H)


def shown_fracs(win, a):
    """A drawn shape's points where they are on the picture now (fractions), or None (its spot is behind the camera)."""
    if not an.is_drawn(a):
        return None
    P = np.asarray(a.get("points"), float)
    if an.is_2d(a):
        return P
    ren = win.st.ren
    if ren is None:
        return P
    (fx, fy), front = ren.project(a["anchor"]["xyz"])
    return P + (np.array([fx, fy]) - an.ref_point(a, P)) if front else None


def repin(win, a, say=True):
    """After a drag: pin the tip (a shape's pinned point) to the 3D spot under it again. Nothing there: it stays on
    the picture (2D) and the window says so. True when pinned."""
    if a["kind"] == "arrow":
        at = tuple(a["tip2d"]) if an.is_2d(a) else None
    else:
        at = tuple(float(v) for v in an.ref_point(a))
    anc = pick_anchor(win, at) if at is not None else None
    if anc is None:
        if say:
            win.say("Nothing under the tip to pin it to: it now stays where it is on the picture. Drag it onto a structure "
                    "or the slice to pin it again.", "err")
        return False
    a["anchor"] = anc; a["place"] = "3d"
    if a["kind"] == "arrow":
        a.pop("tip2d", None)
    return True


def _sync_bar(win):
    bar = getattr(win, "drawbar", None)
    if bar is not None:
        bar.sync()


def start_move_tip(win, aid):
    """Move tip...: the next click in the preview is where the tip goes (pinned to what is there, or on the picture)."""
    win.pick_btn.setChecked(False); win.end_picks(); win.arrow_pick = ("tip", aid); win.prev.setCursor(QtC.Qt.CrossCursor)
    win.say("Click where the tip should point. Esc cancels.")


def move_tip_to(win, aid, f):
    a = an.find(win.spec, aid)
    if a is None or f is None:
        return
    if an.is_drawn(a):
        S = shown_fracs(win, a)
        if S is None:
            S = np.asarray(a["points"], float)
        S = S + (np.asarray(f, float) - an.ref_point(a, S))
        a["points"] = [[round(float(x), 5), round(float(y), 5)] for x, y in S]; a["place"] = "2d"
    else:
        a["place"] = "2d"; a["tip2d"] = [round(float(f[0]), 4), round(float(f[1]), 4)]
    pinned = repin(win, a, say=False)
    commit(win)
    win.say("The tip now points there." if pinned else "Nothing there to pin to: the tip stays on the picture there.", "ok")


def set_place(win, aid, how):
    """An arrow on the spot in 3D (it moves with the camera) or on the picture in 2D (it stays where it is on screen).
    It stays where it is now in the preview when switched."""
    a = an.find(win.spec, aid)
    if a is None or a["kind"] == "text":
        return
    if an.is_drawn(a):  # 3.6: drawn shapes keep their place on the picture, pinned (3D) or not (2D)
        S = shown_fracs(win, a)
        if S is not None:
            a["points"] = [[round(float(x), 5), round(float(y), 5)] for x, y in S]
        a["place"] = "2d"
        if how == "3d" and not repin(win, a, say=False):
            win.say("There is nothing under the tip to pin it to. Drag the tip onto a structure or the slice.", "err")
        commit(win); return
    now = win.ann_mouse.tip_at(a)
    if how == "2d":
        if now is None:
            win.say("Move the playhead to where the arrow shows, then switch it.", "err"); return
        a["place"] = "2d"; a["tip2d"] = [round(now[0], 4), round(now[1], 4)]
    else:
        a["place"] = "3d"
        anc = pick_anchor(win, now) if now is not None else None
        if anc is not None:
            a["anchor"] = anc
        elif not (a.get("anchor") or {}).get("xyz"):
            win.say("There is nothing under the tip to pin it to. Drag the tip onto a structure or the slice.", "err")
            a["place"] = "2d"; return
    commit(win)


def pick_colour(win, aid, key, title):
    """A colour from the colour wheel for an item's text / arrow ("color") or its box ("fill")."""
    a = an.find(win.spec, aid)
    if a is None:
        return
    st = a.setdefault("style", {}); cur = st.get(key)
    c = QtW.QColorDialog.getColor(QtG.QColor(cur if cur and cur != "auto" else "#3a6ea5"), win, title)
    if not c.isValid():
        return

    def fn(b):
        b.setdefault("style", {})[key] = c.name()
        if key == "fill":
            b["style"]["box"] = "fill"
    change(win, aid, fn)


def unpin(win, b):
    """A text box pinned to a spot in 3D stays where it is now on the picture instead (2D)."""
    od = win.prev.overlay; r = od.rects().get(f"ann:{b['id']}") if od is not None else None
    b["pin"] = None
    if r:
        b["pos"] = dict(x=round(r[0][0], 4), y=round(r[0][1], 4), anchor="top-left")


def pin_text(win, aid):
    """Pin a text box to the 3D spot at the middle of its left edge: it then moves with the camera."""
    a = an.find(win.spec, aid); od = win.prev.overlay
    r = od.rects().get(f"ann:{aid}") if od is not None and a is not None else None
    if r is None:
        return
    x0, y0, x1, y1 = r[0]; anc = None
    for fx in (x0, x0 - 0.01, x0 + 0.01, (x0 + x1) / 2):
        anc = pick_anchor(win, (fx, (y0 + y1) / 2))
        if anc is not None:
            break
    if anc is None:
        win.say("There is nothing behind the box to pin it to. Move it next to a structure or onto the slice first.", "err"); return
    change(win, aid, lambda b: b.__setitem__("pin", anc))


def item_menu(win, aid, gpos, pointer=None):
    a = an.find(win.spec, aid)
    if a is None:
        return
    m = QtW.QMenu(win); arrow = a["kind"] == "arrow"; drawn = an.is_drawn(a)
    m.addAction("Edit text..." if a["kind"] == "text" else "Edit label...", lambda: edit_label(win, aid))
    if a["kind"] != "text":
        m.addAction("Move tip..." if a["kind"] not in ("ellipse", "rect") else "Move to...", lambda: start_move_tip(win, aid))
    if not drawn:
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
        pl = m.addMenu("Keep it")
        for t, v in an.PLACES:
            act = pl.addAction(t, lambda v=v: set_place(win, aid, v)); act.setCheckable(True)
            act.setChecked(("2d" if an.is_2d(a) else "3d") == v)
    elif drawn:
        if a["kind"] in ("curve", "freehand", "line"):
            hd = m.addMenu("Arrowheads")
            for t, v in an.HEADS:
                act = hd.addAction(t, lambda v=v: change(win, aid, lambda b: b["style"].__setitem__("heads", v))); act.setCheckable(True)
                act.setChecked(st.get("heads", "end") == v)
        pl = m.addMenu("Keep it")
        for t, v in an.PLACES:
            act = pl.addAction(t, lambda v=v: set_place(win, aid, v)); act.setCheckable(True)
            act.setChecked(("2d" if an.is_2d(a) else "3d") == v)
    else:
        bx = m.addMenu("Box")
        for t, v in an.BOXES:
            if v == "fill":
                act = bx.addAction("coloured box (choose a colour) ...", lambda: pick_colour(win, aid, "fill", "Colour of the box"))
            else:
                act = bx.addAction(t, lambda v=v: change(win, aid, lambda b: b["style"].__setitem__("box", v)))
            act.setCheckable(True); act.setChecked(st.get("box", "dark") == v)
        if a.get("pin"):
            m.addAction("Keep it on the picture (2D, stays put)", lambda: change(win, aid, lambda b: unpin(win, b)))
        else:
            m.addAction("Pin it to the spot next to it (3D, moves with the camera)", lambda: pin_text(win, aid))
    co = m.addMenu("Colour")
    for t, v in COLOURS:
        act = co.addAction(t, lambda v=v: change(win, aid, lambda b: b["style"].__setitem__("color", v))); act.setCheckable(True)
        act.setChecked(st.get("color", "auto") == v)
    other = co.addAction("other colour ...", lambda: pick_colour(win, aid, "color", "Colour")); other.setCheckable(True)
    other.setChecked(st.get("color", "auto") not in [v for _, v in COLOURS])
    ap = m.addMenu("Appear")
    for t, v in an.APPEAR:
        act = ap.addAction(t, lambda v=v: change(win, aid, lambda b: b.setdefault("show", {}).__setitem__("appear", v)))
        act.setCheckable(True); act.setChecked((a.get("show") or {}).get("appear", "fade") == v)
    ly = m.addMenu("Separate layer movie")
    for t, v in an.LAYERS:
        act = ly.addAction(f"goes into: {t}", lambda v=v: change(win, aid, lambda b: b.__setitem__("layer", v)))
        act.setCheckable(True); act.setChecked(an.layer_of(a) == v)
    m.addAction("Duplicate", lambda: (an.duplicate(win.spec, aid), commit(win)))
    cp = m.addMenu("Copy to")
    for i, p in enumerate(win.spec.get("shots", [])):
        if p.get("type") == "card":
            continue
        from .storyboard import part_name
        from .gui_theme import part_icon
        cp.addAction(part_icon(p), f"{i + 1}. {part_name(p, win.spec, win.st.ds)}", lambda pid=p["id"]: copy_to(win, aid, pid))
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


def change(win, aid, fn, from_row=False):
    a = an.find(win.spec, aid)
    if a is not None:
        fn(a)
        if from_row and hasattr(win, "ann_panel"):
            win.ann_panel.mark(aid)  # its own row already shows the change: the row is not made again
        commit(win)


def change_here(win, aid, fn):
    """A change made in the item's row of the Arrows and text list."""
    change(win, aid, fn, from_row=True)


def delete(win, aid):
    win.spec["annotations"] = [a for a in an.items(win.spec) if a["id"] != aid]
    win.ann_mouse.selected = [x for x in win.ann_mouse.selected if x != aid]; commit(win)


def copy_to(win, aid, pid):
    b = an.duplicate(win.spec, aid)
    if b is not None:
        b["show"]["parts"] = [pid]
        if b["kind"] == "text":
            b["pos"] = dict(an.find(win.spec, aid).get("pos") or b["pos"])  # same place on the screen
        elif an.is_drawn(b):
            b["points"] = [list(q) for q in an.find(win.spec, aid)["points"]]
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
    cur = (a.get("label") or {}).get("text", "") if a["kind"] != "text" else a.get("text", "")
    le = QtW.QLineEdit(win.prev); le.setText(cur); le.setPlaceholderText("type the label"); le.setMinimumWidth(200)
    od = win.prev.overlay; r = od.rects().get(f"anntail:{aid}") or od.rects().get(f"ann:{aid}") if od is not None else None
    pt = win.prev.to_widget(r[0][0], r[0][1]).toPoint() if r and win.prev.image_px() else QtC.QPoint(40, 40)
    le.move(max(4, min(pt.x(), win.prev.width() - 210)), max(4, min(pt.y(), win.prev.height() - 30))); le.show(); le.setFocus(); le.selectAll()
    win.label_editor = le

    def done():
        b = an.find(win.spec, aid)
        if b is not None and le.isVisible():
            if b["kind"] != "text":
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

    @staticmethod
    def _snap(a):
        import json
        return json.dumps(a, sort_keys=True, default=str)

    def mark(self, aid):
        """Item aid was changed from its own row: that row already shows the change, so it is not made again."""
        a = an.find(self.win.spec, aid) if self.win.spec is not None else None
        if a is not None and aid in self._shown:
            self._shown[aid] = self._snap(a)

    def rebuild(self):
        """3.6: the list is made again only when the items listed change; an item changed elsewhere (undo, a drag in
        the preview, a menu) gets a new row of its own, and the other rows stay as they are (no flicker)."""
        from .gui_data import clear_layout
        w = self.win; shown = getattr(self, "_shown", None)
        if w.spec is None:
            clear_layout(self.rows); self._shown = {}; self._row_w = {}; return
        pid = w.selected_part
        want = [a for a in an.items(w.spec) if not (self.only_part.isChecked() and pid and not an.shown_in(a, pid))]
        if shown is not None and [a["id"] for a in want] == list(shown) and getattr(self, "_spec_id", None) == id(w.spec):
            for i, a in enumerate(want):
                snap = self._snap(a)
                if shown[a["id"]] != snap:
                    old = self._row_w[a["id"]]; new = self._row(a); self.rows.insertWidget(i, new)
                    old.setParent(None); old.deleteLater(); self._row_w[a["id"]] = new; shown[a["id"]] = snap
            return
        clear_layout(self.rows); self._shown = {}; self._row_w = {}; self._spec_id = id(w.spec)
        for a in want:
            r = self._row(a); self.rows.addWidget(r); self._row_w[a["id"]] = r; self._shown[a["id"]] = self._snap(a)

    def _row(self, a):
        """One item: [shown] [arrow / text] [label] [shape or box] [colour] [x], and under it when it shows and what it
        points at (3.5: the common choices are in the row; the rest stays in the ⋯ menu)."""
        w = self.win; aid = a["id"]; arrow = a["kind"] != "text"; drawn = an.is_drawn(a)
        row = QtW.QFrame(); row.setFrameShape(QtW.QFrame.StyledPanel)
        v = QtW.QVBoxLayout(row); v.setContentsMargins(4, 3, 4, 3); v.setSpacing(2)
        h = QtW.QHBoxLayout(); h.setContentsMargins(0, 0, 0, 0); v.addLayout(h)
        vis = QtW.QCheckBox(); vis.setChecked(a.get("visible", True)); vis.setToolTip("Show or hide this item")
        vis.toggled.connect(lambda on: change_here(w, aid, lambda b: b.__setitem__("visible", bool(on))))
        kind = QtW.QLabel(KIND_GLYPHS.get(a["kind"], "→")); kind.setToolTip(KIND_NAMES.get(a["kind"], "An arrow"))
        txt = QtW.QLineEdit((a.get("label") or {}).get("text", "") if arrow else a.get("text", ""))
        txt.setToolTip("The label of the arrow, or the text"); txt.setMinimumWidth(80)
        txt.setPlaceholderText("label" if arrow else "text")

        def set_text():
            def fn(b, t=txt.text()):
                if b["kind"] != "text":
                    b.setdefault("label", {})["text"] = t
                else:
                    b["text"] = t
            change_here(w, aid, fn)
        txt.editingFinished.connect(set_text)
        st = a.get("style") or {}

        def combo(items, cur, key, tip):
            cb = QtW.QComboBox(); cb.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon); cb.setMinimumContentsLength(5)
            for t, d in items:
                cb.addItem(t, d)
            cb.setCurrentIndex(max(0, cb.findData(cur))); cb.setToolTip(tip)
            cb.activated.connect(lambda j: change_here(w, aid, lambda b: b.setdefault("style", {}).__setitem__(key, cb.itemData(j))))
            return cb
        if drawn:
            look = combo(HEADS_SHORT, st.get("heads", "end"), "heads", "Arrowheads at the ends of the line") \
                if a["kind"] in ("curve", "freehand", "line") else None
        else:
            look = combo(an.SHAPES, st.get("shape", "arrow"), "shape", "Shape of the arrow") if arrow else \
                combo(an.BOXES, st.get("box", "dark"), "box", "Box behind the text")
        col = combo(COLOURS, st.get("color", "auto"), "color", "Colour")
        if st.get("color", "auto") not in [v for _, v in COLOURS]:  # chosen from the colour wheel
            col.addItem("own colour", st["color"]); col.setCurrentIndex(col.count() - 1)
        wheel = QtW.QToolButton(); wheel.setFixedSize(26, 22)
        shown = st.get("fill") if not arrow and st.get("box") == "fill" else (st.get("color") if st.get("color", "auto") != "auto" else None)
        wheel.setStyleSheet(f"background:{shown}; border:1px solid palette(mid); border-radius:3px" if shown else "")
        wheel.setText("" if shown else "🎨")
        if arrow:
            wheel.setToolTip("Choose the colour of the arrow from a colour wheel")
            wheel.clicked.connect(lambda: pick_colour(w, aid, "color", "Colour of the arrow"))
        else:
            wheel.setToolTip("Fill the box with a colour from a colour wheel (the text turns black or white, whichever reads)")
            wheel.clicked.connect(lambda: pick_colour(w, aid, "fill", "Colour of the box"))
        dele = QtW.QToolButton(); dele.setText("✕"); dele.setToolTip("Delete this item"); dele.clicked.connect(lambda: delete(w, aid))
        for x in (vis, kind, txt, look, col, wheel, dele):
            if x is not None:
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
        stt.valueChanged.connect(lambda x: change_here(w, aid, lambda b: b["show"].__setitem__("start", None if x < 0 else float(x))))
        en.valueChanged.connect(lambda x: change_here(w, aid, lambda b: b["show"].__setitem__("end", None if x < 0 else float(x))))
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
        for x in (stt, en, here, there) + (() if drawn else (more,)) + (menu,):
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
    """Dashed boxes around the selected arrows and text boxes, round handles on their tips, ends and bends (3.6), and
    the crosshair of a tip being dragged."""
    od = prev.overlay
    if od is None:
        return
    win.ann_mouse.paint(p, prev)
    if not win.ann_mouse.selected:
        return
    rects = od.rects(); hl = prev.palette().highlight().color()
    for aid in win.ann_mouse.selected:
        r = rects.get(f"ann:{aid}")
        if not r:
            continue
        a, b = prev.to_widget(r[0][0], r[0][1]), prev.to_widget(r[0][2], r[0][3])
        p.setPen(QtG.QPen(hl, 1, QtC.Qt.DashLine)); p.setBrush(QtC.Qt.NoBrush)
        p.drawRect(QtC.QRectF(a, b))
        p.setRenderHint(QtG.QPainter.Antialiasing)
        for pre, rad in (("anntip", 6.0), ("anncurve", 5.0), ("anntail", 4.5)):
            q = rects.get(f"{pre}:{aid}")
            if not q or (pre == "anntail" and _label_box(q[0], prev.image_px() or (1, 1))):
                continue
            c = prev.to_widget((q[0][0] + q[0][2]) / 2, (q[0][1] + q[0][3]) / 2)
            p.setPen(QtG.QPen(QtG.QColor(0, 0, 0, 180), 1.2)); p.setBrush(QtG.QColor(255, 210, 63) if pre == "anncurve" else hl)
            p.drawEllipse(c, rad, rad)
