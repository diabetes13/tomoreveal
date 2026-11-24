"""Settings of a camera path part in the Movie tab: what to follow or which points, and how the camera moves."""
import numpy as np
from PySide6 import QtCore as QtC, QtWidgets as QtW

from . import storyboard as sb
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


def path_rows(panel, pid, p, form):
    win = panel.win

    def part():
        return panel.part(pid)

    def setcam(**kw):
        q = part()
        if q is not None:
            panel.set(pid, "camera", {**sb.PATH_CAMERA, **(q.get("camera") or {}), **kw})
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
        wps = p.get("waypoints") or []
        for i, w in enumerate(wps):
            what = "view" if w.get("pos") is not None else "point"
            lab = QtW.QLabel(f"{i + 1}: {'a view' if what == 'view' else 'a point'}")
            lab.setToolTip("A camera position stored with 'Add point at the current view'" if what == "view" else
                           "A point you clicked; the camera passes near it, looking at it")
            pause = _spin(0, 30, float(w.get("pause", 0) or 0), lambda v, i=i: (part()["waypoints"][i].update(pause=float(v)), panel.set(pid, "waypoints", part()["waypoints"])),
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
            form.addRow("Points" if i == 0 else "", _row(lab, pause, *btns))
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
        form.addRow("" if wps else "Points", _row(here)); form.addRow("", _row(click))
        tip = QtW.QLabel("In the preview, drag a numbered point to move it on the slice; right-click it to delete it.")
        tip.setStyleSheet(DIM); tip.setWordWrap(True); form.addRow("", tip)
    cam = dict(sb.PATH_CAMERA, **(p.get("camera") or {}))
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
    show = QtW.QCheckBox("show the path in the preview"); show.setChecked(bool(p.get("show_path_in_preview", True)))
    show.toggled.connect(lambda v: panel.set(pid, "show_path_in_preview", bool(v))); form.addRow("", show)
