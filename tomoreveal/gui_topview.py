"""Editing a camera path from above (3.6, R19): the preview looks straight down on the clip box (orthographic, the
view locked) while a path through points is edited; clicks add points at the chosen height, a drag with "Draw the
path" lays out the whole path, and a strip under the preview shows and changes the points' heights. Nothing in the
settings changes when the mode is switched on or off: the top view is drawn instead of the part's own camera."""
import math

import numpy as np
from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from . import drawing
from . import storyboard as sb

POINT_GAP_PX = 30  # a drawn path: at most one point per this many pixels on screen
STRIP_H = 78


def active(win, pid=None):
    t = getattr(win, "top_edit", None)
    return t is not None and (pid is None or t["pid"] == pid)


def box(win):
    nz, ny, nx = win.st.ds.shape
    cb = sb.clip_box(win.spec, win.st.ds.shape)
    return (np.asarray(cb[0], float), np.asarray(cb[1], float)) if cb else (np.zeros(3), np.array([nx - 1.0, ny - 1.0, nz - 1.0]))


def top_pose(win, spec, size):
    """Straight down on the clip box, turned like the main view, fitted with a small margin."""
    lo, hi = box(win)
    fr = sb.Framer(win.st.ds.shape, size[0] / size[1], float(spec.get("style", {}).get("fov", 30)), True)
    return fr.fit(lo, hi, 0.0, float((spec.get("view") or {}).get("az", 0.0)), 1.04)


def draw_state(win, spec, frames, i, size):
    """(state, spec) to draw in the top view: the part's frame (the slice and structures as they are there) seen from
    above, orthographic. The settings themselves are not changed."""
    pid = win.top_edit["pid"]
    if frames[i].get("part") != pid or frames[i].get("phase") != "main":
        js = [j for j, f in enumerate(frames) if f.get("part") == pid and f.get("phase") == "main"]
        i = js[0] if js else i
    s = dict(frames[i], blend=None, lkfrom=None, card=None, e=0.0, target=None)
    s["pose"] = top_pose(win, spec, size); s.pop("ann", None)
    dspec = dict(spec, style=dict(spec.get("style") or {}, ortho=True))
    return s, dspec


def height_of(win, part, f):
    """The height (z, voxels) of a new point at f: the part's chosen height, else what is under the mouse, else the slice."""
    h = part.get("top_height")
    if h is not None:
        return float(h)
    p = win.st.ren.pick_point(*f) if win.st.ren is not None else None
    if p is not None:
        return float(p[2])
    plan = win.timeline.canvas.plan
    return float(plan.frames[win.frame]["k"]) if plan is not None and 0 <= win.frame < len(plan.frames) else 0.0


def point_at(win, part, f):
    """The point under f (fractions of the top view) at the height new points get."""
    z = height_of(win, part, f)
    q = win.st.ren.to_plane(f[0], f[1], z)
    return None if q is None else [float(q[0]), float(q[1]), float(z)]


def start(win, pid):
    """Switch the preview to the view from above for part pid (the view is not changed, only drawn differently)."""
    part = win.parts_panel.part(pid)
    if part is None or part.get("type") != "path":
        return
    if (part.get("source") or {}).get("kind") != "points":
        win.parts_panel.set(pid, "source", dict(part.get("source") or {}, kind="points"))
    win.end_picks(keep=pid); win.top_edit = dict(pid=pid, draw=False); win.selected_part = pid
    win.path_edit_paused = None
    if win.parts_panel.open_id == pid:
        win.parts_panel.fill_details(pid)
    strip(win).show(); strip(win).update()
    win.req = {"at": {"part": pid, "u": 0.0}}; win.schedule(0)
    win.say("Seen from above. Click to add points, drag a point to move it, or tick 'Draw the path' and draw it. "
            "Esc or 'Edit from above' again goes back.", "ok")


def stop(win):
    if getattr(win, "top_edit", None) is None:
        return
    pid = win.top_edit["pid"]; win.top_edit = None
    if getattr(win, "_top_strip", None) is not None:
        win._top_strip.hide()
    if win.parts_panel.open_id == pid:
        win.parts_panel.fill_details(pid)
    win.schedule(0)


def click(win, f):
    """A click in the top view: a point after the selected one, at the chosen height. True when handled."""
    pid = win.top_edit["pid"]; part = win.parts_panel.part(pid)
    if part is None or win.st.ren is None:
        return False
    q = point_at(win, part, f)
    if q is None:
        return True
    ws = list(part.get("waypoints") or []); at = (win.path_point[1] + 1) if win.path_point and win.path_point[0] == pid else len(ws)
    ws.insert(at, dict(point=q, pause=0.0)); part["waypoints"] = ws
    win.editor.commit(select=pid); win.path_point = (pid, at)
    win.say(f"Point {at + 1} added.", "ok")
    return True


def spaced(px, gap=POINT_GAP_PX):
    """Keep the simplified stroke's points no closer than `gap` pixels (the last point always stays)."""
    kept = [np.asarray(px[0], float)]
    for p in px[1:-1]:
        if np.hypot(*(np.asarray(p, float) - kept[-1])) >= gap:
            kept.append(np.asarray(p, float))
    last = np.asarray(px[-1], float)
    if len(kept) > 1 and np.hypot(*(last - kept[-1])) < gap * 0.5:
        kept[-1] = last
    else:
        kept.append(last)
    return np.array(kept)


def finish_stroke(win, s):
    """A path drawn in the top view: simplified (RDP), at most one point per POINT_GAP_PX, replacing the points."""
    pid = win.top_edit["pid"]; part = win.parts_panel.part(pid)
    px = np.asarray(s["px"], float); fr = np.asarray(s["pts"], float)
    if part is None or len(px) < 2 or drawing.length(px) < POINT_GAP_PX:
        win.say("Draw a longer line for the path.", "err"); return None
    kept = spaced(drawing.simplify(px, 1.5))
    idx = [int(np.argmin(np.hypot(*(px - k).T))) for k in kept]
    ws = []
    for j in idx:
        q = point_at(win, part, tuple(fr[j]))
        if q is not None:
            ws.append(dict(point=q, pause=0.0))
    if len(ws) < 2:
        return None
    part["waypoints"] = ws; win.top_edit["draw"] = False
    win.editor.commit(select=pid); win.path_point = None
    if win.parts_panel.open_id == pid:
        win.parts_panel.fill_details(pid)
    win.say(f"The path now has {len(ws)} points. Drag them to adjust; Play path flies it.", "ok")
    return ws


def strip(win):
    if getattr(win, "_top_strip", None) is None:
        win._top_strip = HeightStrip(win); win.prev_strip_layout.addWidget(win._top_strip); win._top_strip.hide()
    return win._top_strip


class HeightStrip(QtW.QWidget):
    """The heights of the path's points while editing from above: drag a point up or down to change its height."""

    def __init__(self, win):
        super().__init__(); self.win = win; self.setFixedHeight(STRIP_H); self.drag = None
        self.setToolTip("Height of each point above the bottom of the tomogram (left: first point). Drag a point up or down "
                        "to change its height.")
        self.setMouseTracking(True)

    def _part(self):
        t = getattr(self.win, "top_edit", None)
        return self.win.parts_panel.part(t["pid"]) if t else None

    def _geom(self):
        nz = self.win.st.ds.shape[0]; m = 14
        return m, self.width() - m, 8, self.height() - 16, max(1.0, nz - 1.0)

    def _xy(self, i, n, z):
        x0, x1, y0, y1, top = self._geom()
        x = x0 + (x1 - x0) * (i / (n - 1) if n > 1 else 0.5)
        return QtC.QPointF(x, y1 - (y1 - y0) * min(1.0, max(0.0, z / top)))

    def heights(self):
        p = self._part()
        out = []
        for w in (p.get("waypoints") or []) if p else []:
            q = w.get("point") if w.get("point") is not None else w.get("pos")
            out.append(float(q[2]) if q is not None else 0.0)
        return out

    def paintEvent(self, e):
        p = QtG.QPainter(self); p.setRenderHint(QtG.QPainter.Antialiasing); pal = self.palette()
        p.fillRect(self.rect(), pal.alternateBase())
        zs = self.heights(); n = len(zs); x0, x1, y0, y1, top = self._geom()
        p.setPen(pal.placeholderText().color())
        p.drawText(QtC.QRectF(x0, y1 + 1, x1 - x0, 14), QtC.Qt.AlignLeft, "height of each point (drag up or down)")
        p.drawText(QtC.QRectF(x0, y1 + 1, x1 - x0, 14), QtC.Qt.AlignRight, f"top of the tomogram: {int(top)}")
        if not n:
            p.end(); return
        pts = [self._xy(i, n, z) for i, z in enumerate(zs)]
        p.setPen(QtG.QPen(QtG.QColor("#3a6fd8"), 2))
        for a, b in zip(pts, pts[1:]):
            p.drawLine(a, b)
        sel = self.win.path_point[1] if self.win.path_point and self.win.path_point[0] == self.win.top_edit["pid"] else None
        for i, q in enumerate(pts):
            p.setPen(QtG.QPen(QtG.QColor(0, 0, 0), 1))
            p.setBrush(QtG.QColor("#ffd23f" if i == sel else "#2e9e44" if i == 0 else "#d1495b" if i == n - 1 else "#3a6fd8"))
            p.drawEllipse(q, 6, 6)
            p.drawText(QtC.QRectF(q.x() - 12, q.y() - 20, 24, 12), QtC.Qt.AlignCenter, str(i + 1))
        p.end()

    def _hit(self, pos):
        zs = self.heights()
        for i, z in enumerate(zs):
            q = self._xy(i, len(zs), z)
            if math.hypot(q.x() - pos.x(), q.y() - pos.y()) <= 9:
                return i
        return None

    def mousePressEvent(self, e):
        i = self._hit(e.position())
        if i is not None:
            self.drag = i; self.win.path_point = (self.win.top_edit["pid"], i); self.update()

    def mouseMoveEvent(self, e):
        if self.drag is None:
            self.setCursor(QtC.Qt.SizeVerCursor if self._hit(e.position()) is not None else QtC.Qt.ArrowCursor); return
        x0, x1, y0, y1, top = self._geom()
        z = round(min(top, max(0.0, (y1 - e.position().y()) / max(1.0, y1 - y0) * top)), 1)
        set_height(self.win, self.drag, z, commit=False); self.update()

    def mouseReleaseEvent(self, e):
        if self.drag is not None:
            self.drag = None; self.win.editor.commit(select=self.win.top_edit["pid"])


def set_height(win, i, z, commit=True):
    """Point i of the path being edited at height z (its look-at point, or a stored view's camera)."""
    t = getattr(win, "top_edit", None); pid = t["pid"] if t else win.selected_part
    part = win.parts_panel.part(pid) if pid else None
    if part is None or not (0 <= i < len(part.get("waypoints") or [])):
        return
    w = part["waypoints"][i]; key = "point" if w.get("point") is not None else "pos"
    q = list(w[key]); q[2] = float(z); w[key] = q
    if commit:
        win.editor.commit(select=pid)
    else:
        win.schedule(0)
