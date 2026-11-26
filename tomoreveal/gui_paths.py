"""Settings of a camera path part in the Movie tab: what to follow or which points, and how the camera moves."""
import numpy as np
from PySide6 import QtCore as QtC, QtWidgets as QtW

from . import storyboard as sb
from .paths import PATH_CAMERA, point_values
from .gui_theme import DIM

SIDES = (("above", "above"), ("from the left", "left"), ("from the right", "right"), ("behind, looking along it", "behind"),
         ("as the main view", "free"))
LOOKS = (("at the path", "at_path"), ("a little ahead", "ahead"))
SPEEDS = (("even", "even"), ("slow at the ends", "ease"))


def _combo(items, cur, fn):
    cb = QtW.QComboBox(); cb.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon); cb.setMinimumContentsLength(8)
    for t, d in items:
        cb.addItem(t, d)
    cb.setCurrentIndex(max(0, cb.findData(cur)))
    cb.activated.connect(lambda j: fn(cb.itemData(j)))
    return cb


def _row(*ws):
    w = QtW.QWidget(); h = QtW.QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
    for x in ws:
        h.addWidget(x)
    h.addStretch(1)
    return w


def _spin(lo, hi, v, fn, step=0.1, suffix="", decimals=2, prefix=""):
    sp = QtW.QDoubleSpinBox(); sp.setRange(lo, hi); sp.setSingleStep(step); sp.setDecimals(decimals); sp.setSuffix(suffix); sp.setPrefix(prefix)
    sp.setValue(float(v)); sp.valueChanged.connect(fn)
    return sp


def current_view(win):
    """(camera position, focal point) of the frame shown in the preview."""
    plan = win.timeline.canvas.plan
    if plan is None or not (0 <= win.frame < len(plan.frames)):
        return None
    p = plan.frames[win.frame]["pose"]
    if p.get("pos") is not None:
        return np.asarray(p["pos"], float), np.asarray(p["c"], float)
    style = win.spec.get("style", {}); w, h = win.spec_for()["output"]["size"]
    fr = sb.Framer(win.st.ds.shape, w / h, float(style.get("fov", 30)), bool(style.get("ortho", False)))
    d, _ = fr.axes(p["tilt"], p["az"])
    return np.asarray(p["c"], float) + p["dist"] * np.asarray(d), np.asarray(p["c"], float)


def follow_to_points(win, pid):
    """"Edit as points": a follow path becomes a path through about one point per 15 % of its length (3.5)."""
    part = win.parts_panel.part(pid); plan = win.timeline.canvas.plan
    if part is None or plan is None:
        return
    poses = [f["pose"] for f in plan.frames if f.get("part") == pid and f["phase"] == "main"]
    if len(poses) < 2:
        win.say("The path is not ready yet.", "err"); return
    part["waypoints"] = points_from_poses(poses)
    part["source"] = dict(part.get("source") or {}, kind="points")
    win.editor.commit(select=pid); win.say(f"The path now goes through {len(part['waypoints'])} points you can adjust.", "ok")


def points_from_poses(poses, step=0.15):
    """Waypoints (camera position and look-at point) at about every `step` of the path's length."""
    pos = np.array([np.asarray(p.get("pos") if p.get("pos") is not None else p["c"], float) for p in poses])
    cum = np.r_[0, np.cumsum(np.linalg.norm(np.diff(pos, axis=0), axis=1))]
    k = max(2, int(round(1 / step)) + 1)
    out = []
    for d in np.linspace(0, cum[-1], k):
        i = int(np.argmin(np.abs(cum - d))); p = poses[i]
        P = np.asarray(p.get("pos") if p.get("pos") is not None else p["c"], float)
        out.append(dict(pos=[float(x) for x in P], focal=[float(x) for x in p["c"]], pause=0.0))
    return out


def old_path_parts(spec):
    """Camera paths saved before 3.6 that would move more calmly with the newer method (3.6 offer, R9)."""
    out = []
    for p in spec.get("shots", []):
        if p.get("type") != "path":
            continue
        pts = (p.get("source") or {}).get("kind") == "points"
        if (pts and p.get("interp") != 2) or (not pts and p.get("steadiness") is None) or not p.get("auto_flight"):
            out.append(p)
    return out


def make_paths_smoother(spec):
    """Paths through points: the 3.5 method (even speed, gentle stops); paths along a structure: the steady rail;
    both: calm flights in and out (3.6). Returns the number of parts changed."""
    parts = old_path_parts(spec)
    for p in parts:
        if (p.get("source") or {}).get("kind") == "points":
            p["interp"] = 2
        elif p.get("steadiness") is None:
            p["steadiness"] = 50.0
        p["auto_flight"] = True
    return len(parts)


def path_rows(panel, pid, p, form):
    win = panel.win

    def part():
        return panel.part(pid)

    def setcam(**kw):
        q = part()
        if q is not None:
            panel.set(pid, "camera", {**PATH_CAMERA, **(q.get("camera") or {}), **kw})
    src = p.get("source") or {}; kind = src.get("kind", "follow")
    form.addRow("The camera", _combo((("follows a structure", "follow"), ("moves through points", "points")), kind,
                                     lambda v: panel.set(pid, "source", dict(part().get("source") or {}, kind=v), rebuild=True)))
    if kind == "follow":
        items = [(it.get("structure"), it.get("component")) for it in src.get("items", [])]
        names = []
        for key, comp in items:
            names.append(panel._name(key) if comp is None else f"one object of {panel._name(key)}")
        lab = QtW.QLabel(", ".join(names) if names else "nothing chosen yet"); lab.setWordWrap(True)
        b = QtW.QToolButton(); b.setText("Structure ▾"); b.setPopupMode(QtW.QToolButton.InstantPopup); menu = QtW.QMenu(b); b.setMenu(menu)

        def fill():
            menu.clear()
            for c in (win.info or {}).get("structures", []):
                if c.get("mesh"):
                    menu.addAction(panel._name(c["key"]), lambda key=c["key"]: set_items([(key, None)]))
            menu.addSeparator(); menu.addAction("one object I click in the preview...", lambda: win.start_path_pick(pid))
        menu.aboutToShow.connect(fill)

        def set_items(its):
            q = part(); q["source"] = dict(q.get("source") or {}, kind="follow", items=[{"structure": k, "component": c} for k, c in its])
            win.editor.commit(select=pid); panel.fill_details(pid)
        rev = QtW.QPushButton("Reverse direction"); rev.setToolTip("Glide from the other end")
        rev.clicked.connect(lambda: panel.set(pid, "reverse", not part().get("reverse", False)))
        form.addRow("Follow", _row(lab, b, rev))
        from .paths import STEADINESS
        sv = p.get("steadiness")
        items = list(STEADINESS) + ([] if sv is not None else [("follows every bend (as before 3.5)", -1)])
        near = -1 if sv is None else min(STEADINESS, key=lambda it: abs(it[1] - float(sv)))[1]
        stc = _combo(items, near, lambda v: (panel.set(pid, "steadiness", None if v < 0 else float(v), rebuild=True)))
        stc.setToolTip("How calmly the camera moves: close follows the bends of the membrane, very steady glides along its overall line")
        sl = QtW.QSlider(QtC.Qt.Horizontal); sl.setRange(0, 100); sl.setFixedWidth(110); sl.setValue(int(round(float(sv or 50))))
        sl.setEnabled(sv is not None); sl.setToolTip("Fine control, from close (left) to very steady (right)")
        sl.sliderReleased.connect(lambda: panel.set(pid, "steadiness", float(sl.value())))
        form.addRow("Steadiness", _row(stc, sl))
        edit = QtW.QPushButton("Edit as points"); edit.setToolTip("Turn this path into a path through points that you can adjust one by one")
        edit.clicked.connect(lambda: follow_to_points(win, pid))
        form.addRow("", _row(edit))
        why = (win.timeline.canvas.plan.problems if win.timeline.canvas.plan else {}).get(pid)
        if why:
            n = QtW.QLabel(why); n.setStyleSheet(DIM); n.setWordWrap(True); form.addRow("", n)
    else:
        from . import gui_topview as tv
        above = QtW.QPushButton("Edit from above"); above.setCheckable(True); above.setChecked(tv.active(win, pid))
        above.setToolTip("Look straight down on the tomogram while you lay out the path: click to add points, drag them to "
                         "move them, or draw the whole path. Your view is back as it was when you switch this off (or press Esc).")
        above.toggled.connect(lambda on: tv.start(win, pid) if on else tv.stop(win))
        drawb = QtW.QPushButton("Draw the path"); drawb.setCheckable(True)
        drawb.setChecked(bool(tv.active(win, pid) and win.top_edit.get("draw"))); drawb.setEnabled(tv.active(win, pid))
        drawb.setToolTip("Then draw the path in the preview with the mouse, as with a pen: it becomes points (about one every "
                         "30 pixels) that you can move afterwards. Replaces the points the path has now (Undo brings them back).")

        def draw_on(on):
            if tv.active(win, pid):
                win.top_edit["draw"] = bool(on); win.prev.setCursor(QtC.Qt.CrossCursor if on else QtC.Qt.ArrowCursor)
                if on:
                    win.say("Draw the path in the preview, from where it starts to where it ends.")
        drawb.toggled.connect(draw_on)
        nz = win.st.ds.shape[0] if win.st.ds is not None else 100
        hz = _spin(-1, nz - 1, -1 if p.get("top_height") is None else float(p["top_height"]),
                   lambda v: panel.set(pid, "top_height", None if v < 0 else float(v)), step=1, decimals=0, prefix="height ")
        hz.setSpecialValueText("height: on what is clicked")
        hz.setToolTip("Height (z, in slices) of new points: 'on what is clicked' puts them on the structure or slice under the "
                      "mouse; a number puts every new point at that height. Change single points below, or in the strip under "
                      "the preview while editing from above.")
        form.addRow("Lay out", _row(above, drawb, hz))
        wps = p.get("waypoints") or []
        zs, own_z = point_values(p, "zoom", 1.0)
        fs, own_f = point_values(p, "fov", float(win.spec.get("style", {}).get("fov", 30)))
        new_interp = p.get("interp") == 2

        def upd(i, rebuild=False, **kw):
            ws = part()["waypoints"]; ws[i].update(kw)
            for k in [k for k, v in kw.items() if v is None]:
                ws[i].pop(k, None)
            panel.set(pid, "waypoints", ws, rebuild=rebuild)
        for i, w in enumerate(wps):
            num = QtW.QPushButton(f"{i + 1}"); num.setFixedWidth(30); num.setCheckable(True)
            num.setChecked(bool(win.path_point and win.path_point == (pid, i)))
            num.setToolTip("Select this point in the preview (its zoom and field of view open next to it)")
            num.clicked.connect(lambda _=False, i=i: win.select_path_point(pid, i))
            desc = QtW.QLineEdit(w.get("note", "")); desc.setPlaceholderText("a point" if w.get("pos") is None else "a view")
            desc.setToolTip("A short description, for example 'above the vesicle cluster'"); desc.setMinimumWidth(80)
            desc.editingFinished.connect(lambda i=i, d=desc: upd(i, note=d.text().strip() or None))
            pause = _spin(0, 30, float(w.get("pause", 0) or 0), lambda v, i=i: upd(i, pause=float(v)),
                          step=0.5, suffix=" s", decimals=1, prefix="stop ")
            pause.setToolTip("The camera stops here for this long")

            def move(d, i=i):
                ws = part()["waypoints"]
                if 0 <= i + d < len(ws):
                    ws[i], ws[i + d] = ws[i + d], ws[i]; panel.set(pid, "waypoints", ws, rebuild=True)

            def delete(i=i):
                ws = part()["waypoints"]; del ws[i]; panel.set(pid, "waypoints", ws, rebuild=True)
            btns = []
            for text, tip, fn in (("↑", "Move up", lambda _=False, m=move: m(-1)), ("↓", "Move down", lambda _=False, m=move: m(1)),
                                  ("✕", "Delete this point", lambda _=False, d=delete: d())):
                tb = QtW.QToolButton(); tb.setText(text); tb.setToolTip(tip); tb.clicked.connect(fn); btns.append(tb)
            hw = []
            q = w.get("point") if w.get("point") is not None else w.get("pos")
            if q is not None:
                def set_z(v, i=i):
                    w2 = part()["waypoints"][i]; key = "point" if w2.get("point") is not None else "pos"
                    upd(i, **{key: [float(w2[key][0]), float(w2[key][1]), float(v)]})
                hh = _spin(0, max(1, (win.st.ds.shape[0] if win.st.ds is not None else 100) - 1), float(q[2]), set_z,
                           step=1, decimals=0, prefix="z ")
                hh.setToolTip("Height of this point (slice number from the bottom)"); hw = [hh]
            form.addRow("Points" if i == 0 else "", _row(num, desc, pause, *hw, *btns))
            if new_interp:
                zl = QtW.QLabel(f"{zs[i]:.2f}x · {fs[i]:.0f}°" if own_z[i] or own_f[i] else f"<i>🔗 {zs[i]:.2f}x · {fs[i]:.0f}°</i>")
                zl.setToolTip("Zoom and field of view here; grey with a chain: kept from an earlier point")
                if not (own_z[i] or own_f[i]):
                    zl.setStyleSheet(DIM)
                extra = [zl]
                if i < len(wps) - 1:
                    sp = _combo((("slow", "slow"), ("normal", "normal"), ("fast", "fast")), w.get("speed", "normal"),
                                lambda v, i=i: upd(i, speed=v))
                    sp.setToolTip("Speed to the next point")
                    extra += [QtW.QLabel("to next"), sp]
                sg = QtW.QCheckBox("stop gently"); sg.setChecked(bool(w.get("stop_gently")))
                sg.setToolTip("Slow down to a stop at this point, then set off again")
                sg.toggled.connect(lambda on, i=i: upd(i, stop_gently=bool(on) or None)); extra.append(sg)
                form.addRow("", _row(*extra))
        if new_interp and wps:
            same = QtW.QCheckBox("Same zoom and field of view at all points"); same.setChecked(bool(p.get("same_zoom")))
            same.setToolTip("One zoom and field of view for the whole path; the points' own values are kept for later")
            same.toggled.connect(lambda on: panel.set(pid, "same_zoom", bool(on) or None, rebuild=True)); form.addRow("", same)
            if p.get("same_zoom"):
                pz = _spin(0.3, 4, float(p.get("path_zoom", 1.0)), lambda v: panel.set(pid, "path_zoom", float(v)), step=0.1, suffix=" x")
                pf = _spin(10, 60, float(p.get("path_fov", win.spec.get("style", {}).get("fov", 30))), lambda v: panel.set(pid, "path_fov", float(v)),
                           step=1, suffix="°", decimals=0)
                pz.setToolTip("Zoom for the whole path"); pf.setToolTip("Field of view for the whole path")
                form.addRow("Whole path", _row(pz, pf))
            za = _spin(0.3, 4, float(p.get("zoom_all", 1.0) or 1.0), lambda v: panel.set(pid, "zoom_all", float(v)), step=0.1, suffix=" x",
                       prefix="all points ")
            za.setToolTip("Zoom for the whole part: scales the zoom of every point together")
            play = QtW.QPushButton("Play path"); play.setToolTip("Play just this part in the preview")
            play.clicked.connect(lambda: win.play_part(pid))
            form.addRow("Zoom", _row(za, play))
        here = QtW.QPushButton("Add point at the current view")
        here.setToolTip("Stores where the camera is now in the preview")

        def add_here():
            v = current_view(win)
            if v is None:
                return
            ws = list(part().get("waypoints") or []); ws.append(dict(pos=[float(x) for x in v[0]], focal=[float(x) for x in v[1]], pause=0.0))
            panel.set(pid, "waypoints", ws, rebuild=True); win.editor.record()
        here.clicked.connect(add_here)
        click = QtW.QPushButton("Add point by clicking in the preview"); click.setCheckable(True); click.setChecked(win.waypoint_pick == pid)
        click.setToolTip("Click a structure or the slice; the camera will pass near it, looking at it. Esc ends.")
        click.toggled.connect(lambda on: win.start_waypoint_pick(pid) if on else win.end_waypoint_pick())
        form.addRow("" if wps else "Points", _row(here, click))
        click.setToolTip(click.toolTip() + " In the preview, drag a numbered point to move it on the slice; right-click it to delete it.")
    cam = dict(PATH_CAMERA, **(p.get("camera") or {}))
    dist = _spin(0.2, 5, cam["distance"], lambda v: setcam(distance=float(v)), suffix=" x")
    dist.setToolTip("How far the camera stays from the path, relative to the size of what is followed")
    side = _combo(SIDES, cam.get("side", "above"), lambda v: setcam(side=v))
    height = _spin(0, 85, cam.get("height", 30), lambda v: setcam(height=float(v)), step=5, suffix="°", decimals=0, prefix="height ")
    form.addRow("Camera", _row(dist, side, height))
    look = _combo(LOOKS, cam.get("look", "at_path"), lambda v: setcam(look=v))
    speed = _combo(SPEEDS, p.get("speed", "even"), lambda v: panel.set(pid, "speed", v))
    form.addRow("Looks", _row(look, QtW.QLabel("speed"), speed))
    oc, oo = sb.others_choice(p.get("others"))
    op = QtW.QSpinBox(); op.setRange(0, 100); op.setSuffix(" %"); op.setPrefix("opacity "); op.setValue(round(100 * oo)); op.setEnabled(oc != "hide")

    def colour(v):
        op.setEnabled(v != "hide"); panel.set(pid, "others", sb.set_others_choice(part().get("others"), colour=v))
    col = _combo((("unchanged", "keep"), ("grey", "grey"), ("darker", "darken"), ("hidden", "hide")), oc, colour)
    op.valueChanged.connect(lambda v: panel.set(pid, "others", sb.set_others_choice(part().get("others"), opacity=v / 100)))
    form.addRow("Other structures", _row(col, op))
    hb = _spin(0, 3, float(p.get("pause_before", 0) or 0), lambda v: panel.set(pid, "pause_before", float(v)), step=0.25,
               suffix=" s", decimals=2, prefix="hold at start ")
    ha = _spin(0, 3, float(p.get("pause_after", 0) or 0), lambda v: panel.set(pid, "pause_after", float(v)), step=0.25,
               suffix=" s", decimals=2, prefix="at end ")
    hb.setToolTip("The camera holds still at the first point for this long before it sets off")
    ha.setToolTip("The camera holds still at the last point for this long before the next part")
    calm = QtW.QCheckBox("calm flights in and out"); calm.setChecked(bool(p.get("auto_flight")))
    calm.setToolTip("The flights into and out of the path take as long as they need to turn gently (less than 2° per frame, "
                    "up to 4 s). Off: half a second, as before 3.6. Only when the transition is set to automatic.")
    calm.toggled.connect(lambda on: panel.set(pid, "auto_flight", bool(on) or None))
    form.addRow("Ends", _row(hb, ha, calm))
    show = QtW.QCheckBox("show the path in the preview"); show.setChecked(bool(p.get("show_path_in_preview", True)))
    show.toggled.connect(lambda v: panel.set(pid, "show_path_in_preview", bool(v))); form.addRow("", show)


# ---------------------------------------------------------------------------------------------------------------
# 3.5 phase 5: the path drawn on the preview (2D, fixed line widths), and the panel of a selected point
# ---------------------------------------------------------------------------------------------------------------
from PySide6 import QtGui as QtG  # noqa: E402

START, END, MID, SEL = "#2e9e44", "#d1495b", "#3a6fd8", "#ffd23f"


def _dashed(p, a, b, on=6, off=5):
    v = b - a; L = (v.x() ** 2 + v.y() ** 2) ** 0.5
    if L < 1e-6:
        return
    u = v / L; t = 0.0
    while t < L:
        p.drawLine(a + u * t, a + u * min(L, t + on)); t += on + off


def paint_path(p, prev, view, selected=None, hover=None):
    """Path lines 2 px (white with a dark edge, 70 %), dashed where hidden, chevrons every 80 px; numbered markers
    (S green, E red), a camera mark towards the look-at crosshair, the selected point ringed in yellow."""
    if not view or not prev.image_px():
        return
    p.setRenderHint(QtG.QPainter.Antialiasing)
    for line in view["lines"]:
        pts = [(prev.to_widget(x, y), vis) for x, y, vis, ok in line["pts"] if ok]
        dotted = line["kind"] == "look"
        for (a, va), (b, vb) in zip(pts, pts[1:]):
            vis = va and vb
            for col, wd in ((QtG.QColor(0, 0, 0, 120), 4.0), (QtG.QColor(255, 255, 255, 178), 2.0)):
                pen = QtG.QPen(col, wd); pen.setCapStyle(QtC.Qt.RoundCap); p.setPen(pen)
                if dotted or not vis:
                    _dashed(p, a, b, 2 if dotted else 6, 4 if dotted else 5)
                else:
                    p.drawLine(a, b)
        if line["kind"] != "look":  # chevrons show the direction
            acc = 0.0
            for (a, _), (b, _) in zip(pts, pts[1:]):
                v = b - a; L = (v.x() ** 2 + v.y() ** 2) ** 0.5; acc += L
                if acc >= 80 and L > 1e-6:
                    acc = 0.0; u = v / L; n = QtC.QPointF(-u.y(), u.x()); c = b
                    poly = QtG.QPolygonF([c - u * 5 + n * 4, c, c - u * 5 - n * 4])
                    p.setPen(QtG.QPen(QtG.QColor(0, 0, 0, 160), 3)); p.drawPolyline(poly)
                    p.setPen(QtG.QPen(QtG.QColor(255, 255, 255, 220), 1.5)); p.drawPolyline(poly)
    f = p.font(); f.setBold(True); f.setPointSizeF(max(7.0, f.pointSizeF() * 0.85)); p.setFont(f)
    for m in view["marks"]:
        cx, cy, cvis, cok = m["cam"]; lx, ly, lvis, lok = m["look"]
        c, l = prev.to_widget(cx, cy), prev.to_widget(lx, ly)
        if cok and lok:  # dotted line to where it looks, and a crosshair there
            p.setPen(QtG.QPen(QtG.QColor(255, 255, 255, 150), 1, QtC.Qt.DotLine)); p.drawLine(c, l)
        if lok:
            for col, wd in ((QtG.QColor(0, 0, 0, 150), 3), (QtG.QColor(255, 255, 255, 220), 1.2)):
                p.setPen(QtG.QPen(col, wd)); p.drawLine(l + QtC.QPointF(-6, 0), l + QtC.QPointF(6, 0)); p.drawLine(l + QtC.QPointF(0, -6), l + QtC.QPointF(0, 6))
        if not cok:
            continue
        sel = selected == m["i"]; big = sel or hover == m["i"]
        r = 11 if big else 9
        if lok:  # a small camera mark on the ring, pointing where it looks
            v = l - c; L = (v.x() ** 2 + v.y() ** 2) ** 0.5
            if L > 1e-6:
                u = v / L; n = QtC.QPointF(-u.y(), u.x()); tip = c + u * (r + 7)
                p.setPen(QtC.Qt.NoPen); p.setBrush(QtG.QColor(255, 255, 255, 230))
                p.drawPolygon(QtG.QPolygonF([tip, c + u * (r + 1) + n * 4, c + u * (r + 1) - n * 4]))
        col = START if m["kind"] == "start" else END if m["kind"] == "end" else MID
        fill = QtG.QColor(col)
        if not cvis:
            fill.setAlpha(150)
        p.setBrush(fill); p.setPen(QtG.QPen(QtG.QColor(SEL) if sel else QtG.QColor("#ffffff"), 2))
        p.drawEllipse(c, r, r)
        p.setPen(QtG.QColor("#ffffff"))
        txt = "S" if m["kind"] == "start" else "E" if m["kind"] == "end" else str(m["i"] + 1)
        p.drawText(QtC.QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), QtC.Qt.AlignCenter, txt)


ZOOM_RANGE = (0.3, 4.0)
FOV_RANGE = (10, 60)


class PointPanel(QtW.QFrame):
    """The small panel next to a selected path point: zoom, field of view, keep, look from here / set this view."""

    def __init__(self, win):
        super().__init__(win.prev); self.win = win; self.pid = None; self.i = None
        self.setObjectName("pointpanel")
        self.setStyleSheet("QFrame#pointpanel { background: palette(window); border: 1px solid palette(mid); border-radius: 6px } "
                           "QFrame#pointpanel QLabel, QFrame#pointpanel QCheckBox, QFrame#pointpanel QRadioButton { background: transparent; border: none }")
        g = QtW.QGridLayout(self); g.setContentsMargins(8, 6, 8, 6); g.setVerticalSpacing(3)
        self.title = QtW.QLabel("Point"); self.title.setStyleSheet("font-weight: 600")
        close = QtW.QToolButton(); close.setText("×"); close.setAutoRaise(True); close.setToolTip("Close")
        close.clicked.connect(self.close_panel)
        g.addWidget(self.title, 0, 0, 1, 3); g.addWidget(close, 0, 3)
        self.zoom = QtW.QSlider(QtC.Qt.Horizontal); self.zoom.setRange(0, 1000); self.zoom.setFixedWidth(140)
        self.zoom.setToolTip("How close the camera is to where it looks, at this point (0.3x to 4x)")
        self.zoom_lbl = QtW.QLabel("1.00x")
        self.fov = QtW.QSlider(QtC.Qt.Horizontal); self.fov.setRange(*FOV_RANGE); self.fov.setFixedWidth(140)
        self.fov.setToolTip("Field of view: small is telephoto (flat look), large is wide (strong perspective). "
                            "Changing it along the path with 'Keep the size the same' gives a dolly zoom.")
        self.fov_lbl = QtW.QLabel("30°")
        g.addWidget(QtW.QLabel("Zoom"), 1, 0); g.addWidget(self.zoom, 1, 1, 1, 2); g.addWidget(self.zoom_lbl, 1, 3)
        g.addWidget(QtW.QLabel("Field of view"), 2, 0); g.addWidget(self.fov, 2, 1, 1, 2); g.addWidget(self.fov_lbl, 2, 3)
        self.same_size = QtW.QCheckBox("Keep the size the same"); self.same_size.setChecked(True)
        self.same_size.setToolTip("When the field of view changes, change the distance too, so the target keeps its size on screen")
        g.addWidget(self.same_size, 3, 0, 1, 4)
        self.keep_next = QtW.QRadioButton("this zoom for the next points"); self.only_here = QtW.QRadioButton("only here")
        self.keep_next.setToolTip("The following points use this zoom and field of view until one has its own")
        self.only_here.setToolTip("Only this point; the next points go back to the zoom kept before")
        g.addWidget(QtW.QLabel("Keep"), 4, 0); g.addWidget(self.keep_next, 4, 1, 1, 2); g.addWidget(self.only_here, 5, 1, 1, 2)
        self.look_btn = QtW.QPushButton("Look from here"); self.look_btn.setToolTip("Show the preview from this point")
        self.set_btn = QtW.QPushButton("Set this view"); self.set_btn.setToolTip("Store the camera of the preview in this point")
        self.del_btn = QtW.QPushButton("Delete"); self.del_btn.setToolTip("Delete this point")
        h = QtW.QHBoxLayout(); h.addWidget(self.look_btn); h.addWidget(self.set_btn); h.addWidget(self.del_btn); g.addLayout(h, 6, 0, 1, 4)
        self.note = QtW.QLabel(""); self.note.setStyleSheet(DIM); self.note.setWordWrap(True); g.addWidget(self.note, 7, 0, 1, 4)
        self.zoom.valueChanged.connect(self._zoom); self.fov.valueChanged.connect(self._fov)
        self.zoom.sliderReleased.connect(self._commit); self.fov.sliderReleased.connect(self._commit)
        self.keep_next.toggled.connect(self._keep)
        self.look_btn.clicked.connect(lambda: self.win.look_from_point(self.pid, self.i))
        self.set_btn.clicked.connect(self._set_view); self.del_btn.clicked.connect(self._delete)
        self._busy = False; self.hide()

    @staticmethod
    def z_to_slider(z):
        a, b = np.log(ZOOM_RANGE[0]), np.log(ZOOM_RANGE[1]); return int(round(1000 * (np.log(z) - a) / (b - a)))

    @staticmethod
    def slider_to_z(v):
        a, b = np.log(ZOOM_RANGE[0]), np.log(ZOOM_RANGE[1]); return float(np.exp(a + (b - a) * v / 1000))

    def part(self):
        return self.win.parts_panel.part(self.pid) if self.pid else None

    def show_for(self, pid, i):
        self.pid, self.i = pid, i; self.refresh(); self.place(); self.show(); self.raise_()

    def refresh(self):
        p = self.part()
        if p is None or self.i is None or self.i >= len(p.get("waypoints") or []):
            self.hide(); return
        self._busy = True
        zs, own_z = point_values(p, "zoom", 1.0)
        fs, _ = point_values(p, "fov", float(self.win.spec.get("style", {}).get("fov", 30)))
        w = p["waypoints"][self.i]
        self.title.setText(f"Point {self.i + 1}" + (f" · {w['note']}" if w.get("note") else ""))
        self.zoom.setValue(self.z_to_slider(zs[self.i])); self.zoom_lbl.setText(f"{zs[self.i]:.2f}x")
        self.fov.setValue(int(round(fs[self.i]))); self.fov_lbl.setText(f"{fs[self.i]:.0f}°")
        only = bool(w.get("zoom_only_here")); self.only_here.setChecked(only); self.keep_next.setChecked(not only)
        same = bool(p.get("same_zoom"))
        for x in (self.zoom, self.fov, self.keep_next, self.only_here):
            x.setEnabled(not same)
        self.note.setText("The whole path uses one zoom (part settings)." if same else
                          ("" if own_z[self.i] else "Kept from an earlier point; moving a slider gives this point its own."))
        self.note.setVisible(bool(self.note.text())); self.adjustSize(); self._busy = False

    def place(self):
        rects = self.win.st.ren.last_rects if self.win.st.ren is not None else {}
        r = rects.get(f"marker:{self.i}"); prev = self.win.prev
        img = prev._img
        if r and img is not None:
            W, H = img.width(), img.height(); x1, y0 = r["box"][2] / W, r["box"][1] / H
            pt = prev.to_widget(x1, y0).toPoint() + QtC.QPoint(12, 0)
        else:
            pt = QtC.QPoint(prev.width() - self.width() - 40, 40)
        x = min(max(4, pt.x()), prev.width() - self.width() - 4); y = min(max(4, pt.y()), prev.height() - self.height() - 4)
        self.move(x, y)

    def _wp(self):
        p = self.part()
        return (p, p["waypoints"][self.i]) if p is not None and self.i is not None and self.i < len(p.get("waypoints") or []) else (None, None)

    def _zoom(self, v):
        if self._busy:
            return
        p, w = self._wp()
        if w is None:
            return
        z = self.slider_to_z(v); w["zoom"] = round(z, 4); self.zoom_lbl.setText(f"{z:.2f}x")
        self.win.preview_point(self.pid, self.i)

    def _fov(self, v):
        if self._busy:
            return
        p, w = self._wp()
        if w is None:
            return
        fs, _ = point_values(p, "fov", float(self.win.spec.get("style", {}).get("fov", 30)))
        zs, _ = point_values(p, "zoom", 1.0)
        old = fs[self.i]; w["fov"] = float(v); self.fov_lbl.setText(f"{v}°")
        if self.same_size.isChecked():  # d * tan(fov / 2) stays the same: the target keeps its size on screen
            z = zs[self.i] * np.tan(np.radians(v) / 2) / np.tan(np.radians(old) / 2)
            w["zoom"] = round(float(z), 4); self._busy = True; self.zoom.setValue(self.z_to_slider(z)); self.zoom_lbl.setText(f"{z:.2f}x"); self._busy = False
        self.win.preview_point(self.pid, self.i)

    def _keep(self, on):
        if self._busy:
            return
        p, w = self._wp()
        if w is None:
            return
        zs, _ = point_values(p, "zoom", 1.0); fs, _ = point_values(p, "fov", float(self.win.spec.get("style", {}).get("fov", 30)))
        w["zoom"] = zs[self.i]; w["fov"] = fs[self.i]  # the point gets its own values
        w["zoom_only_here"] = w["fov_only_here"] = not on
        self._commit()

    def _commit(self):
        if self.pid:
            self.win.editor.commit(select=self.pid); self.refresh()

    def _set_view(self):
        v = current_view(self.win); p, w = self._wp()
        if v is None or w is None:
            return
        w["pos"] = [float(x) for x in v[0]]; w["focal"] = [float(x) for x in v[1]]; w.pop("point", None)
        self._commit(); self.win.say(f"Point {self.i + 1} now uses this view.", "ok")

    def _delete(self):
        p, w = self._wp()
        if w is None:
            return
        del p["waypoints"][self.i]; i = self.i; self.close_panel(); self.win.editor.commit(select=self.pid)
        self.win.say(f"Deleted point {i + 1}. Undo brings it back.", "ok")

    def close_panel(self):
        self.hide(); self.win.path_point = None; self.win.prev.update()
