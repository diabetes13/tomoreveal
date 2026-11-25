"""Look tab: lighting (preset, direction, amounts), shadows, anti-aliasing; and the surface style of a structure."""
import math

from PySide6 import QtCore as QtC, QtGui as QtG, QtWidgets as QtW

from .gui_numbers import NumberEdit
from . import look as lk
from .gui_fold import Fold, Section
from .gui_theme import DIM


def _quiet(w, fn):
    w.blockSignals(True)
    try:
        fn()
    finally:
        w.blockSignals(False)


class LightBall(QtW.QWidget):
    """A small shaded ball lit from the chosen direction; dragging on it sets the direction."""
    moved = QtC.Signal(float, float)  # azimuth, elevation

    def __init__(self):
        super().__init__(); self.setFixedSize(84, 84); self.az, self.el = -45.0, 50.0
        self.setToolTip("Drag to choose where the light comes from")

    def set_dir(self, az, el):
        self.az, self.el = float(az), float(el); self.update()

    def paintEvent(self, e):
        p = QtG.QPainter(self); p.setRenderHint(QtG.QPainter.Antialiasing)
        c = QtC.QPointF(self.width() / 2, self.height() / 2); r = min(self.width(), self.height()) / 2 - 4
        a, el = math.radians(self.az), math.radians(self.el)
        hx, hy = c.x() + r * 0.8 * math.cos(el) * math.cos(a), c.y() - r * 0.8 * math.cos(el) * math.sin(a)
        g = QtG.QRadialGradient(QtC.QPointF(hx, hy), r * 1.6)
        g.setColorAt(0, QtG.QColor(250, 250, 250)); g.setColorAt(0.5, QtG.QColor(140, 140, 150)); g.setColorAt(1, QtG.QColor(25, 25, 30))
        p.setBrush(g); p.setPen(QtG.QPen(self.palette().mid().color(), 1)); p.drawEllipse(c, r, r)
        p.setBrush(QtG.QColor(255, 210, 60)); p.setPen(QtC.Qt.NoPen); p.drawEllipse(QtC.QPointF(hx, hy), 3.5, 3.5)

    def _set_from(self, pos):
        c = QtC.QPointF(self.width() / 2, self.height() / 2); r = min(self.width(), self.height()) / 2 - 4
        dx, dy = (pos.x() - c.x()) / (r * 0.8), -(pos.y() - c.y()) / (r * 0.8)
        d = min(1.0, math.hypot(dx, dy))
        self.az = math.degrees(math.atan2(dy, dx)); self.el = math.degrees(math.acos(d)); self.update()
        self.moved.emit(round(self.az), round(self.el))

    def mousePressEvent(self, e):
        self._set_from(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & QtC.Qt.LeftButton:
            self._set_from(e.position())


def _slider(lo, hi, fmt, fn):
    w = QtW.QWidget(); h = QtW.QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
    s = QtW.QSlider(QtC.Qt.Horizontal); s.setRange(lo, hi); lab = NumberEdit(s, fmt, 44)  # type a value (3.6)
    s.valueChanged.connect(lambda v: (lab.show_value(v), fn(v))); h.addWidget(s, 1); h.addWidget(lab)
    w.s, w.lab, w.fmt = s, lab, fmt
    return w


def _setv(w, v):
    _quiet(w.s, lambda: w.s.setValue(int(round(v)))); w.lab.show_value(v)


class LookControls(QtC.QObject):
    """Groups 'Lighting' and 'Shadows' in the Look tab, and 'Smooth edges' for the preview."""

    def __init__(self, win, layout):
        super().__init__(win); self.win = win
        g = Section("Lighting", "lighting", False); self.light_group = g; layout.addWidget(g); f = QtW.QFormLayout(g.body)
        self.preset = QtW.QComboBox()
        for t, d in lk.PRESETS:
            self.preset.addItem(t, d)
        self.preset_tip = ("Default: as in earlier versions. Simple: one light. Soft: soft shadows in creases (ambient "
                           "occlusion, for the whole picture). Full: soft plus slice shadows. Flat: colours only, no shading.")
        self.preset.setToolTip(self.preset_tip)
        self.preset.activated.connect(lambda j: self.set_preset(self.preset.itemData(j)))
        f.addRow("Preset", self.preset)
        row = QtW.QWidget(); h = QtW.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        col = QtW.QVBoxLayout(); h.addLayout(col, 1)
        self.az = _slider(-180, 180, lambda v: f"{v}°", lambda v: self.set_light(azimuth=float(v)))
        self.el = _slider(0, 90, lambda v: f"{v}°", lambda v: self.set_light(elevation=float(v)))
        for text, w in (("From the side", self.az), ("Height", self.el)):
            r = QtW.QHBoxLayout(); lab = QtW.QLabel(text); lab.setFixedWidth(84); r.addWidget(lab); r.addWidget(w, 1); col.addLayout(r)
        self.follow = QtW.QCheckBox("Light moves with the camera")
        self.follow.setToolTip("On: the light keeps its direction as seen from the viewer. Off: the light is fixed in the "
                               "scene, so highlights and shadows move when the camera turns.")
        self.follow.toggled.connect(lambda v: self.set_light(follow_camera=bool(v))); col.addWidget(self.follow)
        self.ball = LightBall(); self.ball.moved.connect(self._ball); h.addWidget(self.ball)
        f.addRow("Direction", row)
        self.amounts = {}
        for key, text, tip in (("intensity", "Brightness", "Strength of the main light"),
                               ("fill", "Fill light", "A second, weaker light from the other side"),
                               ("ambient", "Ambient", "Light from everywhere: lifts the dark sides"),
                               ("specular", "Shininess", "Bright highlights on the surfaces")):
            w = _slider(0, 200 if key == "intensity" else 100, lambda v: f"{v} %", lambda v, key=key: self.set_light(**{key: v / 100}))
            w.setToolTip(tip); f.addRow(text, w); self.amounts[key] = w
        self.ssao = _slider(1, 20, lambda v: f"{v}", lambda v: self.set_light(ssao_radius=v / 100))
        self.ssao.setToolTip("How far the soft shadows in creases reach (Soft and Full presets)"); f.addRow("Soft shadow size", self.ssao)
        self.light_note = QtW.QLabel(""); self.light_note.setStyleSheet(DIM); self.light_note.setWordWrap(True); f.addRow(self.light_note)
        # shadows
        g = Section("Shadows", "shadows", False); self.shadow_group = g; layout.addWidget(g); f = QtW.QFormLayout(g.body)
        self.slice_sh = QtW.QCheckBox("Structures cast shadows on the tomogram slice")
        self.slice_sh.setToolTip("Soft shadows on the slice, as if lit from the direction above")
        self.slice_sh.toggled.connect(lambda v: self.set_shadow("slice", on=bool(v))); f.addRow(self.slice_sh)
        self.sh_strength = _slider(0, 100, lambda v: f"{v} %", lambda v: self.set_shadow("slice", strength=v / 100))
        self.sh_soft = _slider(0, 20, lambda v: f"{v}", lambda v: self.set_shadow("slice", softness=float(v)))
        f.addRow("Strength", self.sh_strength); f.addRow("Softness", self.sh_soft)
        self.real_sh = QtW.QCheckBox("Shadows between structures (needs a graphics card; experimental)")
        self.real_sh.toggled.connect(lambda v: self.set_shadow("real", on=bool(v))); f.addRow(self.real_sh)
        self.real_strength = _slider(0, 100, lambda v: f"{v} %", lambda v: self.set_shadow("real", strength=v / 100))
        f.addRow("Strength", self.real_strength)
        # vignette
        g = Section("Vignette (darker edges)", "vignette", False); self.vig_group = g; layout.addWidget(g); f = QtW.QFormLayout(g.body)
        self.vig_when = QtW.QComboBox()
        for t, d in lk.VIGNETTE_WHEN:
            self.vig_when.addItem(t, d)
        self.vig_when.setToolTip("Default for every close-up (each close-up can choose on its own), or for the whole movie")
        self.vig_when.activated.connect(lambda j: self.set_vig(when=self.vig_when.itemData(j))); f.addRow("Use", self.vig_when)
        self.vig_strength = _slider(0, 100, lambda v: f"{v} %", lambda v: self.set_vig(strength=v / 100))
        self.vig_size = _slider(20, 90, lambda v: f"{v} %", lambda v: self.set_vig(size=v / 100))
        self.vig_size.setToolTip("Size of the clear centre")
        self.vig_soft = _slider(5, 100, lambda v: f"{v} %", lambda v: self.set_vig(softness=v / 100))
        f.addRow("Strength", self.vig_strength); f.addRow("Clear centre", self.vig_size); f.addRow("Softness", self.vig_soft)
        row = QtW.QWidget(); h = QtW.QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        self.vig_col = QtW.QComboBox(); self.vig_col.addItem("black", "black"); self.vig_col.addItem("background colour", "background")
        self.vig_col.activated.connect(lambda j: self.set_vig(colour=self.vig_col.itemData(j)))
        self.vig_shape = QtW.QComboBox(); self.vig_shape.addItem("round", "round"); self.vig_shape.addItem("frame shaped", "frame")
        self.vig_shape.activated.connect(lambda j: self.set_vig(shape=self.vig_shape.itemData(j)))
        h.addWidget(self.vig_col); h.addWidget(self.vig_shape); h.addStretch(1); f.addRow("Colour and shape", row)
        self.vig_blur = QtW.QCheckBox("Blur the background in close-ups")
        self.vig_blur.setToolTip("The objects in focus stay sharp; things in front of them and behind them (the tomogram, other "
                                 "structures) are blurred, more the further away they are")
        self.vig_blur.toggled.connect(lambda v: self.set_vig(blur=dict(lk.look_settings(self.win.spec)["vignette"]["blur"], on=bool(v))))
        f.addRow(self.vig_blur)
        self.blur_amount = _slider(0, 100, lambda v: f"{v} %",
                                   lambda v: self.set_vig(blur=dict(lk.look_settings(self.win.spec)["vignette"]["blur"], strength=v / 100)))
        self.blur_amount.s.setToolTip("How strongly the background is blurred in close-ups"); f.addRow("Blur amount", self.blur_amount)
        # anti-aliasing in the preview
        g = Section("Smooth edges (anti-aliasing)", "smooth_edges", False); layout.addWidget(g); f = QtW.QFormLayout(g.body)
        self.aa_prev = QtW.QComboBox()
        for t, d in lk.AA_PREVIEW:
            self.aa_prev.addItem(t, d)
        self.aa_prev.setToolTip("Off is fastest. Fast smooths the edges a little. The movie has its own choice in Make the movie.")
        self.aa_prev.activated.connect(lambda j: self.set_aa("preview", self.aa_prev.itemData(j))); f.addRow("Preview", self.aa_prev)

    # ---- changes ----
    def _look(self):
        return lk.ensure(self.win.spec) if self.win.spec is not None else None

    def set_preset(self, v):
        L = self._look()
        if L is not None:
            L["lighting"]["preset"] = v
            if v == "full":
                L["shadows"]["slice"]["on"] = True
            self.sync(); self.win.schedule()

    def set_light(self, **kw):
        L = self._look()
        if L is not None:
            L["lighting"].update(kw)
            if "azimuth" in kw or "elevation" in kw:
                self.ball.set_dir(L["lighting"]["azimuth"], L["lighting"]["elevation"])
            self.win.schedule()

    def _ball(self, az, el):
        L = self._look()
        if L is not None:
            L["lighting"].update(azimuth=float(az), elevation=float(el)); _setv(self.az, az); _setv(self.el, el); self.win.schedule()

    def set_shadow(self, which, **kw):
        L = self._look()
        if L is not None:
            L["shadows"][which].update(kw); self.win.schedule()

    def set_vig(self, **kw):
        L = self._look()
        if L is not None:
            L["vignette"].update(kw); self.win.schedule()

    def set_aa(self, which, v):
        L = self._look()
        if L is not None:
            L["aa"][which] = v; self.win.schedule()

    # ---- showing ----
    def sync(self):
        if self.win.spec is None:
            return
        L = lk.look_settings(self.win.spec); li = L["lighting"]
        self.preset.setCurrentIndex(max(0, self.preset.findData(li["preset"])))
        _setv(self.az, li["azimuth"]); _setv(self.el, li["elevation"]); self.ball.set_dir(li["azimuth"], li["elevation"])
        _quiet(self.follow, lambda: self.follow.setChecked(bool(li["follow_camera"])))
        for k, w in self.amounts.items():
            _setv(w, 100 * float(li[k]))
        _setv(self.ssao, 100 * float(li["ssao_radius"]))
        default = li["preset"] == "default"
        for w in (self.az, self.el, self.follow, self.ball, *self.amounts.values()):
            w.setEnabled(not default and li["preset"] != "flat")
        self.ssao.setEnabled(li["preset"] in ("soft", "full"))
        self.light_note.setVisible(False)  # (3.5: shown as the preset's tooltip)
        self.preset.setToolTip((self.preset_tip + "\n\n" + ("Default lighting is the one of earlier versions; choose another preset to change the "
                                "direction and amounts. The direction also sets where slice shadows fall." if default else
                                "Soft shadows in creases (ambient occlusion) work on the whole picture, not per structure."
                                if li["preset"] in ("soft", "full") else "")).strip())
        sh = L["shadows"]
        _quiet(self.slice_sh, lambda: self.slice_sh.setChecked(bool(sh["slice"]["on"])))
        _setv(self.sh_strength, 100 * float(sh["slice"]["strength"])); _setv(self.sh_soft, float(sh["slice"]["softness"]))
        has_t = bool((self.win.info or {}).get("has_tomogram"))
        for w in (self.slice_sh, self.sh_strength, self.sh_soft):
            w.setEnabled(has_t)
        self.slice_sh.setToolTip("Soft shadows on the slice, as if lit from the direction above" if has_t else "Needs a tomogram")
        ok, why = self.win.st.real_shadows_ok, self.win.st.real_shadows_note
        _quiet(self.real_sh, lambda: self.real_sh.setChecked(bool(sh["real"]["on"]) and ok))
        self.real_sh.setEnabled(ok and not default); self.real_strength.setEnabled(ok and not default)
        self.real_sh.setToolTip("Structures shadow each other. Needs a lighting preset other than Default." if ok else why)
        _setv(self.real_strength, 100 * float(sh["real"]["strength"]))
        self.aa_prev.setCurrentIndex(max(0, self.aa_prev.findData(L["aa"]["preview"])))
        v = L["vignette"]
        self.vig_when.setCurrentIndex(max(0, self.vig_when.findData(v["when"])))
        _setv(self.vig_strength, 100 * float(v["strength"])); _setv(self.vig_size, 100 * float(v["size"])); _setv(self.vig_soft, 100 * float(v["softness"]))
        self.vig_col.setCurrentIndex(1 if v.get("colour") == "background" else 0); self.vig_shape.setCurrentIndex(1 if v.get("shape") == "frame" else 0)
        _quiet(self.vig_blur, lambda: self.vig_blur.setChecked(bool(v["blur"].get("on"))))
        _setv(self.blur_amount, 100 * float(v["blur"].get("strength", 0.5)))


def style_rows(win, form, key):
    """Style settings of one structure in its settings in the Structures tab."""
    st = lk.style_of(win.spec, key)
    kind = QtW.QComboBox()
    for t, d in lk.STYLES:
        kind.addItem(t, d)
    kind.setCurrentIndex(max(0, kind.findData(st["kind"])))
    kind.setToolTip("Normal: as before. Cartoon: flat colour with dark outlines. Dots: the surface drawn as dots. "
                    "Glossy: shiny plastic, metal or glass.")
    opts = QtW.QWidget(); h = QtW.QHBoxLayout(opts); h.setContentsMargins(0, 0, 0, 0)

    def put(**kw):
        sc = win.spec["classes"].setdefault(key, {}); cur = dict(sc.get("style") or {}); cur.update(kw); sc["style"] = cur
        win.schedule()

    def fill():
        while h.count():
            w = h.takeAt(0).widget()
            if w is not None:
                w.setParent(None); w.deleteLater()
        s2 = lk.style_of(win.spec, key)
        if s2["kind"] == "cartoon":
            ow = QtW.QSpinBox(); ow.setRange(1, 6); ow.setValue(int(s2["outline_width"])); ow.setPrefix("outline "); ow.setSuffix(" px")
            ow.valueChanged.connect(lambda v: put(outline_width=int(v)))
            oc = QtW.QComboBox(); oc.addItem("black", "black"); oc.addItem("darker colour", "darker")
            oc.setCurrentIndex(1 if s2.get("outline_color") == "darker" else 0); oc.activated.connect(lambda j: put(outline_color=oc.itemData(j)))
            h.addWidget(ow); h.addWidget(oc)
        elif s2["kind"] == "dots":
            ps = QtW.QSpinBox(); ps.setRange(1, 8); ps.setValue(int(s2["point_size"])); ps.setPrefix("dot size "); ps.setSuffix(" px")
            ps.valueChanged.connect(lambda v: put(point_size=int(v))); h.addWidget(ps)
        elif s2["kind"] == "glossy":
            gk = QtW.QComboBox()
            for t, d in (("shiny", "shiny"), ("metallic", "metallic"), ("glass", "glass")):
                gk.addItem(t, d)
            gk.setCurrentIndex(max(0, gk.findData(s2.get("gloss", "shiny")))); gk.activated.connect(lambda j: put(gloss=gk.itemData(j)))
            h.addWidget(gk)
        allb = QtW.QPushButton("Apply to all"); allb.setToolTip("Give every structure this style")
        allb.clicked.connect(lambda: apply_all()); h.addWidget(allb); h.addStretch(1)

    def apply_all():
        s2 = lk.style_of(win.spec, key)
        for c in (win.info or {}).get("structures", []):
            win.spec["classes"].setdefault(c["key"], {})["style"] = dict(s2)
        win.build_controls(); win.schedule()
    kind.activated.connect(lambda j: (put(kind=kind.itemData(j)), fill()))
    fill()
    row = QtW.QWidget(); v = QtW.QHBoxLayout(row); v.setContentsMargins(0, 0, 0, 0); v.addWidget(kind); v.addWidget(opts, 1)
    form.addRow("Style", row)


class Look35Controls(QtC.QObject):
    """The Look group (3.5): one choice sets background, soft shadows, outlines, see-through membranes and depth fade;
    each can then be changed (the choice then shows Custom)."""

    def __init__(self, win, layout):
        super().__init__(win); self.win = win
        g = Section("Look", "look", True); self.group = g; layout.addWidget(g); f = QtW.QFormLayout(g.body)

        def combo(items, key, tip):
            cb = QtW.QComboBox(); cb.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon); cb.setMinimumContentsLength(8)
            for t, d in items:
                cb.addItem(t, d)
            cb.setToolTip(tip); cb.activated.connect(lambda j: self.set(**{key: cb.itemData(j)}))
            return cb
        self.preset = QtW.QComboBox(); self.preset.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.preset.setMinimumContentsLength(14)
        for t, d in lk.LOOKS + (("Custom", "custom"),):
            self.preset.addItem(t, d)
        self.preset_tip = "Sets background, shadows, outlines, membranes and depth fade at once; each can then be changed. " + \
            " ".join(f"{t}: {lk.LOOK_NOTES[d]}" for t, d in lk.LOOKS) + " The Presets tab shows them as pictures."
        self.preset.setToolTip(self.preset_tip)
        self.preset.activated.connect(lambda j: self.choose(self.preset.itemData(j)))
        f.addRow("Look", self.preset)
        self.bg = combo(tuple((t, d) for t, d in (("black", "black"), ("white", "white"), ("light grey", "lightgrey"), ("dark grey", "darkgrey"),
                                                   ("gradient (light grey to white)", "gradient"), ("the tomogram slice", "tomogram"))),
                        "background", "Behind the structures, with any look (for example Soft on black)")
        f.addRow("Background", self.bg)
        self.membranes = QtW.QComboBox(); self.membranes.setSizeAdjustPolicy(QtW.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.membranes.setMinimumContentsLength(8)
        for t, d in lk.MEMBRANE_LOOKS:
            self.membranes.addItem(t, d)
        self.membranes.setToolTip("Structures ticked 'Treat as membrane' (Structures tab): in their own colour, pale and see-through "
                                  "so what is behind or inside them shows, or solid cream like a ceramic model")
        self.membranes.activated.connect(lambda j: self.set_membranes(self.membranes.itemData(j)))
        self.glass_op = _slider(5, 100, lambda v: f"{v} %", lambda v: self.set(glass_opacity=v / 100))
        self.glass_op.s.setToolTip("How solid the see-through membranes look")
        self.mem_col = QtW.QPushButton(); self.mem_col.setFixedSize(30, 22)
        self.mem_col.setToolTip("Choose the colour of the membranes from a colour wheel (solid or see-through)")
        self.mem_col.clicked.connect(self.pick_membrane_colour)
        f.addRow("Membranes", _row(self.membranes, self.mem_col, self.glass_op))
        self.glass_line = combo(lk.GLASS_OUTLINES, "glass_outline", "Outlines of see-through membranes: every edge, only their outer "
                                "edge (no lines across them where they fold or where something lies behind), or none")
        f.addRow("See-through edges", self.glass_line)
        self.smooth_mem = QtW.QCheckBox("smoother membrane surfaces")
        self.smooth_mem.setToolTip("Takes away the bumps and ridges a segmentation leaves on membranes (every structure ticked "
                                   "'Treat as membrane'). Each structure's own setting is 'Smoother surface' in the Structures tab")
        self.smooth_mem.toggled.connect(self.set_smooth_membranes)
        f.addRow("", self.smooth_mem)
        more = Fold("More settings", "look_more", False, "Shadows, outlines, depth fade, surface and colours")
        f.addRow(more); f = more.form
        self.shadow = combo(lk.SHADOW_LEVELS, "shadow", "Soft shadows in creases and between neighbours. They do not change with "
                            "the camera, so they never flicker. Clip boxes and cuts do not change them.")
        self.reach = combo(lk.REACHES, "reach", "How far the soft shadows reach")
        f.addRow("Soft shadows", _row(self.shadow, QtW.QLabel("reach"), self.reach))
        self.outline = combo(lk.OUTLINES, "outline", "Dark outlines around every structure (the same width in the preview and the movie)")
        self.ocol = combo(lk.OUTLINE_COLOURS, "outline_colour", "Colour of the outlines. Glowing: each structure's own colour "
                                                                  "with a soft halo (best on black)")
        f.addRow("Outlines", _row(self.outline, self.ocol))
        self.between = QtW.QCheckBox("also between touching structures")
        self.between.setToolTip("Outlines where two structures touch at the same depth (draws each frame twice)")
        self.between.toggled.connect(lambda v: self.set(between=bool(v))); f.addRow("", self.between)
        self.fade = combo(lk.FADES, "depth_fade", "Far things fade towards the background: gives depth")
        f.addRow("Depth fade", self.fade)
        self.material = combo(lk.MATERIALS, "material", "Ceramic: bright and matte with a broad soft sheen, like a porcelain "
                                                        "model. X-ray: every structure see-through, so what is behind and inside shows")
        self.pastel = QtW.QCheckBox("paler colours")
        self.pastel.setToolTip("Colours mixed a little towards white, as on a ceramic model")
        self.pastel.toggled.connect(lambda v: self.set(pastel=bool(v)))
        f.addRow("Surface", _row(self.material, self.pastel))
        self.richer = QtW.QCheckBox("Richer colours on white backgrounds")
        self.richer.setToolTip("Colours from Dragonfly can look washed out on white: a little more saturated and darker")
        self.richer.toggled.connect(lambda v: self.set(richer=bool(v)))
        self.matte = QtW.QCheckBox("Matte lighting")
        self.matte.setToolTip("A light from the viewer and a soft fill light from the upper left, with little shine "
                              "(with the Default lighting preset)")
        self.matte.toggled.connect(lambda v: self.set(lighting=bool(v)))
        f.addRow(_row(self.richer, self.matte))

    def set_membranes(self, how):
        self.set(glass=how == "glass", membrane_colour=lk.solid_colour(self.win.spec) if how == "cream" else None)

    def pick_membrane_colour(self):
        w = self.win
        if w.spec is None:
            return
        L = lk.style35(w.spec); glass = bool(L["glass"])
        cur = L["glass_colour"] if glass else (L["membrane_colour"] or lk.solid_colour(w.spec))
        c = QtW.QColorDialog.getColor(QtG.QColor(cur), w, "Colour of the membranes")
        if not c.isValid():
            return
        if glass:
            self.set(glass_colour=c.name())
        else:
            self.set(membrane_colour=c.name(), solid_colour=c.name())

    def choose(self, name):
        w = self.win
        if w.spec is None or name not in lk.LOOK_PRESETS:
            self.sync(); return
        lk.apply_look(w.spec, name); w.build_controls(); w.schedule()

    def set(self, **kw):
        w = self.win
        if w.spec is None:
            return
        lk.set_look(w.spec, **kw); self.sync(); w.schedule()
        if "background" in kw and hasattr(w, "bg_cb"):  # the same choice in "Background and contrast"
            _quiet(w.bg_cb, lambda: w.bg_cb.setCurrentIndex(max(0, w.bg_cb.findData(kw["background"]))))

    def _membrane_keys(self):
        w = self.win
        out = []
        for c in (w.info or {}).get("structures", []):
            name = (w.spec["classes"].get(c["key"]) or {}).get("name", c.get("name", ""))
            if lk.is_membrane(w.spec, c["key"], name):
                out.append(c["key"])
        return out

    def set_smooth_membranes(self, on):
        """Smoother surfaces ('more') for every membrane, or back to none; the surfaces are rebuilt a moment later."""
        w = self.win
        if w.spec is None:
            return
        for key in self._membrane_keys():
            sc = w.spec["classes"].setdefault(key, {})
            if bool(on) != bool(sc.get("soften")):
                w._param_changed(key, "soften", 2 if on else 0)

    def sync(self):
        w = self.win
        if w.spec is None:
            return
        L = lk.style35(w.spec)
        mem = self._membrane_keys()
        _quiet(self.smooth_mem, lambda: self.smooth_mem.setChecked(bool(mem) and all(
            (w.spec["classes"].get(k) or {}).get("soften") for k in mem)))
        self.smooth_mem.setEnabled(bool(mem))
        for cb, v in ((self.preset, L["preset"]), (self.shadow, L["shadow"]), (self.reach, L["reach"]), (self.outline, L["outline"]),
                      (self.ocol, L["outline_colour"]), (self.fade, L["depth_fade"]), (self.bg, L["background"]),
                      (self.membranes, lk.membrane_look(L)), (self.glass_line, L["glass_outline"]), (self.material, L["material"])):
            _quiet(cb, lambda cb=cb, v=v: cb.setCurrentIndex(max(0, cb.findData(v))))
        for cb, v in ((self.between, L["between"]), (self.pastel, L["pastel"]), (self.richer, L["richer"]), (self.matte, L["lighting"])):
            _quiet(cb, lambda cb=cb, v=v: cb.setChecked(bool(v)))
        _setv(self.glass_op, 100 * float(L["glass_opacity"])); self.glass_op.setVisible(bool(L["glass"]))
        mode = lk.membrane_look(L); self.mem_col.setVisible(mode != "own")
        col = L["glass_colour"] if mode == "glass" else (L["membrane_colour"] or lk.solid_colour(w.spec))
        self.mem_col.setStyleSheet(f"background:{col}; border:1px solid palette(mid); border-radius:3px")
        self.glass_line.setEnabled(bool(L["glass"]))
        self.reach.setEnabled(L["shadow"] != "none"); self.ocol.setEnabled(L["outline"] != "off"); self.between.setEnabled(L["outline"] != "off")


def _row(*ws):
    w = QtW.QWidget(); h = QtW.QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
    for x in ws:
        h.addWidget(x)
    h.addStretch(1)
    return w


def membrane_row(win, form, key, name):
    """'Treat as membrane' of one structure (Structures tab): see-through with the Glass looks."""
    cb = QtW.QCheckBox("Treat as membrane"); cb.setChecked(lk.is_membrane(win.spec, key, name))
    cb.setToolTip("With 'See-through membranes' (Look tab), this structure is drawn pale and see-through")

    def put(on):
        win.spec["classes"].setdefault(key, {})["membrane"] = bool(on); win.schedule()
        if hasattr(win, "look35"):
            win.look35.sync()  # "smoother membrane surfaces" counts the membranes
    cb.toggled.connect(put)
    c = win.spec["classes"].get(key, {})
    sh = QtW.QCheckBox("soft shadows"); sh.setChecked(not c.get("no_shadow"))
    sh.setToolTip("Soft shadows of the Look on this structure (untick to draw it without them, in the whole movie)")
    ol = QtW.QCheckBox("outline"); ol.setChecked(not c.get("no_outline"))
    ol.setToolTip("Outlines of the Look around this structure (untick to draw it without one, in the whole movie)")

    def flag(name, on):
        sc = win.spec["classes"].setdefault(key, {})
        if on:
            sc.pop(name, None)
        else:
            sc[name] = True
        win.schedule()
    sh.toggled.connect(lambda on: flag("no_shadow", on)); ol.toggled.connect(lambda on: flag("no_outline", on))
    form.addRow("", _row(cb, sh, ol))
