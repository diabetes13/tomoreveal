"""Overlay items in the window: the "On the picture" settings, the quick toolbar above the preview, dragging and
resizing items in the preview, and the voxel size in the Data tab."""
import numpy as np
from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from . import overlays as ov
from .gui_theme import DIM

SNAP = 0.015      # fraction of the frame within which an item snaps to an edge, a corner or a centre line
MARGIN = 0.02     # the edges an item snaps to sit this far inside the frame
HANDLE = 12       # resize handle size in screen pixels
SHORT = {"legend": "Legend", "slice_label": "Slice no.", "scale_bar": "Scale bar", "title": "Title", "caption": "Captions"}
PART_ITEMS = (("legend", "legend"), ("slice_label", "slice number"), ("scale_bar", "scale bar"))


def _slider(lo, hi, value, on_change, fmt):
    w = QtW.QWidget(); h = QtW.QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
    s = QtW.QSlider(QtC.Qt.Horizontal); s.setRange(lo, hi); s.setValue(int(value)); lab = QtW.QLabel(fmt(int(value))); lab.setFixedWidth(44)
    s.valueChanged.connect(lambda v: (lab.setText(fmt(v)), on_change(v)))
    h.addWidget(s, 1); h.addWidget(lab); w.slider, w.label, w.fmt = s, lab, fmt
    w.setToolTip = lambda t, s=s, f=w.setToolTip: (f(t), s.setToolTip(t))  # the tooltip goes to the slider too
    return w


def _set_slider(w, v):
    w.slider.blockSignals(True); w.slider.setValue(int(round(v))); w.slider.blockSignals(False); w.label.setText(w.fmt(int(round(v))))


def _quiet(widget, fn):
    widget.blockSignals(True)
    try:
        fn()
    finally:
        widget.blockSignals(False)


class OverlayControls(QtC.QObject):
    """The Look tab group "On the picture" (each item: shown, size, reset position, and its own options)."""

    def __init__(self, win, layout):
        super().__init__(win); self.win = win
        g = QtW.QGroupBox("On the picture"); self.group = g; layout.addWidget(g); grid = QtW.QGridLayout(g)
        self.cb, self.size = {}, {}
        for r, n in enumerate(ov.NAMES):
            cb = QtW.QCheckBox(ov.LABELS[n]); cb.toggled.connect(lambda v, n=n: self.set_show(n, v))
            sz = _slider(50, 300, 100, lambda v, n=n: self.set(n, size=v / 100), lambda v: f"{v / 100:.1f}x")
            sz.setToolTip(f"Size of the {ov.LABELS[n].lower()} (also: drag its corner in the preview)")
            rb = QtW.QPushButton("Reset position"); rb.setToolTip("Put it back where it was at first")
            rb.clicked.connect(lambda _=False, n=n: self.reset(n))
            grid.addWidget(cb, r, 0); grid.addWidget(sz, r, 1); grid.addWidget(rb, r, 2)
            self.cb[n], self.size[n] = cb, sz
        grid.setColumnStretch(1, 1); r = len(ov.NAMES)
        # legend options
        lo = QtW.QHBoxLayout(); grid.addLayout(lo, r, 0, 1, 3); r += 1
        self.leg_title = QtW.QLineEdit(); self.leg_title.setPlaceholderText("legend title (optional)")
        self.leg_title.textChanged.connect(lambda t: self.set("legend", title=t))
        self.leg_cols = QtW.QComboBox(); self.leg_cols.addItem("one column", 1); self.leg_cols.addItem("two columns", 2)
        self.leg_cols.setToolTip("List the structures in one or two columns")
        self.leg_cols.activated.connect(lambda j: self.set("legend", columns=self.leg_cols.itemData(j)))
        self.leg_box = QtW.QCheckBox("box"); self.leg_box.setToolTip("A see-through box behind the legend")
        self.leg_box.toggled.connect(lambda v: self.set("legend", box=v))
        self.leg_op = _slider(0, 100, 47, lambda v: self.set("legend", box_opacity=v / 100), lambda v: f"{v} %")
        self.leg_op.setToolTip("How dark (or light) the legend box is")
        lo.addWidget(QtW.QLabel("Legend:")); lo.addWidget(self.leg_title, 1); lo.addWidget(self.leg_cols); lo.addWidget(self.leg_box)
        lo.addWidget(self.leg_op)
        # slice number
        self.depth_cb = QtW.QCheckBox("Slice number: also show the depth in nm")
        self.depth_cb.toggled.connect(lambda v: self.set("slice_label", show_depth=v)); grid.addWidget(self.depth_cb, r, 0, 1, 3); r += 1
        # scale bar
        sb = QtW.QHBoxLayout(); grid.addLayout(sb, r, 0, 1, 3); r += 1
        self.bar_auto = QtW.QCheckBox("automatic length"); self.bar_auto.toggled.connect(self._bar_length)
        self.bar_len = QtW.QDoubleSpinBox(); self.bar_len.setRange(0.1, 100000); self.bar_len.setDecimals(1); self.bar_len.setSuffix(" nm")
        self.bar_len.setValue(100); self.bar_len.valueChanged.connect(lambda _v: self._bar_length())
        self.bar_mode = QtW.QComboBox(); self.bar_mode.addItem("flat on the picture", "screen"); self.bar_mode.addItem("lying on the slice", "slice")
        self.bar_mode.setToolTip("Flat on the picture: exact at the centre of view. Lying on the slice: exact in every view.")
        self.bar_mode.activated.connect(lambda j: self.set("scale_bar", mode=self.bar_mode.itemData(j)))
        self.bar_label = QtW.QCheckBox("label"); self.bar_label.toggled.connect(lambda v: self.set("scale_bar", label=v))
        sb.addWidget(QtW.QLabel("Scale bar:")); sb.addWidget(self.bar_auto); sb.addWidget(self.bar_len); sb.addWidget(self.bar_mode)
        sb.addWidget(self.bar_label); sb.addStretch(1)
        self.bar_note = QtW.QLabel(""); self.bar_note.setStyleSheet(DIM); self.bar_note.setWordWrap(True); grid.addWidget(self.bar_note, r, 0, 1, 3); r += 1
        note = QtW.QLabel("Drag an item in the preview to move it; drag its corner handle to change its size (hold Shift for steps "
                          "of 0.1). Right-click an item for more.")
        note.setStyleSheet(DIM); note.setWordWrap(True); grid.addWidget(note, r, 0, 1, 3)

    # ---- changes ----
    def _ok(self):
        return self.win.spec is not None

    def set_show(self, n, v):
        if self._ok():
            ov.set_show(self.win.spec, n, v); self.sync(); self.win.toolbar.sync(); self.win.schedule()

    def set(self, n, **kw):
        if self._ok():
            ov.set_item(self.win.spec, n, **kw); self.win.schedule()

    def reset(self, n):
        if self._ok():
            ov.reset_position(self.win.spec, n); self.win.schedule(0)

    def _bar_length(self):
        auto = self.bar_auto.isChecked(); self.bar_len.setEnabled(not auto)
        self.set("scale_bar", length_nm="auto" if auto else float(self.bar_len.value()))

    # ---- showing the settings ----
    def sync(self):
        if not self._ok():
            return
        items = ov.overlay_settings(self.win.spec)
        for n in ov.NAMES:
            _quiet(self.cb[n], lambda n=n: self.cb[n].setChecked(bool(items[n]["show"])))
            _set_slider(self.size[n], 100 * float(items[n].get("size", 1.0)))
        lg = items["legend"]
        _quiet(self.leg_title, lambda: self.leg_title.setText(lg.get("title", "")))
        self.leg_cols.setCurrentIndex(1 if int(lg.get("columns", 1)) == 2 else 0)
        _quiet(self.leg_box, lambda: self.leg_box.setChecked(bool(lg.get("box", True))))
        _set_slider(self.leg_op, 100 * float(lg.get("box_opacity", 0.47)))
        _quiet(self.depth_cb, lambda: self.depth_cb.setChecked(bool(items["slice_label"].get("show_depth"))))
        b = items["scale_bar"]; auto = b.get("length_nm", "auto") in ("auto", None)
        _quiet(self.bar_auto, lambda: self.bar_auto.setChecked(auto))
        _quiet(self.bar_len, lambda: self.bar_len.setValue(100.0 if auto else float(b["length_nm"]))); self.bar_len.setEnabled(not auto)
        self.bar_mode.setCurrentIndex(1 if b.get("mode") == "slice" else 0)
        _quiet(self.bar_label, lambda: self.bar_label.setChecked(bool(b.get("label", True))))
        vox = ov.voxel_size(self.win.spec, self.win.st.ds)
        ortho = bool(self.win.spec.get("style", {}).get("ortho"))
        self.bar_note.setText("The scale bar needs the voxel size (Data tab)." if not vox else
                              "Lying on the slice, the bar is exact in every view. Flat on the picture, it is exact "
                              + ("everywhere (orthographic view)." if ortho else "at the centre of view; things nearer or further "
                                 "away look bigger or smaller. Switch on Orthographic view for an exact bar everywhere."))


class OverlayToolbar(QtW.QWidget):
    """A row of toggle buttons above the preview for quick switching, and "Layout boxes"."""

    def __init__(self, win):
        super().__init__(); self.win = win; h = QtW.QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 2); h.setSpacing(3)
        self.btn = {}
        lab = QtW.QLabel("On the picture:"); lab.setStyleSheet(DIM); h.addWidget(lab)
        for n in ov.NAMES:
            b = QtW.QToolButton(); b.setText(SHORT[n]); b.setCheckable(True); b.setToolTip(f"Show or hide the {ov.LABELS[n].lower()}")
            b.toggled.connect(lambda v, n=n: win.overlay_controls.set_show(n, v)); h.addWidget(b); self.btn[n] = b
        h.addStretch(1)
        self.boxes = QtW.QToolButton(); self.boxes.setText("Layout boxes"); self.boxes.setCheckable(True)
        self.boxes.setToolTip("Draw dashed boxes around every item, also hidden ones (grey), so you can find and move them")
        self.boxes.toggled.connect(lambda _v: win.prev.update()); h.addWidget(self.boxes)

    def sync(self):
        if self.win.spec is None:
            return
        items = ov.overlay_settings(self.win.spec)
        for n, b in self.btn.items():
            _quiet(b, lambda b=b, n=n: b.setChecked(bool(items[n]["show"])))


def part_overlay_row(win, pid, p):
    """'On the picture during this part': as default / show / hide for legend, slice number and scale bar."""
    w = QtW.QWidget(); h = QtW.QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
    cur = p.get("overlays") or {}
    for name, text in PART_ITEMS:
        cb = QtW.QComboBox(); cb.setToolTip(f"The {text} during this part"); cb.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon); cb.setMinimumContentsLength(8)
        for t, d in ((f"{text}: as default", "default"), (f"{text}: show", "show"), (f"{text}: hide", "hide")):
            cb.addItem(t, d)
        cb.setCurrentIndex(max(0, cb.findData(cur.get(name, "default"))))

        def chose(j, name=name, cb=cb):
            part = win.parts_panel.part(pid)
            if part is not None:
                o = dict(part.get("overlays") or {}); o[name] = cb.itemData(j)
                win.parts_panel.set(pid, "overlays", {k: v for k, v in o.items() if v != "default"} or None)
        cb.activated.connect(chose); h.addWidget(cb)
    h.addStretch(1)
    return w


class OverlayDrag(QtC.QObject):
    """Moving and resizing overlay items with the mouse in the preview. The preview calls press / move / release /
    hover / context / double and paints what `paint` draws."""

    def __init__(self, win):
        super().__init__(win); self.win = win; self.drag = None; self.hover_name = None; self.guides = []

    # ---- items: overlay items, and the text / picture blocks of the card under the playhead ----
    def card(self):
        w = self.win; plan = w.timeline.canvas.plan
        if w.spec is None or plan is None or not (0 <= w.frame < len(plan.frames)):
            return None
        pid = plan.frames[w.frame].get("card")
        return next((p for p in w.spec["shots"] if p["id"] == pid), None) if pid else None

    def path_part(self):
        """The selected camera path part through points, when the preview shows its markers."""
        w = self.win; pid = w.selected_part
        p = next((q for q in (w.spec or {}).get("shots", []) if q["id"] == pid), None)
        return p if p and p.get("type") == "path" and (p.get("source") or {}).get("kind") == "points" else None

    def item(self, name):
        if name.startswith("marker:"):
            return None
        if name.startswith("block:"):
            c = self.card(); i = int(name[6:])
            return c["blocks"][i] if c and i < len(c.get("blocks", [])) else None
        return ov.overlay_settings(self.win.spec)[name]

    def update(self, name, **kw):
        if name.startswith("block:"):
            it = self.item(name)
            if it is not None:
                it.update(kw)
        else:
            ov.set_item(self.win.spec, name, **kw)

    @staticmethod
    def size_field(item):
        return "height" if item.get("kind") == "image" else "size"

    # ---- geometry ----
    def image_size(self):
        img = self.win.prev._img
        return (img.width(), img.height()) if img is not None else None

    def rects(self):
        """{name: (box as fractions of the frame, shown)} of the last drawn preview frame."""
        ren, sz = self.win.st.ren, self.image_size()
        if ren is None or sz is None or self.win.spec is None:
            return {}
        W, H = sz
        return {n: ((r["box"][0] / W, r["box"][1] / H, r["box"][2] / W, r["box"][3] / H), r["shown"])
                for n, r in (getattr(ren, "last_rects", None) or {}).items()}

    def hit(self, f):
        if f is None:
            return None
        show_all = self.win.toolbar.boxes.isChecked(); rects = self.rects()
        blocks = sorted((n for n in rects if n.startswith("block:")), key=lambda n: -int(n[6:])) if self.card() else []
        markers = [n for n in rects if n.startswith("marker:")] if self.path_part() else []
        for n in markers + blocks + ["caption", "scale_bar", "title", "slice_label", "legend"]:
            r = rects.get(n)
            if r and (r[1] or show_all):
                x0, y0, x1, y1 = r[0]
                if x0 <= f[0] <= x1 and y0 <= f[1] <= y1:
                    return n
        return None

    def handle_point(self, name):
        """The resize handle: the corner of the box furthest from its anchor (fractions)."""
        r = self.rects().get(name)
        if not r:
            return None
        it = self.item(name)
        if it is None:
            return None
        x0, y0, x1, y1 = r[0]; a = it.get("anchor") or "top-left"
        v, hz = a.split("-")
        return (x0 if hz == "right" else x1, y0 if v == "bottom" else y1)

    def on_handle(self, name, f):
        hp, sz = self.handle_point(name), self.win.prev.image_px()
        if hp is None or f is None or sz is None:
            return False
        return abs(hp[0] - f[0]) * sz[0] <= HANDLE and abs(hp[1] - f[1]) * sz[1] <= HANDLE

    # ---- mouse ----
    def press(self, f, mods):
        """True when the press starts moving or resizing an item (the camera does not move then)."""
        w = self.win
        if w.spec is None or w.pick_mode or w.gen is not None or w.busy or f is None:
            return False
        name = self.hit(f)
        if name and name.startswith("marker:"):  # a waypoint of a camera path: moved on the slice plane
            self.drag = dict(name=name, mode="marker", f0=f); return True
        hs = [n for n in self.rects() if (not n.startswith(("block:", "marker:")) or self.card()) and self.on_handle(n, f)
              and (self.rects()[n][1] or w.toolbar.boxes.isChecked())]
        if hs:
            name = hs[0]; mode = "resize"
        elif name:
            mode = "move"
        else:
            return False
        box = self.rects()[name][0]; item = self.item(name)
        if item is None:
            return False
        if item.get("x") is None or item.get("y") is None:  # at its default place: give it an explicit position (nothing moves)
            sz = self.image_size(); anchor, ax, ay = ov.anchor_for([box[0] * sz[0], box[1] * sz[1], box[2] * sz[0], box[3] * sz[1]], *sz)
            self.update(name, x=ax, y=ay, anchor=anchor if mode == "move" else item.get("anchor") or anchor)
            if mode == "resize":  # keep the anchor where it is, so the box grows away from it
                v, hz = (item.get("anchor") or anchor).split("-")
                self.update(name, x={"left": box[0], "center": (box[0] + box[2]) / 2, "right": box[2]}[hz],
                            y={"top": box[1], "middle": (box[1] + box[3]) / 2, "bottom": box[3]}[v])
        item = self.item(name); fld = self.size_field(item)
        self.drag = dict(name=name, mode=mode, f0=f, box0=box, size0=float(item.get(fld, 1.0)), field=fld,
                         anchor_pt=(float(item["x"]), float(item["y"])))
        return True

    def move(self, f, mods):
        d = self.drag; w = self.win
        if d is None or f is None:
            return
        if d["mode"] == "marker":
            p = self.path_part(); i = int(d["name"][7:])
            if p is None or i >= len(p.get("waypoints") or []):
                return
            wp = p["waypoints"][i]; old = np.asarray(wp.get("focal") or wp.get("point"), float)
            plan = w.timeline.canvas.plan; k = float(plan.frames[w.frame]["k"]) if plan else old[2]
            new = w.st.ren.to_plane(f[0], f[1], k)
            if new is None:
                return
            new[2] = old[2] if wp.get("pos") is not None else k
            if wp.get("pos") is not None:  # a stored view: camera and point move together
                wp["pos"] = [float(v) for v in np.asarray(wp["pos"], float) + (new - old)]; wp["focal"] = [float(v) for v in new]
            else:
                wp["point"] = [float(v) for v in new]
            w.req = {"frame": w.frame}; w.do_preview(quick=True); w.prev.update()
            return
        if d["mode"] == "move":
            dx, dy = f[0] - d["f0"][0], f[1] - d["f0"][1]
            x0, y0, x1, y1 = d["box0"]; bw, bh = x1 - x0, y1 - y0
            nx0, ny0 = min(max(0.0, x0 + dx), 1 - bw), min(max(0.0, y0 + dy), 1 - bh)
            nx0, ny0, self.guides = self.snap(nx0, ny0, bw, bh)
            sz = self.image_size()
            anchor, ax, ay = ov.anchor_for([nx0 * sz[0], ny0 * sz[1], (nx0 + bw) * sz[0], (ny0 + bh) * sz[1]], *sz)
            self.update(d["name"], x=ax, y=ay, anchor=anchor)
        else:
            ax, ay = d["anchor_pt"]; sz = self.image_size()
            d0 = max(1e-6, ((d["f0"][0] - ax) * sz[0]) ** 2 + ((d["f0"][1] - ay) * sz[1]) ** 2) ** 0.5
            d1 = (((f[0] - ax) * sz[0]) ** 2 + ((f[1] - ay) * sz[1]) ** 2) ** 0.5
            lo, hi = {"size": ov.SIZE_RANGE if not d["name"].startswith("block:") else (0.3, 3.3), "height": (0.02, 1.0)}[d["field"]]
            s = min(hi, max(lo, d["size0"] * d1 / d0))
            if mods & QtC.Qt.ShiftModifier:
                s = round(s * (10 if d["field"] == "size" else 100)) / (10 if d["field"] == "size" else 100)
            self.update(d["name"], **{d["field"]: s})
        w.req = {"frame": w.frame}; w.do_preview(quick=True); w.prev.update()

    def snap(self, x0, y0, bw, bh):
        """Snap a box (fractions) to the frame edges (with a small margin), corners and centre lines."""
        guides = []
        for axis, lo, size in (("x", x0, bw), ("y", y0, bh)):
            best = None
            for edge, pos in ((lo, MARGIN), (lo + size, 1 - MARGIN), (lo + size / 2, 0.5)):
                if abs(edge - pos) <= SNAP and (best is None or abs(edge - pos) < best[0]):
                    best = (abs(edge - pos), pos - edge, pos)
            if best:
                lo += best[1]; guides.append((axis, best[2]))
            if axis == "x":
                x0 = lo
            else:
                y0 = lo
        return x0, y0, guides

    def release(self):
        if self.drag is None:
            return
        name = self.drag["name"]; self.drag = None; self.guides = []
        if name.startswith("marker:"):
            p = self.path_part()
            if p is not None:
                self.win.editor.commit(select=p["id"])
            return
        w = self.win; w.overlay_controls.sync(); w.editor.record(); w.schedule(0); w.prev.update()
        c = self.card() if name.startswith("block:") else None
        if c is not None and w.parts_panel.open_id == c["id"]:
            w.parts_panel.fill_details(c["id"])

    def hover(self, f):
        name = self.hit(f) if self.drag is None else self.drag["name"]
        on_h = name is not None and self.on_handle(name, f)
        if name != self.hover_name:
            self.hover_name = name; self.win.prev.update()
        if self.win.pick_mode:
            return
        self.win.prev.setCursor(QtC.Qt.SizeFDiagCursor if on_h else QtC.Qt.SizeAllCursor if name else QtC.Qt.ArrowCursor)

    def context(self, f, gpos):
        name = self.hit(f); w = self.win
        if name is None or w.spec is None:
            return False
        if name.startswith("marker:"):
            p = self.path_part(); i = int(name[7:]); m = QtW.QMenu(w)

            def delete():
                del p["waypoints"][i]; w.editor.commit(select=p["id"])
            m.addAction(f"Delete point {i + 1}", delete); m.exec(gpos)
            return True
        m = QtW.QMenu(w); item = self.item(name)

        def act(fn):
            fn(); w.overlay_controls.sync(); w.toolbar.sync(); w.editor.record(); w.schedule(0)
        if name.startswith("block:"):
            c = self.card(); i = int(name[6:]); fld = self.size_field(item); val = float(item.get(fld, 1.0))
            hi = 1.0 if fld == "height" else 3.3
            m.addAction("Bigger", lambda: act(lambda: item.update({fld: min(hi, val * 1.2)})))
            m.addAction("Smaller", lambda: act(lambda: item.update({fld: val / 1.2})))
            m.addSeparator(); m.addAction("Delete", lambda: act(lambda: c["blocks"].pop(i)))
            m.exec(gpos)
            if w.parts_panel.open_id == c["id"]:
                w.parts_panel.fill_details(c["id"])
            return True
        m.addAction("Hide", lambda: act(lambda: ov.set_show(w.spec, name, False)))
        m.addAction("Reset position", lambda: act(lambda: ov.reset_position(w.spec, name)))
        m.addAction("Bigger", lambda: act(lambda: ov.set_item(w.spec, name, size=min(3.0, float(item.get("size", 1)) * 1.2))))
        m.addAction("Smaller", lambda: act(lambda: ov.set_item(w.spec, name, size=max(0.5, float(item.get("size", 1)) / 1.2))))
        if name == "legend":
            m.addSeparator()
            m.addAction("One column", lambda: act(lambda: ov.set_item(w.spec, name, columns=1)))
            m.addAction("Two columns", lambda: act(lambda: ov.set_item(w.spec, name, columns=2)))
            m.addAction("Box off" if item.get("box", True) else "Box on",
                        lambda: act(lambda: ov.set_item(w.spec, name, box=not item.get("box", True))))
        m.exec(gpos)
        return True

    def double(self, f):
        name = self.hit(f)
        if name is None:
            return False
        w = self.win
        if name.startswith("block:"):
            w.open_part(self.card()["id"]); return True
        w.tabs.setCurrentIndex(w.TAB_LOOK)
        QtC.QTimer.singleShot(0, lambda: w._scroll_to(w.overlay_controls.group))
        return True

    # ---- painting on the preview ----
    def paint(self, p, to_widget):
        """Dashed boxes (hovered item, or all items with Layout boxes), the resize handle and snap guides."""
        rects = self.rects(); show_all = self.win.toolbar.boxes.isChecked()
        pal = self.win.prev.palette()
        for n, (box, shown) in rects.items():
            if not (show_all or n == self.hover_name or (self.drag and self.drag["name"] == n)):
                continue
            if not shown and not show_all:
                continue
            a, b = to_widget(box[0], box[1]), to_widget(box[2], box[3])
            pen = QtG.QPen(pal.highlight().color() if shown else QtG.QColor(150, 150, 150), 1, QtC.Qt.DashLine)
            p.setPen(pen); p.setBrush(QtC.Qt.NoBrush); p.drawRect(QtC.QRectF(a, b))
            if shown and (n == self.hover_name or (self.drag and self.drag["name"] == n)):
                hp = self.handle_point(n)
                if hp:
                    c = to_widget(*hp); p.setBrush(pal.highlight()); p.setPen(QtC.Qt.NoPen)
                    p.drawRect(QtC.QRectF(c.x() - 4, c.y() - 4, 8, 8))
        for axis, pos in self.guides:
            p.setPen(QtG.QPen(QtG.QColor(255, 80, 200), 1))
            if axis == "x":
                p.drawLine(to_widget(pos, 0), to_widget(pos, 1))
            else:
                p.drawLine(to_widget(0, pos), to_widget(1, pos))


class VoxelRow(QtW.QWidget):
    """Data tab: the voxel size (from the file, or the user's value) with a warning when the header looks wrong."""

    def __init__(self, win):
        super().__init__(); self.win = win
        v = QtW.QVBoxLayout(self); v.setContentsMargins(0, 6, 0, 0)
        h = QtW.QHBoxLayout(); v.addLayout(h)
        h.addWidget(QtW.QLabel("<b>Voxel size</b>"))
        self.spin = QtW.QDoubleSpinBox(); self.spin.setRange(0.0, 1000.0); self.spin.setDecimals(3); self.spin.setSuffix(" nm")
        self.spin.setSpecialValueText("unknown"); self.spin.setToolTip("Size of one voxel in nanometres; used by the scale bar")
        self.spin.valueChanged.connect(self.changed); h.addWidget(self.spin)
        self.reset_btn = QtW.QPushButton("Use the file's value"); self.reset_btn.clicked.connect(self.reset); h.addWidget(self.reset_btn)
        h.addStretch(1)
        self.note = QtW.QLabel(""); self.note.setWordWrap(True); v.addWidget(self.note)

    def sync(self):
        w = self.win
        if w.spec is None:
            self.setVisible(False); return
        self.setVisible(True)
        file_v = w.st.ds.vox; own = (w.spec.get("data") or {}).get("voxel_size_nm")
        val = float(own) if own else (float(file_v) / 10.0 if file_v else 0.0)
        _quiet(self.spin, lambda: self.spin.setValue(val))
        self.reset_btn.setVisible(bool(own))
        from .gui_theme import message_color
        if own:
            self.note.setText(f"{val:g} nm per voxel (your value; the file says "
                              f"{(file_v / 10.0) if file_v else 'nothing'}{' nm' if file_v else ''})."); self.note.setStyleSheet(DIM)
        elif ov.suspicious_voxel(file_v):
            self.note.setText(f"The file says {'0' if not file_v else f'{file_v:g} Å'} per voxel, which is probably wrong. "
                              "Type the real voxel size above, or the scale bar will be wrong.")
            self.note.setStyleSheet(f"color:{message_color('err')}")
        else:
            self.note.setText(f"{val:.3g} nm per voxel, from the file."); self.note.setStyleSheet(DIM)

    def changed(self, v):
        w = self.win
        if w.spec is not None:
            w.spec.setdefault("data", {})["voxel_size_nm"] = float(v) if v > 0 else None
            self.sync(); w.overlay_controls.sync(); w.schedule()

    def reset(self):
        if self.win.spec is not None:
            self.win.spec.setdefault("data", {})["voxel_size_nm"] = None; self.sync(); self.win.schedule()
